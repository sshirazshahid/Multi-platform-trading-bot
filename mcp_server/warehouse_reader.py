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
        # Whole-trade PnL folds any already-banked partial-TP leg into the runner
        # realized_pnl so partial-taken winners are not misclassified as losers
        # (item 2, 2026-07-07 backlog). REPORT-ONLY — the live entry gate reads
        # warehouse.recent_expectancy, not this. partial_realized_pnl is absent on
        # older/foreign DBs (this layer opens arbitrary warehouses mode=ro), so
        # fall back to realized_pnl alone when the column is missing.
        cols = {c["name"] for c in _rows(conn, "PRAGMA table_info(trades)")}
        pnl_expr = ("realized_pnl + COALESCE(partial_realized_pnl, 0)"
                    if "partial_realized_pnl" in cols else "realized_pnl")
        rows = _rows(conn, f"SELECT {pnl_expr} AS pnl FROM trades WHERE {clause}", tuple(params))
        pnls = [float(r["pnl"]) for r in rows]
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


def recent_movers(path: str | Path | None = None) -> dict[str, Any]:
    """Latest absolute-USDT-band mover shortlist (shadow research only)."""
    import json

    p = Path(path) if path else REPO_ROOT / "data" / "mover_shortlist_latest.json"
    if not p.exists():
        raise WarehouseError(
            f"mover shortlist not found at {p}. Bot must complete ≥1 shadow "
            "universe scan with BROAD_UNIVERSE_MONITOR enabled."
        )
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise WarehouseError(f"mover shortlist unreadable: {e}") from e
    if not isinstance(doc, dict):
        raise WarehouseError(
            f"mover shortlist corrupt at {p}: expected JSON object, got "
            f"{type(doc).__name__}"
        )
    return doc


def _f1_reason_family(reason: str) -> str:
    """Collapse noisy per-print gate reasons into operator families."""
    r = str(reason or "unknown")
    if r.startswith("trailing_funding_mean"):
        return "trailing_funding_mean_le_0"
    if r.startswith("funding_rate"):
        return "funding_rate_le_0"
    if r.startswith("time_to_next_funding"):
        return "time_window"
    if r.startswith("feeds_stale"):
        return "feeds_stale"
    if r.startswith("spot_spread"):
        return "spot_spread"
    if r.startswith("perp_spread"):
        return "perp_spread"
    if r.startswith("perp_mark") and "< spot_mid" in r:
        return "contango_fail"
    if r.startswith("edge "):
        return "edge_below_cost"
    if r == "no_snapshot":
        return "no_snapshot"
    if "trailing_funding_history" in r:
        return "trailing_history_incomplete"
    return r[:80]


