"""Current-boot cohort lane in scripts/report_goal_progress.py (2026-07-10).

The owner sees 44% WR because the status line aggregates dead-regime cohorts.
The report gains a "current-boot cohort" lane: WR / n / pnl of trades whose
ts_entry >= the live process boot time (newest "Signal source:" line in
today's bot log; fallback: last 6 hours).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from scripts.report_goal_progress import (
    _boot_epoch,
    current_boot_summary,
    render_journal,
)


def _conn_with_trades(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE trades (ts_entry REAL, ts_exit REAL, realized_pnl REAL)")
    conn.executemany("INSERT INTO trades VALUES (?,?,?)", rows)
    return conn


def test_cohort_counts_only_trades_entered_since_boot():
    now = time.time()
    boot = now - 3600
    rows = [
        (boot - 7200, boot - 3600, +5.0),   # pre-boot win: excluded
        (boot - 7200, boot + 100, -3.0),    # pre-boot entry: excluded
        (boot + 60, boot + 120, +1.0),      # post-boot win
        (boot + 200, boot + 400, +2.0),     # post-boot win
        (boot + 300, boot + 500, -1.0),     # post-boot loss
        (boot + 600, None, 0.0),            # still open: excluded
    ]
    conn = _conn_with_trades(rows)
    lane = current_boot_summary(conn, since_epoch=boot)
    assert lane["available"] is True
    assert lane["closed_trades"] == 3
    assert lane["wins"] == 2
    assert lane["wr"] == round(2 / 3, 4)
    assert lane["net_pnl"] == 2.0


def test_cohort_fallback_is_last_6h_when_no_boot_log(tmp_path):
    conn = _conn_with_trades([])
    lane = current_boot_summary(conn, root=tmp_path)  # no logs/ dir at all
    assert lane["since_source"] == "fallback_6h"
    assert time.time() - lane["since_epoch"] <= 6 * 3600 + 60


def test_boot_epoch_reads_newest_signal_source_line(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    day = datetime.now().strftime("%Y-%m-%d")
    log = logs / f"bot_{day}.log"
    log.write_text(
        f"{day} 01:02:03 | INFO | [Engine] Signal source: mcp (Claude/MCP scoring path)\n"
        f"{day} 04:05:06 | INFO | [Engine] Signal source: mcp (Claude/MCP scoring path)\n"
        f"{day} 05:00:00 | INFO | [Orders] something else\n",
        encoding="utf-8",
    )
    epoch = _boot_epoch(tmp_path)
    assert epoch is not None
    assert datetime.fromtimestamp(epoch).strftime("%H:%M:%S") == "04:05:06"


def test_render_journal_includes_cohort_line():
    report = {
        "goal": "g", "generated_utc": "t",
        "lanes": [{
            "lane": "current_boot", "available": True,
            "since_source": "boot_log", "since_epoch": 0.0,
            "closed_trades": 3, "wins": 2, "wr": 0.6667, "net_pnl": 2.0,
        }],
    }
    text = render_journal(report)
    assert "current-boot cohort" in text
    assert "66.7%" in text
