"""scripts/decision_reconciliation.py — provenance consumer: decisions ↔ outcomes.

Decision Provenance Bundle consumer (tasks/plan_provenance_bundle_2026-06-12.md,
amendment 4 / S3; orphan taxonomy per Eng E-5/E-6). Read-only — never writes to
data/. Stdlib only.

Joins `decision_id` minted in data/mcp_decisions*.jsonl (active file plus
archive-renamed `mcp_decisions.<YYYYMMDD-HHMMSS>.jsonl`) against the warehouse
`trades` table and reports:

- per-source (claude|algo) joined trade count, WR and mean realized_pnl;
- orphans BOTH directions with exemption taxonomy:
  * decision-without-order exempts: non-OPEN action types (HOLD/TIGHTEN/monitor
    advice), decisions covered by a `{"type":"rejection"}` row, rejection rows
    themselves, and actions with no decision_id (pre-provenance);
  * order-without-decision exempts: NULL-decision_id trades whose
    strategy_family is whitelisted (reconciled_exchange/manual/dca/rebalance)
    or whose ts_entry predates the first instrumented decision row;
  * the remainder = TRUE orphans, listed;
- label quality: % NULL r_multiple among closed trades in the last --days days
  vs the 2% acceptance threshold (test-plan spec 20).

Exit code 0 on any data condition (degraded data is a report, not a failure);
nonzero only on crash.

Usage:
    python scripts/decision_reconciliation.py
        [--decisions-glob "data/mcp_decisions*.jsonl"]
        [--warehouse data/warehouse.sqlite]
        [--audit-failures data/claude_audit_failures.json]
        [--days 7] [--as-of EPOCH] [--json]
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

OPEN_TYPE = "OPEN"
EXEMPT_FAMILIES = {"reconciled_exchange", "manual", "dca", "rebalance"}
NULL_R_THRESHOLD_PCT = 2.0
# any of these keys on an action marks the row as provenance-instrumented
_PROVENANCE_KEYS = ("decision_id", "source", "repaired")
_TABLE_ORPHAN_CAP = 20

_TRADE_COLS = (
    "id",
    "ts_entry",
    "ts_exit",
    "symbol",
    "strategy_family",
    "realized_pnl",
    "r_multiple",
    "status",
    "decision_id",
    "exit_decision_id",
)


def _as_ts(v):
    """Epoch float from a float/int or ISO-8601 string; None when unparseable."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _iter_rows(paths):
    """Stream JSONL dict rows; skip unreadable files and invalid lines."""
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError:
            continue


def load_decisions(pattern: str) -> dict:
    """Stream every file matching `pattern` into join inputs + health counters."""
    paths = sorted(glob.glob(pattern))
    open_actions = []  # OPEN actions carrying a decision_id
    id_source = {}  # decision_id -> source (any action type)
    rejection_ids = set()
    rejection_count = 0
    repaired_count = 0  # rows repaired (row-level flag or any action flag)
    truncated_count = 0  # rows with truncation markers
    non_open_actions = 0
    open_no_id = 0
    first_ts = None  # earliest INSTRUMENTED row only (old rows must not count)
    instrumented = False
    n_rows = 0
    for row in _iter_rows(paths):
        n_rows += 1
        ts = _as_ts(row.get("ts"))
        row_instrumented = False
        if row.get("type") == "rejection":
            row_instrumented = True
            rejection_count += 1
            did = row.get("decision_id")
            if did is not None:
                rejection_ids.add(str(did))
        else:
            if "repaired" in row or "truncated" in row or "truncated_sections" in row:
                row_instrumented = True
            row_repaired = bool(row.get("repaired"))
            row_truncated = bool(row.get("truncated")) or bool(row.get("truncated_sections"))
            act_repaired = False
            actions = (row.get("decisions") or {}).get("actions") or []
            for a in actions:
                if not isinstance(a, dict):
                    continue
                if any(k in a for k in _PROVENANCE_KEYS):
                    row_instrumented = True
                did = a.get("decision_id")
                src = str(a.get("source") or "unknown")
                if did is not None:
                    id_source[str(did)] = src
                if a.get("repaired"):
                    act_repaired = True
                if str(a.get("type") or "").upper() == OPEN_TYPE:
                    if did is None:
                        open_no_id += 1
                    else:
                        open_actions.append(
                            {
                                "decision_id": str(did),
                                "symbol": a.get("symbol"),
                                "ts": ts,
                                "source": src,
                            }
                        )
                else:
                    non_open_actions += 1
            if row_repaired or act_repaired:
                repaired_count += 1
            if row_truncated:
                truncated_count += 1
        if row_instrumented:
            instrumented = True
            if ts is not None:
                first_ts = ts if first_ts is None else min(first_ts, ts)
    return {
        "files": [str(p) for p in paths],
        "n_rows": n_rows,
        "open_actions": open_actions,
        "id_source": id_source,
        "rejection_ids": rejection_ids,
        "rejection_count": rejection_count,
        "repaired_count": repaired_count,
        "truncated_count": truncated_count,
        "non_open_actions": non_open_actions,
        "open_no_id": open_no_id,
        "first_decision_ts": first_ts,
        "instrumented": instrumented,
    }


