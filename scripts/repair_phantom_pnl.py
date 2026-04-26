"""Phase 3 — scrub the phantom-PnL trail Bug 2A wrote to the warehouse.

Bug 2A's `reconcile_closed_pnl` synthesised `entry_px=1.0, size=1.0` on
trades where Binance returned realized income with no fill context. The
dollar amount in `realized_pnl` IS real (Binance income is trusted), but
any percentage-derived stat (WR%, expectancy %, Kelly r-multiple) computed
from those rows is bogus.

This script:

1. Identifies CLOSED trades with the synthesis fingerprint
   (`entry_px=1.0 AND size=1.0 AND realized_pnl < -2.0`) whose
   `exit_reason` has not yet been re-annotated.
2. Sets `exit_reason='reconciled_no_context_repaired'` on each so any
   downstream consumer can skip the row when computing pct-derived stats.
   Dollar `realized_pnl` is left untouched.
3. Backs up `data/kelly_stats.json`, then resets it to ``{}``.
4. Re-runs `LearningEngine().learn(force=True)` to regenerate
   `data/knowledge_model.json` from the cleaned warehouse.

Idempotent: re-running on an already-repaired warehouse repairs 0 rows
(the WHERE clause excludes rows already tagged `reconciled_no_context_repaired`).

Usage:
    # Dry run (count only, no writes)
    python scripts/repair_phantom_pnl.py
    # Commit (annotate + reset kelly + relearn)
    python scripts/repair_phantom_pnl.py --commit
    # Annotate only, skip the LearningEngine step (faster for testing)
    python scripts/repair_phantom_pnl.py --commit --skip-learning-rerun
    # Cap (testing)
    python scripts/repair_phantom_pnl.py --commit --limit 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.warehouse import Warehouse, get_warehouse  # noqa: E402

# Module-level constants the test suite can monkeypatch.
DATA_DIR = ROOT / "data"
REPAIRED_TAG = "reconciled_no_context_repaired"
PHANTOM_PNL_THRESHOLD = -2.0   # rows with realized_pnl < this are repair candidates


def _identify_phantom_rows(wh: Warehouse, limit: int | None) -> list[dict]:
    sql = (
        "SELECT id, realized_pnl, entry_px, exit_px, size, symbol, ts_entry "
        "FROM trades "
        "WHERE status = 'CLOSED' "
        "  AND realized_pnl < ? "
        "  AND entry_px = 1.0 "
        "  AND size = 1.0 "
        "  AND (exit_reason IS NULL OR exit_reason != ?) "
        "ORDER BY ts_entry"
    )
    params: list = [PHANTOM_PNL_THRESHOLD, REPAIRED_TAG]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return wh.query(sql, params)


def _annotate(wh: Warehouse, trade_ids: list[int]) -> None:
    if not trade_ids:
        return
    # Build placeholder list — sqlite has a 999-param ceiling but our worst-case
    # is dozens of rows.
    placeholders = ",".join("?" * len(trade_ids))
    sql = (
        f"UPDATE trades SET exit_reason = ? "
        f"WHERE id IN ({placeholders})"
    )
    wh._conn().execute(sql, [REPAIRED_TAG, *trade_ids])


def _backup_and_reset_kelly(data_dir: Path) -> Path | None:
    """Back up kelly_stats.json then truncate to ``{}``. Returns backup path
    if a backup was made, else None (file did not exist)."""
    src = data_dir / "kelly_stats.json"
    if not src.exists():
        # Nothing to back up; still write an empty {} so downstream
        # consumers see a clean slate.
        data_dir.mkdir(parents=True, exist_ok=True)
        src.write_text("{}")
        return None
    ts = time.strftime("%Y%m%dT%H%M%S")
    bak = data_dir / f"kelly_stats.bak.{ts}.json"
    bak.write_text(src.read_text())
    src.write_text("{}")
    return bak


def _rerun_learning() -> None:
    """Regenerate knowledge_model.json + learning_report.html from cleaned warehouse."""
    # Imported lazily so test runs that pass --skip-learning-rerun avoid the
    # heavy import surface (pandas, etc).
    from core.learning_engine import LearningEngine

    LearningEngine().learn(force=True)


def repair(
    warehouse: Warehouse | None = None,
    commit: bool = False,
    limit: int | None = None,
    skip_learning_rerun: bool = False,
) -> int:
    """Repair phantom-PnL rows. Returns the count of rows repaired.

    With ``commit=False`` the count of would-have-repaired rows is returned
    but no warehouse / kelly / knowledge writes occur.
    """
    wh = warehouse if warehouse is not None else get_warehouse()

    rows = _identify_phantom_rows(wh, limit=limit)
    n = len(rows)
    if not commit:
        print(
            f"Repair: identified {n} phantom-PnL rows below "
            f"realized_pnl < {PHANTOM_PNL_THRESHOLD}. DRY-RUN (no writes)."
        )
        return n

    if n == 0:
        print("Repair: 0 phantom-PnL rows to repair (warehouse is clean).")
        return 0

    trade_ids = [int(r["id"]) for r in rows]
    _annotate(wh, trade_ids)

    # Verify the annotation took.
    after_repaired = wh.query(
        "SELECT COUNT(*) c FROM trades WHERE exit_reason = ?",
        (REPAIRED_TAG,),
    )[0]["c"]
    after_unrepaired = wh.query(
        "SELECT COUNT(*) c FROM trades "
        "WHERE status='CLOSED' AND realized_pnl < ? "
        "  AND entry_px = 1.0 AND size = 1.0 "
        "  AND (exit_reason IS NULL OR exit_reason != ?)",
        (PHANTOM_PNL_THRESHOLD, REPAIRED_TAG),
    )[0]["c"]

    print(
        f"Repair: annotated {n} rows with exit_reason='{REPAIRED_TAG}'. "
        f"Total tagged in warehouse now: {after_repaired}. "
        f"Remaining un-repaired phantom rows: {after_unrepaired}."
    )

    bak = _backup_and_reset_kelly(DATA_DIR)
    if bak is not None:
        print(f"Repair: backed up kelly_stats.json -> {bak.name}, reset to {{}}.")
    else:
        print("Repair: kelly_stats.json did not exist; wrote empty {} stub.")

    if skip_learning_rerun:
        print("Repair: --skip-learning-rerun set; not regenerating knowledge_model.json.")
    else:
        print("Repair: re-running LearningEngine to regenerate knowledge_model.json...")
        try:
            _rerun_learning()
            print("Repair: LearningEngine.learn(force=True) completed.")
        except Exception as e:
            print(f"Repair: LearningEngine rerun FAILED: {e}. "
                  "Warehouse + kelly_stats are repaired; rerun learning manually.")

    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true",
                        help="Persist annotations + reset kelly + rerun learning "
                             "(default: dry run, count only)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Repair at most N rows (testing)")
    parser.add_argument("--skip-learning-rerun", action="store_true",
                        help="Skip LearningEngine().learn() at the end "
                             "(annotate only, faster)")
    args = parser.parse_args()
    n = repair(
        commit=args.commit,
        limit=args.limit,
        skip_learning_rerun=args.skip_learning_rerun,
    )
    sys.exit(0 if n >= 0 else 1)
