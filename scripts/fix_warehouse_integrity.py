"""scripts/fix_warehouse_integrity.py — 2026-05-27 data-integrity repair.

Two distinct repairs are performed in order, each with a dry-run preview:

  1. PHANTOM CLOSE: trade id=411 (XRP/USDT:USDT buy, 2026-04-21) is stuck
     status='OPEN' but is absent from positions.json and exchange_positions.json.
     The position was closed on-exchange 36+ days ago and the warehouse row was
     never updated.  We mark it CLOSED with exit_reason='phantom_stale' and
     realized_pnl=None (unknown — we do not fabricate a PnL figure).

  2. NULL r_multiple BACKFILL: 243 closed trades have r_multiple=NULL because
     _finalize_close only used pos.stop_loss, which is 0 on ghost-sync /
     reconcile / stale-close paths.  For the 21 trades that have a linked
     candidate row with stop_px we attempt to compute the exact side-aware
     R-multiple from (exit_px - entry_px) / (entry_px - stop_px).  However,
     the Apr 3–12 cohort (ids 67–160) stored the candidate stop_px on the
     WRONG side of entry (stop > entry for buys, stop < entry for sells) due
     to a pre-scoring-fix candidate-recording bug.  These 21 rows therefore
     all have an inverted denominator and are skipped — writing a sign-wrong
     r_multiple would corrupt attribution more than leaving it NULL.  For the
     remaining 222 trades there is no stop reference at all, so r_multiple
     stays NULL.  The code fix in order_manager._finalize_close ensures all
     FUTURE closes that have a valid candidate stop_px will write r_multiple
     correctly at close time.

Usage:
    python scripts/fix_warehouse_integrity.py          # dry run — prints what
                                                       # would change, writes nothing
    python scripts/fix_warehouse_integrity.py --apply  # apply changes to DB

The script is idempotent: running it twice with --apply produces no further
changes.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "warehouse.sqlite"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHANTOM_TRADE_ID = 411  # XRP/USDT:USDT buy 2026-04-21, confirmed phantom


def _compute_r_multiple(side: str, entry_px: float, exit_px: float, stop_px: float) -> float | None:
    """Side-aware R-multiple.  Returns None if the denominator is degenerate."""
    if side == "buy":
        denom = entry_px - stop_px
        if denom > 1e-12:
            return round((exit_px - entry_px) / denom, 3)
    else:
        denom = stop_px - entry_px
        if denom > 1e-12:
            return round((entry_px - exit_px) / denom, 3)
    return None


# ---------------------------------------------------------------------------
# Step 1 — phantom close
# ---------------------------------------------------------------------------


def preview_phantom(conn: sqlite3.Connection) -> bool:
    """Return True if the phantom trade is still OPEN."""
    row = conn.execute(
        "SELECT id, status, symbol, ts_entry FROM trades WHERE id = ?",
        (PHANTOM_TRADE_ID,),
    ).fetchone()
    if row is None:
        print(f"[phantom] trade id={PHANTOM_TRADE_ID} not found — nothing to do.")
        return False
    if row[1] != "OPEN":
        print(
            f"[phantom] trade id={PHANTOM_TRADE_ID} ({row[2]}) "
            f"already status={row[1]} — nothing to do."
        )
        return False
    print(
        f"[phantom] WILL close trade id={PHANTOM_TRADE_ID} "
        f"({row[2]}, ts_entry={row[3]}) "
        f"with exit_reason='phantom_stale', realized_pnl=NULL"
    )
    return True


def apply_phantom(conn: sqlite3.Connection) -> None:
    """Mark the phantom trade CLOSED."""
    conn.execute(
        """UPDATE trades
           SET status='CLOSED',
               ts_exit=?,
               exit_reason='phantom_stale',
               hold_sec=? - ts_entry
           WHERE id=? AND status='OPEN'""",
        (time.time(), time.time(), PHANTOM_TRADE_ID),
    )
    affected = conn.execute("SELECT changes()").fetchone()[0]
    if affected:
        print(f"[phantom] APPLIED: trade id={PHANTOM_TRADE_ID} marked CLOSED.")
    else:
        print("[phantom] No change (already closed or id missing).")


# ---------------------------------------------------------------------------
# Step 2 — r_multiple backfill via candidate stop_px
# ---------------------------------------------------------------------------


def collect_backfill_rows(conn: sqlite3.Connection) -> list[dict]:
    """Return trades that can receive an exact r_multiple from candidate.stop_px."""
    rows = conn.execute(
        """
        SELECT t.id, t.side, t.entry_px, t.exit_px, c.stop_px
        FROM trades t
        JOIN candidates c ON t.candidate_id = c.id
        WHERE t.status = 'CLOSED'
          AND t.r_multiple IS NULL
          AND c.stop_px IS NOT NULL
          AND t.entry_px IS NOT NULL
          AND t.exit_px IS NOT NULL
        ORDER BY t.id
        """
    ).fetchall()
    result = []
    for row in rows:
        tid, side, ep, xp, sl = row
        r_mult = _compute_r_multiple(side, ep, xp, sl)
        result.append(
            {
                "trade_id": tid,
                "side": side,
                "entry_px": ep,
                "exit_px": xp,
                "stop_px": sl,
                "r_multiple": r_mult,
            }
        )
    return result


def preview_backfill(rows: list[dict]) -> None:
    total = len(rows)
    computable = sum(1 for r in rows if r["r_multiple"] is not None)
    print(
        f"[backfill] {total} candidate-linked NULL r_multiple trades found; "
        f"{computable} have a computable R-multiple."
    )
    for r in rows:
        status = f"{r['r_multiple']:+.3f}" if r["r_multiple"] is not None else "SKIP (bad denom)"
        print(
            f"  trade_id={r['trade_id']:4d} {r['side']:5} "
            f"ep={r['entry_px']} sl={r['stop_px']} xp={r['exit_px']} "
            f"-> r_mult={status}"
        )


def apply_backfill(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Write computed r_multiples. Returns count of rows updated."""
    updated = 0
    for r in rows:
        if r["r_multiple"] is None:
            continue
        conn.execute(
            "UPDATE trades SET r_multiple = ? WHERE id = ? AND r_multiple IS NULL",
            (r["r_multiple"], r["trade_id"]),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0]
        updated += changed
    print(f"[backfill] APPLIED: {updated} r_multiple values written.")
    return updated