def load_trades(warehouse) -> list:
    """All trades rows, read-only. Columns missing from older schemas (e.g.
    decision_id pre-restart) are selected as NULL so the join degrades, not
    crashes. Raises on unreadable/absent database (callers decide)."""
    uri = f"file:{Path(warehouse).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        if not have:
            raise sqlite3.OperationalError("no trades table")
        sel = ", ".join(c if c in have else f"NULL AS {c}" for c in _TRADE_COLS)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT {sel} FROM trades")]
    finally:
        conn.close()


def label_quality(trades: list, days: float = 7.0, now=None) -> dict:
    """% NULL r_multiple among trades closed in the last `days` days (spec 20)."""
    now = time.time() if now is None else float(now)
    cutoff = now - days * 86400
    closed = [
        t
        for t in trades
        if str(t.get("status") or "") == "CLOSED"
        and float(t.get("ts_exit") or t.get("ts_entry") or 0) >= cutoff
    ]
    n = len(closed)
    if n == 0:
        return {
            "window_days": days,
            "closed_n": 0,
            "null_r_multiple": 0,
            "null_pct": None,
            "threshold_pct": NULL_R_THRESHOLD_PCT,
            "status": "NO_DATA",
        }
    nulls = sum(1 for t in closed if t.get("r_multiple") is None)
    pct = round(100.0 * nulls / n, 2)
    return {
        "window_days": days,
        "closed_n": n,
        "null_r_multiple": nulls,
        "null_pct": pct,
        "threshold_pct": NULL_R_THRESHOLD_PCT,
        "status": "PASS" if pct < NULL_R_THRESHOLD_PCT else "FAIL",
    }


