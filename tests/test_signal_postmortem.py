"""Tests for scripts.run_signal_postmortem."""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import run_signal_postmortem as rsp  # noqa: E402


def _seed_warehouse(db: Path, rows: list[tuple]) -> None:
    """rows = [(symbol, side, score, exit_reason, pnl, hours_ago), ...]"""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, ts_entry REAL, ts_exit REAL, "
        "exchange TEXT, symbol TEXT, market_type TEXT, side TEXT, "
        "strategy_family TEXT, realized_pnl REAL, r_multiple REAL, "
        "hold_sec REAL, exit_reason TEXT, mcp_score REAL, status TEXT)"
    )
    now = time.time()
    for i, (symbol, side, score, exit_reason, pnl, hours_ago) in enumerate(rows):
        ts = now - hours_ago * 3600
        conn.execute(
            "INSERT INTO trades (ts_entry, ts_exit, exchange, symbol, market_type, "
            "side, strategy_family, realized_pnl, r_multiple, hold_sec, exit_reason, "
            "mcp_score, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts - 600, ts, "binance", symbol, "futures", side, "claude_portfolio",
             pnl, 0.0, 600, exit_reason, score, "CLOSED"),
        )
    conn.commit()
    conn.close()


def test_summary_shape(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_warehouse(db, [
        ("APT/USDT:USDT", "sell", 88.0, "stop_loss", -3.5, 1),
        ("APT/USDT:USDT", "sell", 85.0, "stop_loss", -2.5, 2),
        ("BTC/USDT:USDT", "buy",  72.0, "trailing_stop", 1.5, 3),
    ])
    trades = rsp._load_trades(db, lookback_days=7)
    rep = rsp.build_report(trades, lookback_days=7)
    assert rep["trade_count"] == 3
    assert rep["wr_pct"] == round(100.0 / 3, 2)
    sym_keys = {r["key"] for r in rep["by_symbol"]}
    assert {"APT/USDT:USDT", "BTC/USDT:USDT"} <= sym_keys


def test_actionables_flag_loss_concentration(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_warehouse(db, [
        ("APT/USDT:USDT", "sell", 80.0, "stop_loss", -2.0, h)
        for h in range(6)
    ])
    rep = rsp.build_report(rsp._load_trades(db, 7), 7)
    keys = {(a["group"], a["key"]) for a in rep["actionables"]}
    assert ("symbol", "APT/USDT:USDT") in keys


def test_score_bucketing(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_warehouse(db, [
        ("BTC/USDT:USDT", "buy", None, "trailing_stop", 1.0, 1),
        ("BTC/USDT:USDT", "buy", 65.0, "trailing_stop", 1.0, 2),
        ("BTC/USDT:USDT", "buy", 75.0, "trailing_stop", 1.0, 3),
        ("BTC/USDT:USDT", "buy", 85.0, "stop_loss",   -2.0, 4),
    ])
    rep = rsp.build_report(rsp._load_trades(db, 7), 7)
    buckets = {r["key"]: r for r in rep["by_score_bucket"]}
    assert "no_score" in buckets and "60-70" in buckets and "80+" in buckets
    assert buckets["80+"]["pnl"] == -2.0


def test_empty_warehouse_returns_zero_report(tmp_path):
    db = tmp_path / "wh.sqlite"
    _seed_warehouse(db, [])
    rep = rsp.build_report(rsp._load_trades(db, 7), 7)
    assert rep["trade_count"] == 0
    assert rep["actionables"] == []
