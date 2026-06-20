"""One-time repair: backfill orphan reconciled_* closes into the warehouse.

Context (2026-05-25 no-edge-forensics audit): before the Bug 1 fix,
`position_tracker.reconcile_closed_pnl` appended reconcile-imported closes
directly to `_closed` (positions.json) without ever writing a warehouse
row. Result: ~89 `reconciled_from_exchange` / `reconciled_no_context`
trades exist in data/positions.json with NO corresponding warehouse row.

This script reads those orphan rows from positions.json and writes a
complete CLOSED row to the warehouse via the idempotent warehouse API,
so the warehouse and positions.json agree. The going-forward leak is
already fixed in reconcile_closed_pnl; this only repairs historical rows.

These rows are reconcile-imports (strategy_family='reconciled_exchange'),
which analytics already EXCLUDE from "real bot trade" WR/PF aggregations —
so this repair restores warehouse completeness, it does NOT change any
performance metric.

Usage:
    python scripts/backfill_reconciled_warehouse.py            # dry-run preview
    python scripts/backfill_reconciled_warehouse.py --commit   # apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSITIONS = ROOT / "data" / "positions.json"


def _is_reconciled(close_reason: str) -> bool:
    return close_reason in ("reconciled_from_exchange", "reconciled_no_context")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--commit", action="store_true", help="Actually write to the warehouse. Default: dry-run."
    )
    args = ap.parse_args()

    if not POSITIONS.exists():
        print(f"positions.json not found: {POSITIONS}", file=sys.stderr)
        return 1

    data = json.loads(POSITIONS.read_text(encoding="utf-8"))
    closed = data.get("closed", []) or []
    orphans = [p for p in closed if _is_reconciled(p.get("close_reason") or "")]
    print(f"positions.json closed rows: {len(closed)}")
    print(f"reconciled_* candidates:    {len(orphans)}")
    if not orphans:
        print("nothing to backfill.")
        return 0

    # Import the warehouse API lazily (writes go through the idempotent path).
    sys.path.insert(0, str(ROOT))
    from core.warehouse import get_warehouse

    wh = get_warehouse()
    conn = wh._conn()

    to_write = []
    already = 0
    skipped_bad = 0
    for p in orphans:
        ex = (p.get("exchange") or "").lower()
        sym = p.get("symbol", "")
        side = p.get("side", "")
        ct = float(p.get("close_time") or 0)
        ot = float(p.get("open_time") or (max(ct - 60, 0) if ct else 0))
        pnl = float(p.get("pnl") or 0)
        entry = float(p.get("entry_price") or 0)
        size = float(p.get("size") or 0)
        exit_p = float(p.get("exit_price") or 0)
        lev = int(p.get("leverage") or 1)
        reason = p.get("close_reason") or "reconciled_from_exchange"
        if not (ex and sym and side and ct > 0):
            skipped_bad += 1
            continue
        # Dedup: skip if a warehouse row already exists for this
        # exchange+symbol+side within ±120s of the open time (matches the
        # warehouse's own idempotency window + tolerance).
        hit = conn.execute(
            "SELECT id FROM trades WHERE exchange=? AND symbol=? AND side=? "
            "AND ts_entry BETWEEN ? AND ?",
            (ex, sym, side, ot - 120, ot + 120),
        ).fetchone()
        if hit:
            already += 1
            continue
        to_write.append(
            dict(
                ex=ex,
                sym=sym,
                side=side,
                ot=ot,
                ct=ct,
                pnl=pnl,
                entry=entry,
                size=size,
                exit_p=exit_p,
                lev=lev,
                reason=reason,
            )
        )

    print(f"  already in warehouse: {already}")
    print(f"  skipped (missing key fields): {skipped_bad}")
    print(f"  to write: {len(to_write)}")
    print()
    for w in to_write[:30]:
        ts = datetime.fromtimestamp(w["ct"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        print(f"  {w['ex']:<8} {w['sym']:<22} {w['side']:<5} pnl={w['pnl']:+.4f}  closed={ts}")
    if len(to_write) > 30:
        print(f"  ... and {len(to_write) - 30} more")

    if not args.commit:
        print()
        print("DRY RUN — no changes. Re-run with --commit to apply.")
        return 0

    written = 0
    for w in to_write:
        try:
            tid = wh.record_trade_open(
                exchange=w["ex"],
                symbol=w["sym"],
                side=w["side"],
                ts_entry=w["ot"],
                entry_px=w["entry"] if w["entry"] > 0 else 0.0,
                size=w["size"] if w["size"] > 0 else 0.0,
                leverage=w["lev"],
                market_type="futures",
                strategy_family="reconciled_exchange",
                mode="CONTROLLED_LIVE",
            )
            if tid and tid > 0:
                wh.record_trade_close(
                    trade_id=tid,
                    ts_exit=w["ct"],
                    exit_px=w["exit_p"] if w["exit_p"] > 0 else 0.0,
                    realized_pnl=w["pnl"],
                    exit_reason=w["reason"],
                )
                written += 1
        except Exception as e:
            print(f"  ERROR {w['sym']}: {e}", file=sys.stderr)
    print()
    print(f"wrote {written} reconciled rows to warehouse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
