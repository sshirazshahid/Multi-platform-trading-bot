"""Forward-accumulation runner for the derivatives harvester (Phase 3).

Run periodically (e.g. Windows Task Scheduler / cron, hourly) to build the
multi-month derivs history that a future leakage-clean falsification needs —
the Binance public endpoints only retain ~21 days, so history MUST be
accumulated forward. Each run appends a snapshot row per coin to
data/derivs_history.jsonl. Read-only w.r.t. the bot; touches no order path.

    python scripts/harvest_derivs.py
    python scripts/harvest_derivs.py BTC ETH SOL
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.data_sources.derivs import DerivsHarvester  # noqa: E402

HIST = ROOT / "data" / "derivs_history.jsonl"
DEFAULT_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA"]


def main() -> None:
    coins = sys.argv[1:] or DEFAULT_COINS
    h = DerivsHarvester()
    snap = h.snapshot(coins, force=True)
    fresh = {c: d for c, d in snap.items() if not d.get("stale")}
    n = DerivsHarvester.append_history(HIST, snap, fetched_at=time.time())
    print(f"[harvest_derivs] coins={len(coins)} fresh={len(fresh)} rows_appended={n} -> {HIST}")
    for c, d in snap.items():
        flag = "" if not d.get("stale") else "  [STALE]"
        print(
            f"  {c:<5} lsr={d['lsr']}  taker_ls={d['taker_ls']}  "
            f"oi_chg%={d['oi_chg_pct']}  funding={d['funding']}{flag}"
        )
    if HIST.exists():
        total = sum(1 for _ in HIST.open(encoding="utf-8"))
        print(f"[harvest_derivs] total history rows: {total}")


if __name__ == "__main__":
    main()
