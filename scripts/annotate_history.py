"""
scripts/annotate_history.py — walk closed trades and generate ClaudeCode
historical_annotation reviews for every row that doesn't already have one.

Rate-limited by default (batch size 20) so the owner doesn't burn through
the Claude Max daily cap. Use --all to process every unannotated row.

Outputs:
 - warehouse.claude_reviews rows (one per annotated trade)
 - data/claude_schema_failures.jsonl if any responses fail validation

Design choices:
 - Reads trades via `warehouse.unannotated_trades()` — already defined to
   join on claude_reviews with task='historical_annotation'.
 - Failure-tolerant: any single trade failing never aborts the batch.
 - Stateless: re-runs are safe; already-annotated rows are filtered out
   by the warehouse join.

Usage:
    python scripts/annotate_history.py                # process 20 rows
    python scripts/annotate_history.py --limit 50     # custom batch
    python scripts/annotate_history.py --all          # drain the queue
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load warehouse without triggering core/__init__ (which imports the full bot)
import importlib.util as _il  # noqa: E402

_spec = _il.spec_from_file_location("warehouse", ROOT / "core" / "warehouse.py")
_mod = _il.module_from_spec(_spec)
sys.modules["warehouse"] = _mod
_spec.loader.exec_module(_mod)
Warehouse = _mod.Warehouse

# The advisor *does* need utils.claude_client + core.claude_schemas, so
# allow the real package import to happen; it doesn't require `schedule`
# unless BotEngine is instantiated.
from core.claude_advisor import call_advisory  # noqa: E402


def annotate(limit: int = 20, *, dry_run: bool = False) -> dict:
    wh = Warehouse()
    rows = wh.unannotated_trades(limit=limit)
    if not rows:
        print("[annotate] no unannotated closed trades")
        return {"annotated": 0, "failed": 0}

    ok = 0
    failed = 0
    for row in rows:
        tid = row.get("id")
        trade_payload = {
            "symbol":       row.get("symbol"),
            "exchange":     row.get("exchange"),
            "side":         row.get("side"),
            "entry_px":     row.get("entry_px"),
            "exit_px":      row.get("exit_px"),
            "realized_pnl": row.get("realized_pnl"),
            "r_multiple":   row.get("r_multiple"),
            "hold_sec":     row.get("hold_sec"),
            "exit_reason":  row.get("exit_reason"),
            "leverage":     row.get("leverage"),
            "strategy_family": row.get("strategy_family"),
        }

        if dry_run:
            print(f"[annotate] [dry] trade {tid} {row.get('symbol')} "
                  f"{row.get('side')} pnl={row.get('realized_pnl')}")
            continue

        print(f"[annotate] trade {tid} {row.get('symbol')} "
              f"{row.get('side')} -> calling Claude advisory...")
        out = call_advisory(
            "historical_annotation",
            {"TRADE_JSON": trade_payload},
            warehouse=wh, ref_type="trade", ref_id=int(tid),
        )
        if out is None:
            failed += 1
            print(f"[annotate] trade {tid} FAILED (see claude_schema_failures.jsonl)")
        else:
            ok += 1
            print(f"[annotate] trade {tid} -> {out.get('decision')} "
                  f"conf={out.get('confidence')}")

        # Gentle pacing to avoid hammering the CLI
        time.sleep(0.5)

    return {"annotated": ok, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all", action="store_true",
                    help="Drain the unannotated queue (batches of --limit).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be annotated; don't call Claude.")
    args = ap.parse_args()

    if not args.all:
        stats = annotate(limit=args.limit, dry_run=args.dry_run)
        print(f"[annotate] done: {stats}")
        return 0

    total = {"annotated": 0, "failed": 0}
    while True:
        stats = annotate(limit=args.limit, dry_run=args.dry_run)
        if not stats["annotated"] and not stats["failed"]:
            break
        for k in total:
            total[k] += stats.get(k, 0)
        if args.dry_run:
            break  # dry-run never advances the cursor
    print(f"[annotate] done (all): {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
