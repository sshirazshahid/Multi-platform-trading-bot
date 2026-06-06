"""TDD for the TP booking-completeness fix (audit 2026-06-04,
reports/tp_booking_codepath_audit_2026-06-04.md).

Two defects in how closed trades are booked to the warehouse (the learning
substrate):

  B1 — `r_multiple` was computed against the MUTATED `stop_loss` (trailing /
       breakeven), not the entry-time stop, so a breakeven-moved winner booked
       NULL (denom == 0) or an inflated R. Fix: capture an IMMUTABLE
       `entry_stop_loss` at construction and compute R against it; persist it
       on the trade row as `trades.entry_stop_px` for later re-derivation.

  B3 — partial-TP trades booked only the RUNNER leg's `realized_pnl`, so the
       taken fraction was invisible to the warehouse (it only hit the paper
       wallet). Fix: accumulate the partial fill on the Position and book the
       WHOLE trade (partial + runner) with a size-weighted blended exit for R.

These are BOOKING-accuracy fixes only. They do not change PnL economics, the
edge (settled: flat alpha), or the risk rails (which intentionally stay on the
conservative runner-leg accounting).
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from core.position_tracker import Position

ROOT = Path(__file__).resolve().parents[1]


def _mk(side="buy", entry=100.0, stop=98.0, tp=104.0, size=1.0):
    return Position(
        id="t1", exchange="binance", symbol="BTC/USDT", side=side,
        market_type="futures", strategy="systematic_v3_1",
        entry_price=entry, size=size, stop_loss=stop, take_profit=tp,
        leverage=1,
    )


# ── B1: entry_stop_loss capture & immutability ───────────────────────────
def test_entry_stop_loss_captured_at_construction():
    assert _mk(stop=98.0).entry_stop_loss == 98.0


def test_entry_stop_loss_immutable_under_breakeven_move():
    p = _mk(entry=100.0, stop=98.0)
    p.stop_loss = 100.0  # breakeven move (trailing / partial-TP)
    assert p.entry_stop_loss == 98.0


def test_entry_stop_loss_survives_json_roundtrip():
    p = _mk(stop=98.0)
    p.stop_loss = 100.0
    p2 = Position(**asdict(p))
    assert p2.entry_stop_loss == 98.0
    assert p2.stop_loss == 100.0


def test_legacy_position_without_entry_stop_falls_back_to_current():
    """Old positions.json predating the field load with no entry_stop_loss
    key → best-effort baseline = the (possibly moved) current stop."""
    d = asdict(_mk(stop=98.0))
    d.pop("entry_stop_loss")
    d["stop_loss"] = 100.0
    p = Position(**d)
    assert p.entry_stop_loss == 100.0


# ── B1: r_multiple uses the entry stop, not the moved stop ────────────────
def test_r_multiple_uses_entry_stop_after_breakeven_buy():
    p = _mk(side="buy", entry=100.0, stop=98.0)  # entry risk = 2.0
    p.stop_loss = 100.0  # BE move would NULL the old R (denom == 0)
    assert p.r_multiple(104.0) == pytest.approx(2.0)


def test_r_multiple_short():
    p = _mk(side="sell", entry=100.0, stop=102.0)  # entry risk = 2.0
    assert p.r_multiple(96.0) == pytest.approx(2.0)


def test_r_multiple_loss_is_minus_one_at_entry_stop():
    p = _mk(side="buy", entry=100.0, stop=98.0)
    assert p.r_multiple(98.0) == pytest.approx(-1.0)


def test_r_multiple_none_on_garbage_exit():
    assert _mk(side="buy", entry=100.0, stop=98.0).r_multiple(0.0) is None


def test_r_multiple_falls_back_to_stop_loss_when_no_entry_stop():
    """Legacy in-flight position whose entry_stop_loss is 0 → use stop_loss."""
    p = _mk(side="buy", entry=100.0, stop=98.0)
    p.entry_stop_loss = 0.0  # simulate unset
    assert p.r_multiple(104.0) == pytest.approx(2.0)


# ── B3: partial accounting on the Position ───────────────────────────────
def test_book_partial_exit_accumulates():
    p = _mk(size=1.0)
    p.book_partial_exit(0.3, 102.0, 0.6)
    p.book_partial_exit(0.2, 103.0, 0.5)
    assert p.partial_exit_qty == pytest.approx(0.5)
    assert p.partial_exit_value == pytest.approx(0.3 * 102.0 + 0.2 * 103.0)
    assert p.realized_partial_pnl == pytest.approx(1.1)


def test_effective_exit_price_no_partial_is_final():
    assert _mk(size=1.0).effective_exit_price(101.0) == pytest.approx(101.0)


def test_effective_exit_price_blends_partial_and_runner():
    p = _mk(size=1.0)
    p.book_partial_exit(0.3, 104.0, 1.2)
    p.size = 0.7  # runner size after the partial decrement
    # blended = (0.3*104 + 0.7*102) / 1.0 = 102.6
    assert p.effective_exit_price(102.0) == pytest.approx(102.6)


def test_partial_trade_r_multiple_uses_blended_exit():
    p = _mk(side="buy", entry=100.0, stop=98.0, size=1.0)  # entry risk 2.0
    p.book_partial_exit(0.3, 104.0, 1.2)
    p.size = 0.7
    p.stop_loss = 100.0  # BE moved at partial — must NOT affect R
    eff = p.effective_exit_price(102.0)  # 102.6
    assert p.r_multiple(eff) == pytest.approx((102.6 - 100.0) / 2.0)  # 1.3


def test_total_realized_pnl_includes_partial():
    """The dollar total the warehouse should book = runner pnl + partials."""
    p = _mk(size=1.0)
    p.book_partial_exit(0.3, 104.0, 1.2)
    runner_pnl = -0.4  # runner stopped at small loss
    total = runner_pnl + p.realized_partial_pnl
    assert total == pytest.approx(0.8)  # net-positive despite runner loss


# ── B1/B3: warehouse persistence of entry_stop_px ────────────────────────
def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_tp_test_copy", ROOT / "core" / "warehouse.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_tp_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def test_trades_table_has_entry_stop_px_column(wh):
    cols = [r["name"] for r in wh.query("PRAGMA table_info(trades)")]
    assert "entry_stop_px" in cols


def test_trades_table_has_partial_realized_pnl_column(wh):
    cols = [r["name"] for r in wh.query("PRAGMA table_info(trades)")]
    assert "partial_realized_pnl" in cols


def test_record_trade_close_persists_partial_realized_pnl(wh):
    tid = wh.record_trade_open(
        exchange="binance", symbol="SOL/USDT", side="buy",
        ts_entry=3000.0, entry_px=10.0, size=1.0, leverage=1, mode="PAPER")
    wh.record_trade_close(
        trade_id=tid, ts_exit=3600.0, exit_px=10.2,
        realized_pnl=0.14, r_multiple=1.3, exit_reason="take_profit",
        partial_realized_pnl=1.2)
    row = wh.query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    # realized_pnl stays the RUNNER leg (live gate input unchanged); the
    # partial leg is recorded separately. Whole-trade $ = sum of the two.
    assert row["realized_pnl"] == pytest.approx(0.14)
    assert row["partial_realized_pnl"] == pytest.approx(1.2)


def test_record_trade_close_persists_entry_stop_px(wh):
    tid = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=1000.0, entry_px=100.0, size=1.0, leverage=1, mode="PAPER")
    wh.record_trade_close(
        trade_id=tid, ts_exit=1600.0, exit_px=104.0,
        realized_pnl=4.0, r_multiple=2.0, exit_reason="take_profit",
        entry_stop_px=98.0)
    row = wh.query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert row["entry_stop_px"] == pytest.approx(98.0)


def test_record_trade_close_without_entry_stop_px_is_backward_compatible(wh):
    """Existing callers (e.g. the reconcile path) omit entry_stop_px."""
    tid = wh.record_trade_open(
        exchange="binance", symbol="ETH/USDT", side="sell",
        ts_entry=2000.0, entry_px=50.0, size=1.0, leverage=1, mode="PAPER")
    wh.record_trade_close(
        trade_id=tid, ts_exit=2600.0, exit_px=49.0,
        realized_pnl=1.0, exit_reason="reconciled_from_exchange")
    row = wh.query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert row["status"] == "CLOSED"
    assert row["entry_stop_px"] is None


# ── Integration: real _finalize_close wiring (partial + runner → one row) ──
def test_finalize_close_books_whole_trade_with_entry_stop(tmp_path, monkeypatch):
    """End-to-end: a partial-TP trade whose stop was moved to breakeven must
    book the WHOLE trade's PnL, R against the ENTRY stop, and entry_stop_px."""
    from unittest.mock import MagicMock

    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)

    import core.warehouse as whmod
    from core.order_manager import OrderManager

    wh = whmod.Warehouse(path=tmp_path / "wh.sqlite")
    monkeypatch.setattr(whmod, "get_warehouse", lambda: wh)

    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = False

    open_time = 1000.0
    pos = Position(
        id="p1", exchange="Binance", symbol="BTC/USDT", side="buy",
        market_type="futures", strategy="systematic_v3_1",
        entry_price=100.0, size=1.0, stop_loss=98.0, take_profit=104.0,
        leverage=1, open_time=open_time)
    pos.paper_trade = False  # skip the paper-wallet branch

    tid = wh.record_trade_open(
        exchange="binance", symbol="BTC/USDT", side="buy",
        ts_entry=open_time, entry_px=100.0, size=1.0, leverage=1,
        mode="CONTROLLED_LIVE")
    assert tid > 0

    # partial TP banks 0.3 @ 104 (+1.2 net), runner = 0.7, stop moved to BE
    pos.book_partial_exit(0.3, 104.0, 1.2)
    pos.size = 0.7
    pos.stop_loss = 100.0  # breakeven — must NOT contaminate R
    pos.close(102.0, "take_profit")
    pos.close_time = open_time + 600

    om._finalize_close(pos, 102.0, "take_profit")

    row = wh.query("SELECT * FROM trades WHERE id=?", (tid,))[0]
    assert row["status"] == "CLOSED"
    assert row["entry_stop_px"] == pytest.approx(98.0)          # entry stop, not BE
    assert row["r_multiple"] == pytest.approx(1.3, abs=1e-3)     # blended 102.6 → 1.3R
    # realized_pnl stays RUNNER-only so the live expectancy gate input is
    # unchanged; the partial leg is booked in its own column. Whole-trade $
    # = realized_pnl + partial_realized_pnl.
    assert row["realized_pnl"] == pytest.approx(pos.pnl)
    assert row["partial_realized_pnl"] == pytest.approx(1.2)
    assert (row["realized_pnl"] + row["partial_realized_pnl"]
            == pytest.approx(pos.pnl + pos.realized_partial_pnl))