def reconcile(decisions: dict, trades: list, days: float = 7.0, now=None) -> dict:
    """Join decisions to trades; emit per-source stats + orphan taxonomy."""
    id_source = decisions["id_source"]
    trade_ids = {str(t["decision_id"]) for t in trades if t.get("decision_id")}

    # per-source outcomes on JOINED trades
    per_source = {}
    joined = 0
    for t in trades:
        did = t.get("decision_id")
        if did is None or str(did) not in id_source:
            continue
        joined += 1
        s = per_source.setdefault(
            id_source[str(did)], {"n": 0, "closed": 0, "wins": 0, "pnl_sum": 0.0}
        )
        s["n"] += 1
        if str(t.get("status") or "") == "CLOSED" and t.get("realized_pnl") is not None:
            pnl = float(t["realized_pnl"])
            s["closed"] += 1
            s["pnl_sum"] += pnl
            if pnl > 0:
                s["wins"] += 1
    for s in per_source.values():
        s["wr_pct"] = round(100.0 * s["wins"] / s["closed"], 2) if s["closed"] else None
        s["mean_pnl"] = round(s["pnl_sum"] / s["closed"], 4) if s["closed"] else None
        s["pnl_sum"] = round(s["pnl_sum"], 4)
    exit_joined = sum(
        1
        for t in trades
        if t.get("exit_decision_id") is not None and str(t["exit_decision_id"]) in id_source
    )

    # decision → order orphans (OPEN actions only; see module docstring taxonomy)
    rejection_covered = 0
    true_dwo = []
    for a in decisions["open_actions"]:
        if a["decision_id"] in trade_ids:
            continue
        if a["decision_id"] in decisions["rejection_ids"]:
            rejection_covered += 1
            continue
        true_dwo.append(a)

    # order → decision orphans
    first_ts = decisions["first_decision_ts"]
    fam_exempt = 0
    pre_provenance = 0
    true_owd = []
    for t in trades:
        did = t.get("decision_id")
        if did is not None:
            if str(did) not in id_source:
                true_owd.append(
                    {
                        "trade_id": t.get("id"),
                        "symbol": t.get("symbol"),
                        "ts_entry": t.get("ts_entry"),
                        "decision_id": str(did),
                    }
                )
            continue
        if str(t.get("strategy_family") or "").lower() in EXEMPT_FAMILIES:
            fam_exempt += 1
            continue
        ts_e = t.get("ts_entry")
        # no instrumented decision rows at all -> everything is pre-provenance
        if first_ts is None or (ts_e is not None and float(ts_e) < first_ts):
            pre_provenance += 1
            continue
        true_owd.append(
            {
                "trade_id": t.get("id"),
                "symbol": t.get("symbol"),
                "ts_entry": ts_e,
                "decision_id": None,
            }
        )

    return {
        "files_scanned": len(decisions["files"]),
        "decision_rows": decisions["n_rows"],
        "first_decision_ts": first_ts,
        "per_source": per_source,
        "joined_trades": joined,
        "exit_joined_trades": exit_joined,
        "orphans": {
            "decision_without_order": {
                "open_candidates": len(decisions["open_actions"]),
                "exempt": {
                    "non_open_actions": decisions["non_open_actions"],
                    "rejection_rows": decisions["rejection_count"],
                    "rejection_covered": rejection_covered,
                    "no_decision_id": decisions["open_no_id"],
                },
                "true_count": len(true_dwo),
                "true": true_dwo,
            },
            "order_without_decision": {
                "exempt": {
                    "family_whitelist": fam_exempt,
                    "pre_provenance": pre_provenance,
                },
                "true_count": len(true_owd),
                "true": true_owd,
            },
            "true_total": len(true_dwo) + len(true_owd),
        },
        "label_quality": label_quality(trades, days=days, now=now),
    }


def read_audit_failures(path) -> int | None:
    """`count` from data/claude_audit_failures.json; None when absent/invalid."""
    try:
        return int(json.loads(Path(path).read_text(encoding="utf-8")).get("count", 0))
    except Exception:
        return None