def f1_edge_status(
    path: str | Path | None = None, *, lookback_hours: float = 24.0
) -> dict[str, Any]:
    """Summarize F1 carry gate-log over the last lookback window (read-only).

    Distinguishes runner-never-wrote (missing file → WarehouseError), stale
    runner (file exists but no rows in window / old mtime), and idle-no-edge
    (checks>0, ok=0) so operators do not confuse compressed funding with a
    dead F1 process.
    """
    import json
    import time

    p = Path(path) if path else REPO_ROOT / "data" / "carry_gate_log.jsonl"
    if not p.exists():
        raise WarehouseError(
            f"carry_gate_log not found at {p}. F1 runner has not written yet."
        )
    now = time.time()
    cut = now - float(lookback_hours) * 3600.0
    n = 0
    ok = 0
    best: dict[str, Any] | None = None
    reasons: dict[str, int] = {}
    reason_families: dict[str, int] = {}
    feeds_fresh_true = 0
    feeds_fresh_false = 0
    last_ts = 0.0
    try:
        mtime = p.stat().st_mtime
        with p.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    ts = float(row.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if ts > last_ts:
                    last_ts = ts
                if ts < cut:
                    continue
                n += 1
                reason = str(row.get("reason") or "unknown")
                reasons[reason] = reasons.get(reason, 0) + 1
                family = _f1_reason_family(reason)
                reason_families[family] = reason_families.get(family, 0) + 1
                if "feeds_fresh" in row:
                    if row.get("feeds_fresh"):
                        feeds_fresh_true += 1
                    else:
                        feeds_fresh_false += 1
                is_ok = bool(row.get("ok") or row.get("f1_gate_ok"))
                if is_ok:
                    ok += 1
                try:
                    edge_f = float(row.get("net_edge_bps"))
                except (TypeError, ValueError):
                    continue
                if best is None or edge_f > float(best.get("net_edge_bps") or -1e18):
                    best = {
                        "symbol": row.get("symbol"),
                        "venue": row.get("venue"),
                        "net_edge_bps": edge_f,
                        "ok": is_ok,
                        "reason": reason,
                        "ts": ts,
                    }
    except OSError as e:
        raise WarehouseError(f"carry_gate_log unreadable: {e}") from e
    age_sec = max(0.0, now - (last_ts if last_ts > 0 else mtime))
    stale = age_sec > float(lookback_hours) * 3600.0
    if n == 0 and stale:
        status = "stale_runner"
    elif n == 0:
        status = "no_checks_in_window"
    elif ok > 0:
        status = "passing"
    else:
        status = "idle_no_edge"
    top_reasons = sorted(reasons.items(), key=lambda kv: -kv[1])[:8]
    top_families = sorted(reason_families.items(), key=lambda kv: -kv[1])[:8]
    feeds_n = feeds_fresh_true + feeds_fresh_false
    return {
        "lookback_hours": lookback_hours,
        "checks": n,
        "ok": ok,
        "ok_rate": (ok / n) if n else 0.0,
        "best": best,
        "top_reject_reasons": [
            {"reason": r, "count": c} for r, c in top_reasons
        ],
        "top_reject_families": [
            {"family": f, "count": c} for f, c in top_families
        ],
        "feeds_fresh_true": feeds_fresh_true,
        "feeds_fresh_false": feeds_fresh_false,
        "feeds_fresh_rate": (feeds_fresh_true / feeds_n) if feeds_n else None,
        "status": status,
        "last_check_ts": last_ts if last_ts > 0 else None,
        "log_age_sec": round(age_sec, 1),
        "note": (
            "F1 is the only ledger-validated live profit family; "
            "status=idle_no_edge means compressed funding / negative net edge "
            "(idle is correct). status=stale_runner means the gate log is older "
            "than lookback — alert on that, not on idle_no_edge. "
            "feeds_fresh_rate near 0 with non-stale runner suggests a snapshot "
            "freshness wiring bug, not compressed funding."
        ),
    }


def open_funnel_status(
    db_path: str | Path | None = None, *, lookback_hours: float = 24.0
) -> dict[str, Any]:
    """Denominator-aware OPEN-attempt funnel from ``decision_events`` (read-only).

    Distinguishes "no candidates reached execute_open" from "candidates reached
    the economic gate and were refused" so EconGate=strict idle is measurable
    without mistaking score-floor starvation for model-gate blocks.
    """
    import json
    import time
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not path.exists():
        raise WarehouseError(f"warehouse not found at {path}")
    hours = float(lookback_hours)
    if hours < 1.0 or hours > 168.0:
        raise WarehouseError("lookback_hours must be in [1, 168]")
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    cut_iso = cut.strftime("%Y-%m-%dT%H:%M:%S")
    reasons: Counter[str] = Counter()
    econ_reasons: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    open_attempts = 0
    filled = 0
    econ_blocked = 0
    other_rejected = 0
    deferred = 0
    try:
        conn = _connect(path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM decision_events "
                "WHERE occurred_at >= ? ORDER BY occurred_at DESC",
                (cut_iso,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise WarehouseError(f"decision_events query failed: {e}") from e

    for (payload,) in rows:
        try:
            doc = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        ctx = doc.get("context") if isinstance(doc.get("context"), dict) else {}
        stage = str(ctx.get("terminal_stage") or "")
        action = str(doc.get("action") or "").lower()
        # OPEN-path terminals only (execute_open / maker_resolution). Legacy
        # rows may omit stage — accept enter_* actions in that case.
        if stage in ("execute_open", "maker_resolution"):
            pass
        elif not stage and action.startswith("enter_"):
            pass
        else:
            continue
        reason = str(
            ctx.get("reason")
            or ((doc.get("reason_codes") or [None])[0])
            or "unspecified"
        )
        outcome = str(
            doc.get("outcome") or ctx.get("terminal_outcome") or "unknown"
        ).lower()
        open_attempts += 1
        reasons[reason] += 1
        if reason.startswith("universe_filter_blocked"):
            reasons["_family:universe_filter_blocked"] += 1
        elif reason.startswith("band_regime_filter"):
            reasons["_family:band_regime_filter"] += 1
        outcomes[outcome] += 1
        if outcome == "filled":
            filled += 1
        elif outcome == "deferred":
            deferred += 1
        elif reason.startswith("economic_gate"):
            econ_blocked += 1
            econ_reasons[reason] += 1
        else:
            other_rejected += 1

    denom = open_attempts
    top = [
        {"reason": r, "count": c}
        for r, c in reasons.most_common(15)
        if not str(r).startswith("_family:")
    ]
    families = [
        {"family": str(r)[len("_family:") :], "count": c}
        for r, c in reasons.most_common()
        if str(r).startswith("_family:")
    ]
    fam_counts = {f["family"]: f["count"] for f in families}
    fam_counts["economic_gate"] = econ_blocked
    dominant = max(fam_counts, key=fam_counts.get) if fam_counts and denom else None
    if denom == 0:
        drought = "no_open_attempts"
    elif filled > 0 and filled >= denom * 0.05:
        drought = "trading"
    elif dominant == "band_regime_filter":
        drought = "band_regime_idle"
    elif dominant == "universe_filter_blocked":
        drought = "universe_chop_or_liquidity"
    elif econ_blocked >= max(other_rejected, filled, 1):
        drought = "econ_gate_strict"
    else:
        drought = "mixed_blocks"

    return {
        "lookback_hours": hours,
        "open_attempts": open_attempts,
        "filled": filled,
        "deferred": deferred,
        "econ_blocked": econ_blocked,
        "other_rejected": other_rejected,
        "econ_block_rate": (econ_blocked / denom) if denom else 0.0,
        "fill_rate": (filled / denom) if denom else 0.0,
        "outcomes": dict(outcomes),
        "econ_reasons": [
            {"reason": r, "count": c} for r, c in econ_reasons.most_common(8)
        ],
        "top_reject_reasons": top,
        "top_reject_families": families,
        "drought_status": drought,
        "status": (
            "no_open_attempts"
            if denom == 0
            else (
                "econ_gate_dominant"
                if econ_blocked > 0 and econ_blocked >= max(other_rejected, filled)
                else "mixed"
                if econ_blocked > 0
                else "passing_or_other_blocks"
            )
        ),
        "note": (
            "Denominator = terminal OPEN attempts in decision_events "
            "(execute_open / maker_resolution). drought_status=band_regime_idle "
            "means BAND_REGIME_FILTER correctly refuses toxic ADX/vol buckets "
            "(not a hung process). F1 carry is separate — see "
            "trading_bot_f1_edge_status."
        ),
        "asof_unix": time.time(),
    }
