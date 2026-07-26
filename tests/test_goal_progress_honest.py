from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scripts.report_goal_progress import (
    RESOLVED_FLOOR,
    _runtime_context,
    excursion_telemetry_summary,
    performance_summary,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            ts_entry REAL,
            ts_exit REAL,
            market_type TEXT,
            strategy_family TEXT,
            realized_pnl REAL,
            partial_realized_pnl REAL,
            status TEXT,
            mode TEXT,
            decision_id TEXT
        )"""
    )
    return conn


def _insert(conn, trade_id, pnl, *, partial=0.0, mode="PAPER",
            market="futures", status="CLOSED", family="algo_det",
            decision_id="decision", ts_exit=200.0):
    conn.execute(
        "INSERT INTO trades (id,ts_entry,ts_exit,market_type,strategy_family,"
        "realized_pnl,partial_realized_pnl,status,mode,decision_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (trade_id, 100.0, ts_exit, market, family, pnl, partial,
         status, mode, decision_id),
    )


def test_performance_counts_only_provenance_bearing_closed_paper_futures():
    conn = _conn()
    _insert(conn, 1, 1.0, partial=0.25)
    _insert(conn, 2, -0.5)
    _insert(conn, 3, 99.0, mode="CONTROLLED_LIVE")
    _insert(conn, 4, 99.0, market="spot")
    _insert(conn, 5, 99.0, status="OPEN")
    _insert(conn, 6, 99.0, family="reconciled_exchange")
    _insert(conn, 7, 99.0, decision_id=None)

    result = performance_summary(conn, lane="x", since_epoch=0)
    assert result["closed_outcomes"] == 2
    assert result["wins"] == 1 and result["losses"] == 1
    assert result["net_after_cost_pnl"] == pytest.approx(0.75)
    assert result["profit_factor"] == pytest.approx(2.5)
    assert result["sample_mature"] is False
    assert result["target_status"] == "INSUFFICIENT_SAMPLE"


def test_target_requires_band_and_positive_after_cost_economics():
    conn = _conn()
    # 20/30 = 66.7%, positive expectancy and PF=4.0.
    for trade_id in range(1, 21):
        _insert(conn, trade_id, 1.0)
    for trade_id in range(21, 31):
        _insert(conn, trade_id, -0.5)
    result = performance_summary(conn, lane="x", since_epoch=0)
    assert result["closed_outcomes"] == RESOLVED_FLOOR
    assert result["win_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert result["profit_factor"] == pytest.approx(4.0)
    assert result["target_status"] == "TARGET_MET"

    conn = _conn()
    # Same WR, but losses dominate after costs. Win-rate geometry is not success.
    for trade_id in range(1, 21):
        _insert(conn, trade_id, 0.1)
    for trade_id in range(21, 31):
        _insert(conn, trade_id, -1.0)
    result = performance_summary(conn, lane="x", since_epoch=0)
    assert TARGET_WR_IN_BAND(result["win_rate"])
    assert result["target_status"] == "NEGATIVE_AFTER_COST_ECONOMICS"


def TARGET_WR_IN_BAND(value):
    return 0.63 <= value <= 0.67


def test_runtime_context_uses_stable_profile_start_from_heartbeat(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "heartbeat.json").write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": 50,
        "paper_trading_profile": "AGGRESSIVE_RESEARCH",
        "paper_profile_started_at": "1234.5",
        "operating_mode": "PAPER",
        "dry_run": True,
        "entry_policy": "APPROVED_PAPER",
    }), encoding="utf-8")
    context = _runtime_context(tmp_path)
    assert context["profile"] == "AGGRESSIVE_RESEARCH"
    assert context["cohort_started_at"] == pytest.approx(1234.5)
    assert context["cohort_start_source"] == "heartbeat_profile_start"
    assert context["operating_mode"] == "PAPER"
    assert context["dry_run"] is True


def test_excursion_telemetry_separates_legacy_and_post_fix_coverage():
    conn = _conn()
    conn.execute("ALTER TABLE trades ADD COLUMN mfe REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN mae REAL")
    _insert(conn, 1, 1.0)
    _insert(conn, 2, -1.0)
    _insert(conn, 3, 1.0)
    conn.execute("UPDATE trades SET ts_entry=50, mfe=0.2, mae=-0.1 WHERE id=1")
    conn.execute("UPDATE trades SET ts_entry=150, mfe=0.3, mae=-0.2 WHERE id=2")
    conn.execute("UPDATE trades SET ts_entry=160, mfe=0.4 WHERE id=3")

    result = excursion_telemetry_summary(conn, since_epoch=100)

    assert result["legacy_closed_trades"] == 3
    assert result["legacy_captured_trades"] == 2
    assert result["post_fix_closed_trades"] == 2
    assert result["post_fix_captured_trades"] == 1
    assert result["post_fix_coverage"] == pytest.approx(0.5)
    assert result["runtime_wiring_verified"] is True
    assert result["analysis_sample_mature"] is False
    assert result["status"] == "INCOMPLETE_CAPTURE"
