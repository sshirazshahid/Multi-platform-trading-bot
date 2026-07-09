"""Item 2 (data-integrity backlog 2026-07-07): whole-trade REPORTING analytics.

Consumers of the runner-leg PnL that ignore the already-banked partial-TP leg
under-count WR and PnL (~150 misclassified wins/14d at audit). Whole-trade PnL =
realized_pnl + COALESCE(partial_realized_pnl, 0) (warehouse), or
pnl + realized_partial_pnl (positions.json). REPORT readers only — the live entry
gate (warehouse.recent_expectancy) is deliberately NOT touched here.
"""
from __future__ import annotations

import pytest


def _fresh_warehouse(path):
    """Warehouse with a clean thread-local connection. `Warehouse._tls` is a
    class-level thread-local shared across instances, so a 2nd Warehouse in the
    same process would otherwise reuse the first's connection (points at the
    wrong DB). Same reset idiom as tests/test_warehouse_trade_events.py."""
    from core.warehouse import Warehouse
    if hasattr(Warehouse._tls, "conn"):
        delattr(Warehouse._tls, "conn")
    return Warehouse(path=path)


# ── learning_engine (reads data/positions.json; runner `pnl` + partial leg) ──
def test_learning_engine_folds_partial_into_wr_and_pnl():
    from core.learning_engine import MIN_SAMPLE_SIZE, LearningEngine, _whole_pnl

    # runner leg -1 but +3 partial already banked -> whole-trade +2 = a WIN
    proto = {"pnl": -1.0, "realized_partial_pnl": 3.0, "strategy": "sys",
             "open_time": 0, "close_reason": "take_profit", "close_time": 1,
             "total_fees": 0.1, "paper_trade": True}
    assert _whole_pnl(proto) == 2.0

    trades = [dict(proto, symbol=f"S{i}/USDT") for i in range(MIN_SAMPLE_SIZE)]
    eng = LearningEngine()
    ins = eng._analyze(trades, trades, [])
    # every trade is a whole-trade winner despite a negative runner leg
    assert ins["overall_win_rate"] == 100.0
    assert ins["total_net_pnl"] == round(2.0 * len(trades), 4)
    assert ins["by_strategy_all"]["sys"]["win_rate"] == 100.0


# ── warehouse_reader.performance_summary (MCP) ──────────────────────────────
def test_performance_summary_folds_partial(tmp_path):
    from mcp_server import warehouse_reader as wr

    p = tmp_path / "wh.sqlite"
    w = _fresh_warehouse(p)
    tid = w.record_trade_open(
        exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        ts_entry=1.0, entry_px=100.0, size=1.0, strategy_family="sys")
    w.record_trade_close(
        trade_id=tid, ts_exit=2.0, exit_px=99.0,
        realized_pnl=-1.0, partial_realized_pnl=3.0)

    s = wr.performance_summary(db_path=p)
    assert s["trades"] == 1
    assert s["wins"] == 1 and s["losses"] == 0        # whole-trade +2 -> win
    assert s["total_realized_pnl"] == 2.0


def test_performance_summary_defensive_without_partial_column(tmp_path):
    """Older/foreign DBs lack partial_realized_pnl; the reader must fall back to
    realized_pnl alone rather than raising 'no such column'."""
    import sqlite3

    from mcp_server import warehouse_reader as wr

    p = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE trades (symbol TEXT, strategy_family TEXT, "
        "realized_pnl REAL, status TEXT)")
    conn.executemany(
        "INSERT INTO trades VALUES (?,?,?, 'CLOSED')",
        [("BTC/USDT:USDT", "sys", 5.0), ("BTC/USDT:USDT", "sys", -2.0)])
    conn.commit()
    conn.close()
    s = wr.performance_summary(db_path=p)
    assert s["trades"] == 2 and s["wins"] == 1
    assert s["total_realized_pnl"] == 3.0


# ── dashboard.load_warehouse_stats per-symbol / per-family ──────────────────
def test_dashboard_warehouse_stats_folds_partial(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    p = tmp_path / "data" / "warehouse.sqlite"
    w = _fresh_warehouse(p)
    tid = w.record_trade_open(
        exchange="binance", symbol="BTC/USDT:USDT", side="buy",
        ts_entry=1.0, entry_px=100.0, size=1.0, strategy_family="sys", mode="PAPER")
    w.record_trade_close(
        trade_id=tid, ts_exit=2.0, exit_px=99.0,
        realized_pnl=-1.0, partial_realized_pnl=3.0)

    monkeypatch.chdir(tmp_path)
    import dashboard
    stats = dashboard.load_warehouse_stats()
    assert stats.get("mode") == "PAPER"
    per_sym = {r["symbol"]: r for r in stats["per_symbol"]}
    assert per_sym["BTC/USDT:USDT"]["wins"] == 1        # whole-trade +2 -> win
    assert per_sym["BTC/USDT:USDT"]["net"] == pytest.approx(2.0)
    per_fam = {r["fam"]: r for r in stats["per_family"]}
    assert per_fam["sys"]["wins"] == 1
    assert per_fam["sys"]["net"] == pytest.approx(2.0)
