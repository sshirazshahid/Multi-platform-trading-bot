"""Tests for blueprint Phase-1 warehouse orphan auto-reconcile."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import core.health_watchdog as hw
from core.warehouse_reconcile import (
    close_orphan_trade_row,
    reconcile_warehouse_orphans,
    warehouse_orphan_auto_close_enabled,
)


def _make_wh(path: Path, rows: list) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY,
          exchange TEXT, symbol TEXT, side TEXT,
          status TEXT, ts_entry REAL,
          ts_exit REAL, exit_px REAL, realized_pnl REAL,
          r_multiple REAL, hold_sec REAL, exit_reason TEXT
        )
        """
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, exchange, symbol, side, status, ts_entry) "
            "VALUES (?,?,?,?,?,?)",
            (r[0], r[1], r[2], r[3], "OPEN", r[4]),
        )
    conn.commit()
    conn.close()


def test_close_orphan_sets_reconcile_flat(tmp_path: Path) -> None:
    wh = tmp_path / "wh.sqlite"
    age = time.time() - 100_000
    _make_wh(wh, [(7, "binance", "ETH/USDT:USDT", "buy", age)])
    assert close_orphan_trade_row(wh, 7, ts_entry=age, now=age + 50_000)
    conn = sqlite3.connect(str(wh))
    row = conn.execute(
        "SELECT status, exit_reason, realized_pnl, exit_px FROM trades WHERE id=7"
    ).fetchone()
    conn.close()
    assert row[0] == "CLOSED"
    assert row[1] == "reconcile_flat"
    assert row[2] == 0.0
    assert row[3] is None


def test_reconcile_closes_orphan_not_in_tracker(tmp_path: Path, monkeypatch) -> None:
    wh = tmp_path / "wh.sqlite"
    pos = tmp_path / "positions.json"
    age = time.time() - (hw.STUCK_OPEN_HOURS + 2) * 3600
    _make_wh(
        wh,
        [
            (1, "binance", "ETH/USDT:USDT", "buy", age),
            (2, "binance", "BTC/USDT:USDT", "sell", age),
        ],
    )
    pos.write_text(
        json.dumps(
            {
                "open": [
                    {
                        "exchange": "binance",
                        "symbol": "BTC/USDT:USDT",
                        "side": "sell",
                    }
                ],
                "closed": [],
            }
        ),
        encoding="utf-8",
    )
    out = reconcile_warehouse_orphans(wh, positions_path=pos)
    assert out["ok"] is True
    assert len(out["closed"]) == 1
    assert out["closed"][0]["id"] == 1
    conn = sqlite3.connect(str(wh))
    statuses = dict(conn.execute("SELECT id, status FROM trades").fetchall())
    conn.close()
    assert statuses[1] == "CLOSED"
    assert statuses[2] == "OPEN"  # still in tracker


def test_reconcile_dry_run_does_not_write(tmp_path: Path) -> None:
    wh = tmp_path / "wh.sqlite"
    pos = tmp_path / "positions.json"
    age = time.time() - (hw.STUCK_OPEN_HOURS + 2) * 3600
    _make_wh(wh, [(9, "binance", "SOL/USDT:USDT", "buy", age)])
    pos.write_text(json.dumps({"open": [], "closed": []}), encoding="utf-8")
    out = reconcile_warehouse_orphans(wh, positions_path=pos, dry_run=True)
    assert out["closed"][0]["dry_run"] is True
    conn = sqlite3.connect(str(wh))
    status = conn.execute("SELECT status FROM trades WHERE id=9").fetchone()[0]
    conn.close()
    assert status == "OPEN"


def test_auto_close_default_paper_only(monkeypatch) -> None:
    monkeypatch.delenv("WAREHOUSE_ORPHAN_AUTO_CLOSE", raising=False)
    monkeypatch.setattr("config.OPERATING_MODE", "PAPER", raising=False)
    # Import path uses config.OPERATING_MODE — patch module attr if loaded
    import config

    monkeypatch.setattr(config, "OPERATING_MODE", "PAPER", raising=False)
    assert warehouse_orphan_auto_close_enabled() is True
    monkeypatch.setattr(config, "OPERATING_MODE", "CONTROLLED_LIVE", raising=False)
    assert warehouse_orphan_auto_close_enabled() is False
    monkeypatch.setenv("WAREHOUSE_ORPHAN_AUTO_CLOSE", "true")
    assert warehouse_orphan_auto_close_enabled() is True
