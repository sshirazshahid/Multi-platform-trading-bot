"""Sprint KPI bucket reporter — read-only.

Prints exit-reason rollup over a user-supplied window. Used for sprint
progress measurement and the Day-8 interim signal check per design spec
docs/superpowers/specs/2026-05-18-bleed-fix-sprint-and-rebuild-design.md
§7.2 / §7.4.

Categorizes each CLOSED trade into one of seven buckets:
    WIN         — mcp_take_profit, trailing_stop, partial_take_profit
    GHOST       — ghost_sync, ghost_reconciled, ghost_force_close,
                  ghost_reroute_exhausted (Patch #1)
    AGE         — AGE_LIMIT, AGE_LOSS, STALE, plus *_post_grace siblings
                  (Patch #3 audit signal)
    SOFT_OTHER  — systematic_close, sl_failed, sl_placement_failed
    SL          — stop_loss
    AGE_TEXT    — any other exit_reason containing "age" or "cutoff"
                  (free-text variants)
    OTHER       — everything else

Read-only. No schema changes. No writes.

Usage:
    python scripts/sprint_kpi.py --since 2026-05-19 [--until 2026-05-26]
    python scripts/sprint_kpi.py --since 2026-04-19 --markdown
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone

WIN = ("mcp_take_profit", "trailing_stop", "partial_take_profit")
GHOST = (
    "ghost_sync",
    "ghost_reconciled",
    "ghost_force_close",
    "ghost_reroute_exhausted",
)
AGE_CLEAN = (
    "AGE_LIMIT",
    "AGE_LOSS",
    "STALE",
    "STALE_post_grace",
    "AGE_LIMIT_post_grace",
    "AGE_LOSS_post_grace",
)
SOFT_OTHER = ("systematic_close", "sl_failed", "sl_placement_failed")
SL = ("stop_loss",)

BUCKET_ORDER = ("WIN", "GHOST", "AGE", "AGE_TEXT", "SOFT_OTHER", "SL", "OTHER")


def bucket_for_reason(er: str | None) -> str:
    """Categorize an exit_reason string into a bucket key.

    Order of checks matters: canonical-set membership wins over the
    free-text AGE_TEXT fallback, so e.g. "AGE_LIMIT" buckets to AGE
    (not AGE_TEXT) even though it contains "age".
    """
    if er in WIN:
        return "WIN"
    if er in GHOST:
        return "GHOST"
    if er in AGE_CLEAN:
        return "AGE"
    if er in SOFT_OTHER:
        return "SOFT_OTHER"
    if er in SL:
        return "SL"
    if er and ("age" in er.lower() or "cutoff" in er.lower()):
        return "AGE_TEXT"
    return "OTHER"


def _parse_date(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--until", default=None,
                    help="YYYY-MM-DD (UTC, default: now)")
    ap.add_argument("--warehouse", default="data/warehouse.sqlite",
                    help="Path to warehouse.sqlite (default: data/warehouse.sqlite)")
    ap.add_argument("--markdown", action="store_true",
                    help="Emit markdown table suitable for reports/")
    args = ap.parse_args()

    since = _parse_date(args.since)
    until = _parse_date(args.until) if args.until else time.time()

    conn = sqlite3.connect(args.warehouse)
    try:
        buckets = {k: 0.0 for k in BUCKET_ORDER}
        counts = {k: 0 for k in BUCKET_ORDER}
        n_total = 0

        for er, pnl in conn.execute(
            "SELECT exit_reason, realized_pnl FROM trades "
            "WHERE status='CLOSED' AND ts_entry >= ? AND ts_entry <= ?",
            (since, until),
        ):
            n_total += 1
            pnl = pnl or 0.0
            bucket = bucket_for_reason(er)
            buckets[bucket] += pnl
            counts[bucket] += 1
    finally:
        conn.close()

    total = sum(buckets.values())
    days = (until - since) / 86400.0
    until_label = args.until or "now"

    if args.markdown:
        print(f"# Sprint KPI report  {args.since}  ->  {until_label}")
        print()
        print(f"Window: {days:.1f} days   n_trades: {n_total}")
        print()
        print("| Bucket | $ | n |")
        print("|---|---:|---:|")
        for k in BUCKET_ORDER:
            print(f"| {k} | {buckets[k]:+.2f} | {counts[k]} |")
        print(f"| **TOTAL** | **{total:+.2f}** | **{n_total}** |")
    else:
        print(f"=== Sprint KPI {args.since} -> {until_label} "
              f"({days:.1f}d, n={n_total}) ===")
        for k in BUCKET_ORDER:
            print(f"  {k:11} ${buckets[k]:+8.2f}  n={counts[k]}")
        print(f"  {'TOTAL':11} ${total:+8.2f}  n={n_total}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