# ---------------------------------------------------------------------------
# Summary after apply
# ---------------------------------------------------------------------------


def print_summary(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
    null_r = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND r_multiple IS NULL"
    ).fetchone()[0]
    open_ct = conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]
    print()
    print("=== Post-fix warehouse summary ===")
    print(f"  CLOSED trades:      {total}")
    print(f"  NULL r_multiple:    {null_r}  ({null_r / total * 100:.1f}%)")
    print(f"  Has r_multiple:     {total - null_r}  ({(total - null_r) / total * 100:.1f}%)")
    print(f"  OPEN trades:        {open_ct}")
    # Attribution coverage
    attr = conn.execute("SELECT COUNT(*) FROM attribution").fetchone()[0]
    print(f"  Attribution rows:   {attr} / {total} closed ({attr / total * 100:.1f}%)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to the database (default is dry-run).",
    )
    args = parser.parse_args()
    dry = not args.apply

    if dry:
        print("DRY RUN — pass --apply to commit changes.\n")
    else:
        print("APPLYING changes to database.\n")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # Step 1
    phantom_needed = preview_phantom(conn)
    print()

    # Step 2
    backfill_rows = collect_backfill_rows(conn)
    preview_backfill(backfill_rows)
    print()

    if dry:
        print("Dry run complete — no changes written.")
        conn.close()
        return 0

    # Apply step 1
    if phantom_needed:
        apply_phantom(conn)

    # Apply step 2
    apply_backfill(conn, backfill_rows)

    conn.commit()
    print_summary(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
