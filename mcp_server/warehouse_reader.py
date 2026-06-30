"""Read-only access layer for the trading bot's SQLite warehouse.

This module is intentionally free of any ``mcp`` (FastMCP) dependency and of any
heavy bot imports (config, bot_engine, ccxt) so it can be unit-tested in
isolation and so the MCP server process stays lightweight. Every connection is
opened in SQLite read-only mode — this layer can never mutate trading state.

The MCP tool wrappers in ``trading_bot_mcp.py`` are thin shells over these
functions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# Repo root is the parent of this file's directory (mcp_server/).
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "warehouse.sqlite"

# Only a single read-only SELECT/WITH statement is allowed through run_select.
_FORBIDDEN_SQL = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "truncate",
)


class WarehouseError(RuntimeError):
    """Raised for any read failure with an actionable message."""


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the warehouse strictly read-only. Raises WarehouseError if missing."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not path.exists():
        raise WarehouseError(
            f"warehouse not found at {path}. The bot has not produced a warehouse "
            f"yet (fresh install / never run). No trade data to query."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def list_tables(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return each table name with its row count and column names."""
    conn = _connect(db_path)
    try:
        tables = _rows(
            conn,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
        out: list[dict[str, Any]] = []
        for t in tables:
            name = t["name"]
            count = conn.execute(f"SELECT COUNT(*) AS n FROM '{name}'").fetchone()["n"]
            cols = [c["name"] for c in _rows(conn, f"PRAGMA table_info('{name}')")]
            out.append({"table": name, "rows": count, "columns": cols})
        return out
    finally:
        conn.close()


def recent_trades(
    limit: int = 20, symbol: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Most recent CLOSED trades, newest first, optionally filtered by symbol."""
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT symbol, exchange, side, strategy_family, entry_px, exit_px, "
            "realized_pnl, r_multiple, leverage, hold_sec, exit_reason, "
            "ts_entry, ts_exit FROM trades WHERE status='CLOSED'"
        )
        params: list[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY ts_exit DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return _rows(conn, sql, tuple(params))
    finally:
        conn.close()


def performance_summary(
    symbol: str | None = None, strategy: str | None = None, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Aggregate after-the-fact performance over CLOSED trades.

    Returns win rate, profit factor, total/avg realized PnL, and counts — the
    numbers needed to judge whether the live path (or a filtered slice) has edge.
    """
    conn = _connect(db_path)
    try:
        where = ["status='CLOSED'", "realized_pnl IS NOT NULL"]
        params: list[Any] = []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if strategy:
            where.append("strategy_family = ?")
            params.append(strategy)
        clause = " AND ".join(where)
        rows = _rows(conn, f"SELECT realized_pnl FROM trades WHERE {clause}", tuple(params))
        pnls = [float(r["realized_pnl"]) for r in rows]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)  # positive magnitude
        total = sum(pnls)
        return {
            "filter": {"symbol": symbol, "strategy": strategy},
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / n, 4) if n else None,
            "total_realized_pnl": round(total, 4),
            "avg_pnl_per_trade": round(total / n, 4) if n else None,
            "profit_factor": (
                round(gross_win / gross_loss, 4)
                if gross_loss > 0
                else (None if gross_win == 0 else float("inf"))
            ),
            "gross_win": round(gross_win, 4),
            "gross_loss": round(gross_loss, 4),
        }
    finally:
        conn.close()


def recent_candidates(
    limit: int = 20, decision: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Most recent candidate setups the engine evaluated, newest first.

    ``decision`` filters by ALLOW | SKIP | REVIEW | TAKEN; SKIP rows carry the
    skip_reason so you can see *why* setups were rejected.
    """
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT ts, exchange, symbol, side, strategy_family, confidence, "
            "decision, skip_reason, entry_px, leverage FROM candidates"
        )
        params: list[Any] = []
        if decision:
            sql += " WHERE decision = ?"
            params.append(decision.upper())
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return _rows(conn, sql, tuple(params))
    finally:
        conn.close()


def shadow_vs_live(db_path: str | Path | None = None) -> dict[str, Any]:
    """Compare the shadow agent ensemble's simulated PnL against live trades.

    This is the promotion-criterion question: the shadow ensemble may only be
    moved to the live decision path once it beats live on an honest gate. Both
    sides are reported as count + total/avg simulated/realized PnL.
    """
    conn = _connect(db_path)
    try:
        # Phase 0b: report forward-RESOLVED net PnL (after-cost, SL-first) from
        # shadow_outcomes, NOT the projected-at-TP shadow_decisions.sim_pnl.
        shadow = _rows(
            conn,
            "SELECT COUNT(*) AS n, COALESCE(SUM(net_pnl),0) AS total, "
            "COALESCE(AVG(net_pnl),0) AS avg FROM shadow_outcomes "
            "WHERE label_status='RESOLVED' AND net_pnl IS NOT NULL",
        )[0]
        live = _rows(
            conn,
            "SELECT COUNT(*) AS n, COALESCE(SUM(realized_pnl),0) AS total, "
            "COALESCE(AVG(realized_pnl),0) AS avg FROM trades "
            "WHERE status='CLOSED' AND realized_pnl IS NOT NULL",
        )[0]
        return {
            "shadow": {
                "resolved": shadow["n"],
                "total_resolved_pnl": round(float(shadow["total"]), 4),
                "avg_resolved_pnl": round(float(shadow["avg"]), 4),
            },
            "live": {
                "trades": live["n"],
                "total_realized_pnl": round(float(live["total"]), 4),
                "avg_realized_pnl": round(float(live["avg"]), 4),
            },
            "note": (
                "Shadow is log-only. Promote to the live path only after shadow "
                "beats live on the honest promotion gate (core/promotion_gate.py)."
            ),
        }
    finally:
        conn.close()


def is_select_only(sql: str) -> bool:
    """True iff sql is a single read-only SELECT/WITH statement.

    Rejects multiple statements and any DDL/DML keyword so the freeform query
    tool can never mutate the warehouse (defence-in-depth on top of mode=ro).
    """
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return False
    if ";" in stripped:  # no multiple statements
        return False
    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False
    return not any(f" {kw} " in f" {lowered} " for kw in _FORBIDDEN_SQL)


def run_select(
    sql: str, limit: int = 100, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Run a guarded, read-only SELECT and return rows (capped by limit).

    Raises WarehouseError with an actionable message if the statement is not a
    single read-only SELECT.
    """
    if not is_select_only(sql):
        raise WarehouseError(
            "Only a single read-only SELECT (or WITH ... SELECT) statement is "
            "allowed. No INSERT/UPDATE/DELETE/DDL or multiple statements."
        )
    conn = _connect(db_path)
    try:
        capped = f"SELECT * FROM ({sql.strip().rstrip(';')}) LIMIT {max(1, min(limit, 1000))}"
        return _rows(conn, capped)
    except sqlite3.Error as e:
        raise WarehouseError(f"SQL error: {e}") from e
    finally:
        conn.close()
