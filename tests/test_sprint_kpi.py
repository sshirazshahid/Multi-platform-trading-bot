"""Tests for scripts/sprint_kpi.py — read-only bucket reporter.

Verifies:
1. bucket_for_reason() partitions exit reasons into the 7 buckets exactly once.
2. Free-text reasons containing "age" or "cutoff" fall through to AGE_TEXT.
3. --markdown output emits a properly formatted table with TOTAL row.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sprint_kpi.py"

# Make scripts/ importable so we can call bucket_for_reason() directly.
sys.path.insert(0, str(REPO_ROOT / "scripts"))


TRADES_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER,
    ts_entry REAL NOT NULL,
    ts_exit REAL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market_type TEXT,
    side TEXT NOT NULL,
    strategy_family TEXT,
    entry_px REAL,
    exit_px REAL,
    size REAL,
    leverage INTEGER,
    fee REAL,
    slippage REAL,
    realized_pnl REAL,
    r_multiple REAL,
    hold_sec REAL,
    status TEXT,
    exit_reason TEXT,
    mode TEXT,
    mcp_score REAL,
    model_version TEXT
)
"""


def _make_wh(tmp_path: Path, rows: list[tuple[str, float]]) -> Path:
    """Build an isolated warehouse with one CLOSED trade per (exit_reason, pnl)."""
    wh = tmp_path / "wh.sqlite"
    conn = sqlite3.connect(wh)
    conn.execute(TRADES_SCHEMA)
    # Use a fixed entry timestamp inside any reasonable test window.
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
    for er, pnl in rows:
        conn.execute(
            "INSERT INTO trades (ts_entry, ts_exit, exchange, symbol, side, "
            "realized_pnl, status, exit_reason) VALUES (?,?,?,?,?,?,?,?)",
            (ts, ts + 300, "binance", "BTC/USDT:USDT", "long", pnl, "CLOSED", er),
        )
    conn.commit()
    conn.close()
    return wh


def test_buckets_partition_correctly():
    """Each canonical exit reason routes to exactly one bucket."""
    from sprint_kpi import bucket_for_reason

    cases = {
        "WIN": ["mcp_take_profit", "trailing_stop", "partial_take_profit"],
        "GHOST": [
            "ghost_sync",
            "ghost_reconciled",
            "ghost_force_close",
            "ghost_reroute_exhausted",
        ],
        "AGE": [
            "AGE_LIMIT",
            "AGE_LOSS",
            "STALE",
            "STALE_post_grace",
            "AGE_LIMIT_post_grace",
            "AGE_LOSS_post_grace",
        ],
        "SOFT_OTHER": ["systematic_close", "sl_failed", "sl_placement_failed"],
        "SL": ["stop_loss"],
        "OTHER": ["random_unknown_reason", "manual_close", ""],
    }
    for expected_bucket, reasons in cases.items():
        for r in reasons:
            assert bucket_for_reason(r) == expected_bucket, (
                f"exit_reason={r!r} expected={expected_bucket} "
                f"got={bucket_for_reason(r)}"
            )


def test_age_text_fallback_catches_free_text_reasons():
    """Free-text exit reasons mentioning 'age' or 'cutoff' bucket to AGE_TEXT."""
    from sprint_kpi import bucket_for_reason

    free_text = [
        "STAR age 188m>90m cutoff",
        "Past 4h age cutoff, -1.8%",
        "Hit age limit at 95min",
        "AGE_DUMP fallback",  # contains "age"
    ]
    for er in free_text:
        b = bucket_for_reason(er)
        assert b == "AGE_TEXT", f"exit_reason={er!r} expected=AGE_TEXT got={b}"

    # Negative control: bare unknown reason without "age"/"cutoff" -> OTHER.
    assert bucket_for_reason("random_string") == "OTHER"


def test_markdown_output_has_correct_columns(tmp_path):
    """--markdown emits a table with Bucket | $ | n headers and a TOTAL row."""
    wh = _make_wh(
        tmp_path,
        [
            ("mcp_take_profit", 2.50),
            ("trailing_stop", 1.20),
            ("ghost_sync", -3.40),
            ("AGE_LIMIT", -0.80),
            ("stop_loss", -1.10),
            ("systematic_close", -0.30),
            ("STAR age 100m>90m cutoff", -0.50),
            ("freeform_unknown", -0.10),
        ],
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--since", "2026-04-01",
         "--warehouse", str(wh), "--markdown"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"stderr={result.stderr}"
    out = result.stdout

    # Table header columns.
    assert "| Bucket | $ | n |" in out
    assert "|---|---:|---:|" in out
    # Each bucket appears as its own row.
    for b in ("WIN", "GHOST", "AGE", "AGE_TEXT", "SOFT_OTHER", "SL", "OTHER"):
        assert f"| {b} |" in out, f"Missing bucket row {b!r} in output:\n{out}"
    # TOTAL row uses bold-markdown wrapping.
    assert "**TOTAL**" in out