def render_table(result: dict) -> str:
    lines = [
        "DECISION RECONCILIATION",
        f"  decision files: {result['files_scanned']}  "
        f"rows: {result['decision_rows']}  "
        f"first instrumented ts: {result['first_decision_ts']}",
        "",
    ]
    per = result["per_source"]
    lines.append("Per-source joined outcomes (entry decision_id -> trades):")
    if per:
        lines.append(f"  {'source':<10}{'n':>5}{'closed':>8}{'wins':>6}{'WR%':>8}{'mean_pnl':>12}")
        for src in sorted(per):
            s = per[src]
            wr = f"{s['wr_pct']:.1f}" if s["wr_pct"] is not None else "n/a"
            mp = f"{s['mean_pnl']:+.4f}" if s["mean_pnl"] is not None else "n/a"
            lines.append(f"  {src:<10}{s['n']:>5}{s['closed']:>8}{s['wins']:>6}{wr:>8}{mp:>12}")
    else:
        lines.append("  (no joined trades)")
    lines.append(
        f"  joined trades: {result['joined_trades']}  "
        f"exit-joined (CLOSE advice): {result['exit_joined_trades']}"
    )
    lines.append("")

    d = result["orphans"]["decision_without_order"]
    e = d["exempt"]
    lines.append(
        f"Decisions without orders: {d['true_count']} TRUE orphan(s) "
        f"(OPEN candidates {d['open_candidates']}; exempt: "
        f"non-OPEN {e['non_open_actions']}, "
        f"rejection rows {e['rejection_rows']}, "
        f"rejection-covered {e['rejection_covered']}, "
        f"no decision_id {e['no_decision_id']})"
    )
    for a in d["true"][:_TABLE_ORPHAN_CAP]:
        lines.append(
            f"  - decision_id={a['decision_id']} symbol={a.get('symbol')} "
            f"ts={a.get('ts')} source={a.get('source')}"
        )
    if d["true_count"] > _TABLE_ORPHAN_CAP:
        lines.append(f"  ... +{d['true_count'] - _TABLE_ORPHAN_CAP} more (use --json)")

    w = result["orphans"]["order_without_decision"]
    e2 = w["exempt"]
    lines.append(
        f"Orders without decisions: {w['true_count']} TRUE orphan(s) "
        f"(exempt: family whitelist {e2['family_whitelist']}, "
        f"pre-provenance {e2['pre_provenance']})"
    )
    for t in w["true"][:_TABLE_ORPHAN_CAP]:
        lines.append(
            f"  - trade_id={t['trade_id']} symbol={t.get('symbol')} "
            f"ts_entry={t.get('ts_entry')} decision_id={t.get('decision_id')}"
        )
    if w["true_count"] > _TABLE_ORPHAN_CAP:
        lines.append(f"  ... +{w['true_count'] - _TABLE_ORPHAN_CAP} more (use --json)")
    lines.append("")

    lq = result["label_quality"]
    if lq["status"] == "NO_DATA":
        lines.append(f"Label quality: no closed trades in last {lq['window_days']:g}d -> NO_DATA")
    else:
        lines.append(
            f"Label quality: {lq['null_r_multiple']}/{lq['closed_n']} closed "
            f"trades last {lq['window_days']:g}d NULL r_multiple = "
            f"{lq['null_pct']}% -> {lq['status']} "
            f"(threshold {lq['threshold_pct']:g}%)"
        )
    af = result.get("audit_write_failures")
    lines.append(f"Audit-write failures: {'n/a' if af is None else af}")
    if result.get("warehouse_error"):
        lines.append(f"Warehouse: UNREADABLE ({result['warehouse_error']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Join decision provenance to warehouse outcomes (read-only)."
    )
    ap.add_argument(
        "--decisions-glob",
        default="data/mcp_decisions*.jsonl",
        help="active + archive decision files",
    )
    ap.add_argument("--warehouse", default="data/warehouse.sqlite", type=Path)
    ap.add_argument("--audit-failures", default="data/claude_audit_failures.json", type=Path)
    ap.add_argument(
        "--days", default=7.0, type=float, help="label-quality window (NULL r_multiple check)"
    )
    ap.add_argument(
        "--as-of",
        default=None,
        type=float,
        help="epoch 'now' for the label-quality window (default: now)",
    )
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    decisions = load_decisions(args.decisions_glob)
    warehouse_error = None
    try:
        trades = load_trades(args.warehouse)
    except Exception as exc:  # degraded data is a report, not a failure
        trades, warehouse_error = [], str(exc)[:200]
    result = reconcile(decisions, trades, days=args.days, now=args.as_of)
    result["audit_write_failures"] = read_audit_failures(args.audit_failures)
    if warehouse_error:
        result["warehouse_error"] = warehouse_error
    print(json.dumps(result, indent=2, default=str) if args.as_json else render_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
