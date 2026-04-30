"""Idempotent ghost/reconciliation-noise trimmer for data/positions.json.

Background — 2026-05-01 stop-the-bleed plan, change C
=====================================================
Over months of operation, `data/positions.json` accumulated 144 phantom
"closed" rows that distort dashboards, `analyze_trades.py` aggregates,
and any "load all closed" path that doesn't pre-filter. They came from:

  ghost_sync             — 109 rows  (bot's local close fallback when
                                       the real exchange-side close
                                       happened off-bot — usually
                                       carries fabricated/zero context)
  reconciled_no_context  —  30 rows  (Binance income-ledger imports
                                       lacking entry/size/exit_price;
                                       exchange dollar PnL kept but
                                       no per-trade context for stats)
  ghost_reconciled       —   5 rows  (ghost-sync that DID find a
                                       ledger record; carries real
                                       PnL but is structurally a
                                       reconciliation, not a bot trade)
  paper_ghost_cleanup    —   variable (DRY_RUN->LIVE leftover positions
                                       force-closed at startup)

What we KEEP
============
`reconciled_from_exchange` rows are kept — those carry genuine PnL
from operator-driven manual closes / API closes outside the bot's
runtime window. They're real trades; they just weren't initiated by
this bot's code.

Safety
======
1. Refuses to run if heartbeat.json is < 120s old (bot is live and
   actively writing positions.json — would race). Override with --force.
2. Always writes a timestamped backup before any mutation.
3. Idempotent: re-running on a clean file is a no-op.
4. Atomic write: temp file + rename, never leaves a half-written file.

Usage
=====
    python scripts/trim_ghost_positions.py            # dry-run
    python scripts/trim_ghost_positions.py --commit   # apply
    python scripts/trim_ghost_positions.py --commit --force   # ignore
                                                       # heartbeat
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POS_PATH = ROOT / "data" / "positions.json"
HB_PATH = ROOT / "data" / "heartbeat.json"

# Reasons whose rows are STRUCTURAL noise, not real trades.
GHOST_REASONS = frozenset({
    "ghost_sync",
    "ghost_reconciled",
    "reconciled_no_context",
    "paper_ghost_cleanup",
})


def heartbeat_age_seconds() -> float | None:
    if not HB_PATH.exists():
        return None
    try:
        hb = json.loads(HB_PATH.read_text(encoding="utf-8"))
        ts = hb.get("timestamp", "")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="Apply the trim (default is dry-run).")
    ap.add_argument("--force", action="store_true",
                    help="Bypass the live-bot heartbeat guard.")
    args = ap.parse_args()

    if not POS_PATH.exists():
        print(f"positions.json not found: {POS_PATH}", file=sys.stderr)
        return 1

    age = heartbeat_age_seconds()
    if args.commit and not args.force and age is not None and age < 120:
        print(
            f"REFUSED: heartbeat is {age:.0f}s old — bot appears to be "
            f"running and may overwrite our edits within seconds. Either "
            f"stop the bot first, or pass --force to override.",
            file=sys.stderr,
        )
        return 2

    pos = json.loads(POS_PATH.read_text(encoding="utf-8"))
    closed = pos.get("closed", []) or []
    open_pos = pos.get("open", []) or []

    keep: list[dict] = []
    drop_count: dict[str, int] = {}
    for c in closed:
        reason = (c.get("close_reason") or "").strip()
        # 2026-05-01: also drop paper_trade=True rows. They survived
        # from earlier DRY_RUN sessions and pollute aggregate stats
        # that don't pre-filter (analyze_trades.py, dashboard PnL,
        # learning_engine seeds). The bot is in CONTROLLED_LIVE
        # exclusively now — paper rows are pure noise.
        if c.get("paper_trade") is True:
            drop_count["paper_trade"] = drop_count.get("paper_trade", 0) + 1
            continue
        if reason in GHOST_REASONS:
            drop_count[reason] = drop_count.get(reason, 0) + 1
            continue
        keep.append(c)

    n_before = len(closed)
    n_after = len(keep)
    n_dropped = n_before - n_after

    print(f"positions.json closed rows: {n_before}")
    print(f"open positions (untouched):  {len(open_pos)}")
    print()
    if n_dropped == 0:
        print("Nothing to trim — file is already clean. (idempotent no-op)")
        return 0
    print(f"Would drop {n_dropped} ghost/reconciliation rows:")
    for reason, n in sorted(drop_count.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:30}  {n:>4}")
    print(f"After trim: {n_after} closed rows kept.")

    if not args.commit:
        print()
        print("DRY RUN — no changes made. Re-run with --commit to apply.")
        return 0

    # Backup before mutation.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup_path = POS_PATH.with_suffix(f".json.bak.{today}")
    # If a backup with today's date already exists (re-run same day),
    # add a unix-second suffix so we don't clobber the first one.
    if backup_path.exists():
        backup_path = POS_PATH.with_suffix(
            f".json.bak.{today}-{int(time.time())}")
    backup_path.write_bytes(POS_PATH.read_bytes())
    print()
    print(f"Backup written: {backup_path}")

    # Atomic write: temp file + rename.
    pos["closed"] = keep
    tmp_path = POS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(pos, indent=2), encoding="utf-8")
    tmp_path.replace(POS_PATH)
    print(f"Wrote: {POS_PATH}")
    print(f"Trimmed {n_dropped} rows ({n_before} -> {n_after}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
