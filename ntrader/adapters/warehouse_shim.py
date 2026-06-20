"""Warehouse mirror — writes to a SEPARATE nt/data/nt_warehouse.sqlite.

Mirrors the exact keyword-only signatures of core/warehouse.py
(record_candidate / record_trade_open / record_trade_close) so the NT strategies
exercise the same learning-substrate contract as the live bot — but NEVER touches
the live data/warehouse.sqlite. The schema is the subset of columns the live
warehouse uses for these three calls.

`realized_pnl` is DOLLARS (matches core/warehouse.py's documented contract), not a
per-unit price delta.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "nt_warehouse.sqlite"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, exchange TEXT, symbol TEXT, market_type TEXT, side TEXT,
        strategy_family TEXT, regime_label TEXT, entry_px REAL, stop_px REAL,
        target_px REAL, leverage INTEGER, size_pct REAL, confidence REAL,
        decision TEXT, skip_reason TEXT, features_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER, ts_entry REAL, exchange TEXT, symbol TEXT,
        market_type TEXT, side TEXT, strategy_family TEXT, entry_px REAL,
        size REAL, leverage INTEGER, fee REAL, slippage REAL, status TEXT,
        mode TEXT, mcp_score REAL, model_version TEXT, fill_type TEXT,
        decision_id TEXT, ts_exit REAL, exit_px REAL, realized_pnl REAL,
        r_multiple REAL, hold_sec REAL, exit_reason TEXT,
        UNIQUE(exchange, symbol, ts_entry, side))""",
)


class WarehouseShim:
    """Minimal, signature-compatible mirror of core/warehouse.Warehouse."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _DEFAULT
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        for ddl in _SCHEMA:
            conn.execute(ddl)
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self, "_c", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path))
            conn.row_factory = sqlite3.Row
            self._c = conn
        return conn

    def record_candidate(
        self, *, exchange: str, symbol: str, market_type: str = "futures",
        side: str | None = None, strategy_family: str | None = None,
        regime_label: str | None = None, entry_px: float | None = None,
        stop_px: float | None = None, target_px: float | None = None,
        leverage: int | None = None, size_pct: float | None = None,
        confidence: float | None = None, decision: str = "SKIP",
        skip_reason: str | None = None, features: dict | None = None,
        ts: float | None = None,
    ) -> int:
        cur = self._conn().execute(
            """INSERT INTO candidates(
                ts, exchange, symbol, market_type, side, strategy_family,
                regime_label, entry_px, stop_px, target_px, leverage, size_pct,
                confidence, decision, skip_reason, features_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts or time.time(), exchange, symbol, market_type, side, strategy_family,
             regime_label, entry_px, stop_px, target_px, leverage, size_pct,
             confidence, decision, skip_reason,
             json.dumps(features or {}, separators=(",", ":"))),
        )
        self._conn().commit()
        return int(cur.lastrowid)

    def record_trade_open(
        self, *, exchange: str, symbol: str, side: str, ts_entry: float,
        entry_px: float, size: float, leverage: int = 1,
        candidate_id: int | None = None, market_type: str = "futures",
        strategy_family: str | None = None, fee: float = 0.0, slippage: float = 0.0,
        mode: str = "PAPER", mcp_score: float | None = None,
        model_version: str | None = None, fill_type: str | None = None,
        decision_id: str | None = None,
    ) -> int:
        cur = self._conn().execute(
            """INSERT OR IGNORE INTO trades(
                candidate_id, ts_entry, exchange, symbol, market_type, side,
                strategy_family, entry_px, size, leverage, fee, slippage, status,
                mode, mcp_score, model_version, fill_type, decision_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (candidate_id, ts_entry, exchange, symbol, market_type, side,
             strategy_family, entry_px, size, leverage, fee, slippage, "OPEN",
             mode, mcp_score, model_version, fill_type, decision_id),
        )
        self._conn().commit()
        if cur.rowcount > 0:
            return int(cur.lastrowid)
        got = self._conn().execute(
            "SELECT id FROM trades WHERE exchange=? AND symbol=? AND ts_entry=? AND side=?",
            (exchange, symbol, ts_entry, side),
        ).fetchone()
        return int(got["id"]) if got else -1

    def record_trade_close(
        self, *, trade_id: int, ts_exit: float, exit_px: float, realized_pnl: float,
        r_multiple: float | None = None, hold_sec: float | None = None,
        exit_reason: str = "", fee: float | None = None, slippage: float | None = None,
        entry_stop_px: float | None = None, partial_realized_pnl: float | None = None,
        exit_decision_id: str | None = None,
    ) -> None:
        self._conn().execute(
            """UPDATE trades SET status='CLOSED', ts_exit=?, exit_px=?, realized_pnl=?,
                   r_multiple=?, hold_sec=?, exit_reason=?,
                   fee=COALESCE(?, fee), slippage=COALESCE(?, slippage)
               WHERE id=?""",
            (ts_exit, exit_px, realized_pnl, r_multiple, hold_sec, exit_reason,
             fee, slippage, trade_id),
        )
        self._conn().commit()
