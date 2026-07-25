"""Offline unit tests for core/microstructure_store.py + the harvester CLI.

No network: only the pure feature math, the sqlite schema/upsert behavior,
and the quarantine invariants (no order-path imports; no writes outside the
warehouse) are tested. The live fetches are exercised by running the script.
"""

import sqlite3
from pathlib import Path

import pytest

from core.microstructure_store import (
    LARGE_TRADE_NOTIONAL_USD,
    compute_book_features,
    compute_trade_features,
    ensure_schema,
    insert_row,
)

_REPO = Path(__file__).resolve().parents[1]
_MODULE_SRC = _REPO / "core" / "microstructure_store.py"
_CLI_SRC = _REPO / "scripts" / "harvest_microstructure.py"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _trade(side, price, amount):
    return {"side": side, "price": price, "amount": amount}


# ── quarantine invariants ────────────────────────────────────────────────────
def test_no_order_path_imports():
    """Gate-guard: the feature store must never import the order path."""
    for src in (_MODULE_SRC, _CLI_SRC):
        import_lines = [
            ln
            for ln in src.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        for forbidden in (
            "order_manager",
            "risk_manager",
            "live_gate",
            "bot_engine",
            "exchanges",
            "sim_execution",
        ):
            assert forbidden not in joined, f"{src.name} must not import {forbidden}"


def test_no_writes_outside_warehouse():
    """The store touches ONLY the warehouse — never positions/risk files."""
    for src in (_MODULE_SRC, _CLI_SRC):
        text = src.read_text(encoding="utf-8")
        for forbidden in ("positions.json", "risk_state", "risk_incident"):
            assert forbidden not in text, f"{src.name} must not reference {forbidden}"


# ── schema ───────────────────────────────────────────────────────────────────
def test_ensure_schema_idempotent(conn):
    ensure_schema(conn)
    ensure_schema(conn)  # second call must not raise
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(microstructure_features)")}
    assert {
        "symbol",
        "venue",
        "bar_ts",
        "signed_volume",
        "buy_sell_imbalance",
        "trade_count",
        "large_trade_count",
        "notional_total",
        "book_imbalance",
        "spread_bps",
        "depth_top20_bid_usd",
        "depth_top20_ask_usd",
        "window_trades_n",
        "asof_ts",
        "source",
    } <= cols


def test_insert_row_pk_upsert(conn):
    ensure_schema(conn)
    base = {"symbol": "BTC/USDT:USDT", "venue": "bybit", "bar_ts": 1_000_000}
    insert_row(conn, {**base, "signed_volume": 1.0})
    insert_row(conn, {**base, "signed_volume": 2.5})
    rows = conn.execute("SELECT * FROM microstructure_features").fetchall()
    assert len(rows) == 1
    assert rows[0]["signed_volume"] == 2.5


def test_insert_row_missing_keys_are_null(conn):
    ensure_schema(conn)
    insert_row(conn, {"symbol": "ETH/USDT:USDT", "venue": "bybit", "bar_ts": 5})
    row = conn.execute("SELECT * FROM microstructure_features").fetchone()
    assert row["book_imbalance"] is None
    assert row["trade_count"] is None


# ── trade features ───────────────────────────────────────────────────────────
def test_signed_volume_and_imbalance():
    trades = [
        _trade("buy", 100.0, 3.0),
        _trade("buy", 101.0, 1.0),
        _trade("sell", 100.0, 1.0),
    ]
    f = compute_trade_features(trades)
    assert f["signed_volume"] == pytest.approx(3.0)  # 4 buy - 1 sell
    assert f["buy_sell_imbalance"] == pytest.approx(3.0 / 5.0)
    assert f["trade_count"] == 3
    assert f["window_trades_n"] == 3
    assert f["notional_total"] == pytest.approx(100 * 3 + 101 * 1 + 100 * 1)


def test_all_sells_negative_imbalance():
    f = compute_trade_features([_trade("sell", 50.0, 2.0)])
    assert f["signed_volume"] == pytest.approx(-2.0)
    assert f["buy_sell_imbalance"] == pytest.approx(-1.0)


def test_large_trade_threshold():
    small = _trade("buy", 100.0, LARGE_TRADE_NOTIONAL_USD / 100.0 - 0.01)
    large = _trade("sell", 100.0, LARGE_TRADE_NOTIONAL_USD / 100.0)
    f = compute_trade_features([small, large])
    assert f["large_trade_count"] == 1
    assert f["trade_count"] == 2


def test_unusable_trades_excluded_but_counted_in_window():
    trades = [
        _trade("buy", 100.0, 1.0),
        _trade(None, 100.0, 1.0),  # no aggressor side
        {"side": "buy"},  # no price/amount
        _trade("buy", 0.0, 1.0),  # nonpositive price
    ]
    f = compute_trade_features(trades)
    assert f["trade_count"] == 1
    assert f["window_trades_n"] == 4  # raw sample size disclosed


def test_empty_trades_none():
    assert compute_trade_features([]) is None
    assert compute_trade_features(None) is None
    assert compute_trade_features([{"side": "x"}]) is None  # nothing usable


# ── book features ────────────────────────────────────────────────────────────
def test_book_imbalance_and_spread():
    ob = {
        "bids": [[100.0, 2.0], [99.0, 1.0]],
        "asks": [[101.0, 1.0]],
    }
    f = compute_book_features(ob)
    bid_usd = 100.0 * 2.0 + 99.0 * 1.0  # 299
    ask_usd = 101.0 * 1.0
    assert f["depth_top20_bid_usd"] == pytest.approx(bid_usd)
    assert f["depth_top20_ask_usd"] == pytest.approx(ask_usd)
    assert f["book_imbalance"] == pytest.approx(bid_usd / (bid_usd + ask_usd))
    mid = (100.0 + 101.0) / 2.0
    assert f["spread_bps"] == pytest.approx((101.0 - 100.0) / mid * 1e4)


def test_book_top20_only():
    bids = [[100.0 - i, 1.0] for i in range(30)]
    asks = [[101.0 + i, 1.0] for i in range(30)]
    f = compute_book_features({"bids": bids, "asks": asks})
    assert f["depth_top20_bid_usd"] == pytest.approx(sum(p for p, _ in bids[:20]))
    assert f["depth_top20_ask_usd"] == pytest.approx(sum(p for p, _ in asks[:20]))


def test_empty_book_none():
    assert compute_book_features(None) is None
    assert compute_book_features({}) is None
    assert compute_book_features({"bids": [], "asks": [[1.0, 1.0]]}) is None
    assert compute_book_features({"bids": [[1.0, 1.0]], "asks": []}) is None


# ── CLI bar stamping ─────────────────────────────────────────────────────────
def test_closed_bar_open_ts():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_harvest_microstructure", _CLI_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bar = 4 * 3600
    # 00:05 UTC -> the bar that OPENED at 20:00 the prior day (just closed)
    now = 10 * 86400 + 300
    assert mod.closed_bar_open_ts(now) == 10 * 86400 - bar
    # exactly on the boundary -> previous bar too
    assert mod.closed_bar_open_ts(10 * 86400) == 10 * 86400 - bar
