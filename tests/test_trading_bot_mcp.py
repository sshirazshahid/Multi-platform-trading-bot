"""Tests for the read-only MCP warehouse reader (mcp_server.warehouse_reader).

These exercise the data-access + SQL-guard logic directly against a temp SQLite
warehouse, so they need neither the `mcp` package nor real bot state.
"""
from __future__ import annotations

import sqlite3

import pytest

from mcp_server import warehouse_reader as wr


@pytest.fixture
def db(tmp_path):
    """A minimal warehouse with a couple of trades, candidates, shadow rows."""
    path = tmp_path / "warehouse.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, ts_entry REAL, ts_exit REAL, exchange TEXT,
            symbol TEXT, market_type TEXT, side TEXT, strategy_family TEXT,
            entry_px REAL, exit_px REAL, size REAL, leverage INTEGER, fee REAL,
            slippage REAL, realized_pnl REAL, r_multiple REAL, hold_sec REAL,
            status TEXT, exit_reason TEXT
        );
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY, ts REAL, exchange TEXT, symbol TEXT,
            market_type TEXT, side TEXT, strategy_family TEXT, regime_label TEXT,
            entry_px REAL, stop_px REAL, target_px REAL, leverage INTEGER,
            size_pct REAL, confidence REAL, decision TEXT, skip_reason TEXT,
            features_json TEXT
        );
        CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY, ts INTEGER, model_version TEXT, symbol TEXT,
            side TEXT, decision TEXT, p_win REAL, sim_pnl REAL, sim_r_multiple REAL,
            proposal_id TEXT, label_status TEXT
        );
        CREATE TABLE shadow_outcomes (
            proposal_id TEXT PRIMARY KEY, exit_px REAL, exit_reason TEXT,
            gross_pnl REAL, net_pnl REAL, fees REAL, slippage REAL, funding REAL,
            mfe REAL, mae REAL, bars_held INTEGER, r_multiple REAL,
            resolved_ts INTEGER, label_status TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO trades (ts_entry, ts_exit, exchange, symbol, side, "
        "strategy_family, realized_pnl, status) VALUES (?,?,?,?,?,?,?, 'CLOSED')",
        [
            (1, 2, "bybit", "BTC/USDT:USDT", "buy", "sys", 10.0),
            (3, 4, "bybit", "BTC/USDT:USDT", "buy", "sys", -4.0),
            (5, 6, "binance", "ETH/USDT:USDT", "sell", "scalp", -2.0),
        ],
    )
    conn.executemany(
        "INSERT INTO candidates (ts, exchange, symbol, side, confidence, "
        "decision, skip_reason) VALUES (?,?,?,?,?,?,?)",
        [
            (10, "bybit", "BTC/USDT:USDT", "buy", 0.7, "TAKEN", None),
            (11, "bybit", "SOL/USDT:USDT", "buy", 0.4, "SKIP", "low_confidence"),
        ],
    )
    conn.execute(
        "INSERT INTO shadow_decisions (ts, model_version, symbol, side, "
        "decision, p_win, sim_pnl, proposal_id, label_status) VALUES "
        "(1, 'm1', 'BTC/USDT:USDT', 'buy', 'OPEN', 0.6, 3.5, 'p-1', 'RESOLVED')"
    )
    # Phase 0b: shadow_vs_live reports RESOLVED net PnL from shadow_outcomes,
    # not the projected sim_pnl on the decision row.
    conn.execute(
        "INSERT INTO shadow_outcomes (proposal_id, net_pnl, label_status) "
        "VALUES ('p-1', 1.25, 'RESOLVED')"
    )
    conn.commit()
    conn.close()
    return path


def test_missing_warehouse_actionable_error(tmp_path):
    with pytest.raises(wr.WarehouseError, match="warehouse not found"):
        wr.recent_trades(db_path=tmp_path / "nope.sqlite")


def test_list_tables(db):
    tables = {t["table"]: t for t in wr.list_tables(db_path=db)}
    assert tables["trades"]["rows"] == 3
    assert "realized_pnl" in tables["trades"]["columns"]


def test_recent_trades_filter(db):
    btc = wr.recent_trades(symbol="BTC/USDT:USDT", db_path=db)
    assert len(btc) == 2
    assert all(t["symbol"] == "BTC/USDT:USDT" for t in btc)


def test_performance_summary(db):
    s = wr.performance_summary(db_path=db)
    assert s["trades"] == 3
    assert s["wins"] == 1 and s["losses"] == 2
    assert s["total_realized_pnl"] == 4.0  # 10 - 4 - 2
    assert s["profit_factor"] == round(10.0 / 6.0, 4)


def test_performance_summary_symbol_filter(db):
    s = wr.performance_summary(symbol="ETH/USDT:USDT", db_path=db)
    assert s["trades"] == 1 and s["wins"] == 0


def test_recent_candidates_skip_reason(db):
    skips = wr.recent_candidates(decision="SKIP", db_path=db)
    assert len(skips) == 1
    assert skips[0]["skip_reason"] == "low_confidence"


def test_shadow_vs_live(db):
    cmp = wr.shadow_vs_live(db_path=db)
    assert cmp["shadow"]["resolved"] == 1
    assert cmp["shadow"]["total_resolved_pnl"] == 1.25
    assert cmp["live"]["trades"] == 3


@pytest.mark.parametrize(
    "sql,ok",
    [
        ("SELECT * FROM trades", True),
        ("  with x as (select 1) select * from x  ", True),
        ("SELECT 1; DROP TABLE trades", False),
        ("DELETE FROM trades", False),
        ("UPDATE trades SET realized_pnl=0", False),
        ("INSERT INTO trades (id) VALUES (9)", False),
        ("PRAGMA table_info(trades)", False),
        ("", False),
    ],
)
def test_is_select_only(sql, ok):
    assert wr.is_select_only(sql) is ok


def test_run_select_rejects_mutation(db):
    with pytest.raises(wr.WarehouseError, match="read-only SELECT"):
        wr.run_select("DELETE FROM trades", db_path=db)


def test_run_select_returns_rows_and_caps(db):
    rows = wr.run_select("SELECT symbol, realized_pnl FROM trades", limit=2, db_path=db)
    assert len(rows) == 2
    assert "symbol" in rows[0]


# ── T4: movers + F1 measurement surfaces (autoplan 2026-07-30) ──────────────


def test_recent_movers_missing_file(tmp_path):
    with pytest.raises(wr.WarehouseError, match="mover shortlist not found"):
        wr.recent_movers(path=tmp_path / "missing.json")


def test_recent_movers_corrupt_json(tmp_path):
    p = tmp_path / "mover_shortlist_latest.json"
    p.write_text("{not-json", encoding="utf-8")
    with pytest.raises(wr.WarehouseError, match="unreadable"):
        wr.recent_movers(path=p)


def test_recent_movers_non_object_json(tmp_path):
    p = tmp_path / "mover_shortlist_latest.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(wr.WarehouseError, match="corrupt"):
        wr.recent_movers(path=p)


def test_recent_movers_ok(tmp_path):
    p = tmp_path / "mover_shortlist_latest.json"
    p.write_text(
        '{"schema_version":1,"abs_band_usdt":[5,200],"movers":[]}',
        encoding="utf-8",
    )
    doc = wr.recent_movers(path=p)
    assert doc["abs_band_usdt"] == [5, 200]


def test_f1_edge_status_missing_log(tmp_path):
    with pytest.raises(wr.WarehouseError, match="carry_gate_log not found"):
        wr.f1_edge_status(path=tmp_path / "missing.jsonl")


def test_f1_edge_status_idle_no_edge(tmp_path):
    import json
    import time

    p = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    rows = [
        {"ts": now - 60, "symbol": "BTC", "venue": "bybit",
         "net_edge_bps": -12.0, "ok": False, "reason": "funding_rate -0.0001 <= 0",
         "feeds_fresh": False},
        {"ts": now - 30, "symbol": "ETH", "venue": "bybit",
         "net_edge_bps": -8.0, "ok": False, "reason": "edge 1.0bps < 5.0bps",
         "feeds_fresh": True},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    st = wr.f1_edge_status(path=p, lookback_hours=1.0)
    assert st["checks"] == 2 and st["ok"] == 0
    assert st["status"] == "idle_no_edge"
    assert st["best"]["net_edge_bps"] == -8.0
    assert st["feeds_fresh_rate"] == 0.5
    families = {row["family"]: row["count"] for row in st["top_reject_families"]}
    assert families["funding_rate_le_0"] == 1
    assert families["edge_below_cost"] == 1


def test_f1_edge_status_stale_runner(tmp_path):
    import json
    import time

    p = tmp_path / "carry_gate_log.jsonl"
    old = time.time() - 48 * 3600
    p.write_text(
        json.dumps({"ts": old, "symbol": "BTC", "net_edge_bps": -1.0, "ok": False})
        + "\n",
        encoding="utf-8",
    )
    st = wr.f1_edge_status(path=p, lookback_hours=24.0)
    assert st["checks"] == 0
    assert st["status"] == "stale_runner"


def test_f1_edge_status_partial_corrupt_lines_skipped(tmp_path):
    import json
    import time

    p = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    p.write_text(
        "not-json\n"
        + json.dumps({"ts": now, "symbol": "SOL", "net_edge_bps": 3.0,
                      "ok": True, "f1_gate_ok": True, "reason": "ok"})
        + "\n",
        encoding="utf-8",
    )
    st = wr.f1_edge_status(path=p, lookback_hours=1.0)
    assert st["checks"] == 1 and st["ok"] == 1
    assert st["status"] == "passing"


# ── T5: denominator-aware OPEN / econ-gate funnel ────────────────────────────


def _mk_decision_db(tmp_path, rows: list[dict]):
    import hashlib
    import json

    db = tmp_path / "warehouse.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE decision_events (
            event_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE,
            occurred_at TEXT NOT NULL, venue TEXT, market TEXT,
            canonical_symbol TEXT, exchange_symbol TEXT, strategy_id TEXT,
            action TEXT, side TEXT, snapshot_id TEXT, model_manifest_id TEXT,
            schema_version INTEGER, payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL
        )"""
    )
    for i, payload in enumerate(rows):
        raw = json.dumps(payload)
        sha = hashlib.sha256(raw.encode()).hexdigest()
        conn.execute(
            "INSERT INTO decision_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"e{i}", f"d{i}", payload.get("occurred_at") or "2026-07-30T00:00:00Z",
                "bybit", "perpetual", "BTC/USDT:USDT", "BTC/USDT:USDT",
                "mcp_registry", payload.get("action") or "enter_long", "buy",
                f"s{i}", None, 1, raw, sha,
            ),
        )
    conn.commit()
    conn.close()
    return db


def test_open_funnel_econ_vs_other_denominator(tmp_path):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = _mk_decision_db(
        tmp_path,
        [
            {
                "occurred_at": now,
                "action": "enter_long",
                "outcome": "rejected",
                "context": {
                    "reason": "economic_gate_model_missing",
                    "terminal_stage": "execute_open",
                    "terminal_outcome": "rejected",
                },
                "reason_codes": ["economic_gate_model_missing", "execute_open"],
            },
            {
                "occurred_at": now,
                "action": "enter_long",
                "outcome": "rejected",
                "context": {
                    "reason": "universe_filter_blocked",
                    "terminal_stage": "execute_open",
                    "terminal_outcome": "rejected",
                },
                "reason_codes": ["universe_filter_blocked", "execute_open"],
            },
            {
                "occurred_at": now,
                "action": "enter_long",
                "outcome": "filled",
                "context": {
                    "reason": "maker_first_maker_fill",
                    "terminal_stage": "maker_resolution",
                    "terminal_outcome": "filled",
                },
                "reason_codes": ["maker_first_maker_fill", "maker_resolution"],
            },
        ],
    )
    st = wr.open_funnel_status(db_path=db, lookback_hours=24.0)
    assert st["open_attempts"] == 3
    assert st["econ_blocked"] == 1
    assert st["other_rejected"] == 1
    assert st["filled"] == 1
    assert st["econ_block_rate"] == pytest.approx(1 / 3)
    assert st["fill_rate"] == pytest.approx(1 / 3)
    assert st["status"] in ("mixed", "econ_gate_dominant", "passing_or_other_blocks")
    assert "drought_status" in st


def test_open_funnel_drought_band_regime_idle(tmp_path):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    db = _mk_decision_db(
        tmp_path,
        [
            {
                "occurred_at": now,
                "action": "enter_long",
                "outcome": "rejected",
                "context": {
                    "reason": "band_regime_filter:adx_4h>30",
                    "terminal_stage": "execute_open",
                    "terminal_outcome": "rejected",
                },
                "reason_codes": ["band_regime_filter:adx_4h>30", "execute_open"],
            },
            {
                "occurred_at": now,
                "action": "enter_short",
                "outcome": "rejected",
                "context": {
                    "reason": "band_regime_filter:adx_4h>30",
                    "terminal_stage": "execute_open",
                    "terminal_outcome": "rejected",
                },
                "reason_codes": ["band_regime_filter:adx_4h>30", "execute_open"],
            },
            {
                "occurred_at": now,
                "action": "enter_long",
                "outcome": "rejected",
                "context": {
                    "reason": "universe_filter_blocked:chop:ER=0.09<0.12",
                    "terminal_stage": "execute_open",
                    "terminal_outcome": "rejected",
                },
                "reason_codes": [
                    "universe_filter_blocked:chop:ER=0.09<0.12",
                    "execute_open",
                ],
            },
        ],
    )
    st = wr.open_funnel_status(db_path=db, lookback_hours=24.0)
    assert st["drought_status"] == "band_regime_idle"
    families = {f["family"]: f["count"] for f in st["top_reject_families"]}
    assert families["band_regime_filter"] == 2
    assert families["universe_filter_blocked"] == 1


def test_open_funnel_no_attempts(tmp_path):
    db = _mk_decision_db(tmp_path, [])
    st = wr.open_funnel_status(db_path=db, lookback_hours=24.0)
    assert st["open_attempts"] == 0
    assert st["status"] == "no_open_attempts"
    assert st["econ_block_rate"] == 0.0
    assert st["drought_status"] == "no_open_attempts"
