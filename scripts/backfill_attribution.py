"""Backfill the `attribution` table for every closed trade.

Phase 1.2 of the quant rebuild plan. For historical trades, mid prices
were not captured at entry/exit, so this script treats `entry_px` and
`exit_px` as approximations of mid (i.e. fill ≈ mid). That zeros the
spread and slippage components on backfilled rows but preserves alpha
(= gross PnL = (exit_px − entry_px) × size × side_sign).

Going forward (Phase 1.4), `order_manager._finalize_close` captures the
actual top-of-book mid at entry and exit so newly closed trades get the
full decomposition.

Usage:
    # Dry run (count only, no writes)
    python scripts/backfill_attribution.py
    # Commit (write attribution rows)
    python scripts/backfill_attribution.py --commit
    # Limit (testing)
    python scripts/backfill_attribution.py --commit --limit 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.attribution import Trade, attribute, record  # noqa: E402
from core.warehouse import Warehouse, get_warehouse  # noqa: E402


def backfill(
    warehouse: Warehouse | None = None,
    commit: bool = False,
    limit: int | None = None,
) -> int:
    """Process every CLOSED trade not yet in `attribution`.

    Returns the number of trades processed. With ``commit=False``,
    computes attribution but does NOT write — useful for previews.
    """
    wh = warehouse if warehouse is not None else get_warehouse()
    sql = (
        "SELECT t.id, t.side, t.size, t.entry_px, t.exit_px, "
        "       t.fee, t.slippage, t.realized_pnl "
        "FROM trades t "
        "LEFT JOIN attribution a ON a.trade_id = t.id "
        "WHERE t.status = 'CLOSED' "
        "  AND a.trade_id IS NULL "
        "  AND t.entry_px IS NOT NULL "
        "  AND t.exit_px IS NOT NULL "
        "  AND t.size IS NOT NULL "
        "ORDER BY t.ts_entry"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = wh.query(sql)

    n_ok = 0
    n_err = 0
    for r in rows:
        try:
            t = Trade(
                side=str(r["side"]),
                size=float(r["size"]),
                entry_mid=float(r["entry_px"]),
                entry_fill=float(r["entry_px"]),  # mid ≈ fill (no separate mid stored)
                exit_mid=float(r["exit_px"]),
                exit_fill=float(r["exit_px"]),
                funding_paid=0.0,                 # not stored historically
                fees=float(r["fee"] or 0.0),
                slippage_modeled=0.0,             # already baked into px for paper trades
            )
            if commit:
                record(wh, trade_id=int(r["id"]), trade=t)
            else:
                attribute(t)  # dry-run: validate it computes; no write
            n_ok += 1
        except Exception as e:
            print(f"  ERROR trade {r['id']}: {e}")
            n_err += 1

    print(
        f"Backfill: scanned {len(rows)} trades, "
        f"{n_ok} attributed, {n_err} errors. "
        f"{'WROTE' if commit else 'DRY-RUN (no writes)'}"
    )
    if commit:
        n_attr = wh.query("SELECT COUNT(*) c FROM attribution")[0]["c"]
        print(f"  attribution table now holds {n_attr} rows")
    return n_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true",
                        help="Persist attribution rows (default: dry run)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N trades")
    args = parser.parse_args()
    backfill(commit=args.commit, limit=args.limit)
