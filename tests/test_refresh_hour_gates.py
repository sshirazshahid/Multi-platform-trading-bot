"""Tests for scripts.refresh_hour_gates.

Builds a synthetic warehouse.sqlite, runs build_evidence(), and asserts
the classified hour buckets reflect the input distribution.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import refresh_hour_gates as rhg  # noqa: E402


def _make_warehouse(tmp_path: Path, rows: list[tuple]) -> Path:
    """rows = [(hour_offset_back, n, wins, total_pnl), ...] — synthesise rows."""
    db = tmp_path / "wh.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY,
          ts_entry REAL, ts_exit REAL, exchange TEXT, symbol TEXT,
          status TEXT, realized_pnl REAL
        )
        """
    )
    now = int(time.time())
    rid = 0
    for hour_utc, n, wins, total_pnl in rows:
        per_win = (max(total_pnl, 1.0) / wins) if wins > 0 else 0.0
        per_loss = ((total_pnl - wins * per_win) / max(1, n - wins)) if n > wins else 0.0
        for i in range(n):
            rid += 1
            day = now - (rid % 30) * 86400
            day_start = day - (day % 86400)
            ts = day_start + hour_utc * 3600 + i
            pnl = per_win if i < wins else per_loss
            conn.execute(
                "INSERT INTO trades (id,ts_entry,ts_exit,exchange,symbol,status,realized_pnl) "
                "VALUES (?,?,?,?,?,?,?)",
                (rid, ts, ts + 60, "test", "BTC/USDT", "CLOSED", pnl),
            )
    conn.commit()
    conn.close()
    return db


def test_blocked_hour_when_low_wr_and_negative_pnl(tmp_path):
    db = _make_warehouse(tmp_path, [
        (17, 12, 3, -10.0),
        (15, 10, 7, +5.0),
        (5,  3,  1, -2.0),
    ])
    ev = rhg.build_evidence(db, lookback_days=60)
    assert 17 in ev["blocked"]
    assert 15 in ev["green"]
    assert 5 in ev["neutral"]


def test_no_data_returns_empty_blocked(tmp_path):
    db = _make_warehouse(tmp_path, [])
    ev = rhg.build_evidence(db, lookback_days=60)
    assert ev["blocked"] == []
    assert ev["green"] == []
    assert len(ev["neutral"]) == 24


def test_borderline_does_not_block(tmp_path):
    """WR 40% + small loss is grey-zone — should be NEUTRAL, not BLOCKED."""
    db = _make_warehouse(tmp_path, [(10, 10, 4, -1.5)])
    ev = rhg.build_evidence(db, lookback_days=60)
    assert 10 not in ev["blocked"]
    assert 10 in ev["neutral"]


def test_missing_db_returns_empty(tmp_path):
    ev = rhg.build_evidence(tmp_path / "nope.sqlite", lookback_days=60)
    assert ev["blocked"] == []
    assert ev["green"] == []
