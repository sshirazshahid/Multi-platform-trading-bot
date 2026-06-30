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
