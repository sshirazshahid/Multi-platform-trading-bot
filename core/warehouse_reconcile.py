"""Warehouse orphan auto-close (blueprint Phase 1) — learning-book only.

Closes warehouse trades stuck at status=OPEN when no matching open exists in
positions.json. Never invents PnL. Never places exchange orders.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from loguru import logger

from core.health_watchdog import (
    STUCK_OPEN_HOURS,
    orphan_open_trade_rows,
)


def warehouse_orphan_auto_close_enabled() -> bool:
    """Env override, else True only under PAPER (plan default)."""
    raw = os.getenv("WAREHOUSE_ORPHAN_AUTO_CLOSE", "").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    try:
        from config import OPERATING_MODE

        return str(OPERATING_MODE).upper() == "PAPER"
    except Exception:
        return False


def close_orphan_trade_row(
    warehouse_path: Path,
    trade_id: int,
    *,
    ts_entry: float,
    exit_reason: str = "reconcile_flat",
    now: float | None = None,
) -> bool:
    """Mark one OPEN trade CLOSED with honest zero PnL / null exit px.

    Returns True if a row was updated.
    """
    ts_exit = float(now if now is not None else time.time())
    hold_sec = max(0.0, ts_exit - float(ts_entry))
    try:
        conn = sqlite3.connect(str(warehouse_path))
        try:
            cur = conn.execute(
                """
                UPDATE trades SET
                  ts_exit=?, exit_px=NULL, realized_pnl=0.0, r_multiple=NULL,
                  hold_sec=?, exit_reason=?, status='CLOSED'
                WHERE id=? AND status='OPEN'
                """,
                (ts_exit, hold_sec, exit_reason, int(trade_id)),
            )
            conn.commit()
            return int(cur.rowcount or 0) > 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(f"[WarehouseReconcile] close id={trade_id} failed: {exc}")
        return False


def reconcile_warehouse_orphans(
    warehouse_path: Path,
    *,
    positions_path: Path,
    older_than_hours: float = STUCK_OPEN_HOURS,
    limit: int = 50,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Close orphan OPEN warehouse rows (not in positions.json).

    Fail-closed: returns closed=[] when orphan check cannot run safely.
    """
    orphans = orphan_open_trade_rows(
        warehouse_path,
        older_than_hours=older_than_hours,
        positions_path=positions_path,
        limit=limit,
    )
    if orphans is None:
        return {
            "ok": False,
            "reason": "orphan_check_unsafe",
            "closed": [],
            "skipped": 0,
        }
    closed: list[dict[str, Any]] = []
    for row in orphans:
        trade_id = int(row[0])
        ts_entry = float(row[4])
        meta = {
            "id": trade_id,
            "exchange": str(row[1]),
            "symbol": str(row[2]),
            "side": str(row[3]),
        }
        if dry_run:
            closed.append({**meta, "dry_run": True})
            continue
        if close_orphan_trade_row(
            warehouse_path,
            trade_id,
            ts_entry=ts_entry,
            exit_reason="reconcile_flat",
            now=now,
        ):
            closed.append(meta)
            logger.info(
                f"[WarehouseReconcile] closed orphan trade id={trade_id} "
                f"{meta['exchange']} {meta['symbol']} {meta['side']} "
                f"reason=reconcile_flat"
            )
    return {
        "ok": True,
        "reason": "reconciled" if closed else "none",
        "closed": closed,
        "orphan_count": len(orphans),
        "skipped": 0,
    }
