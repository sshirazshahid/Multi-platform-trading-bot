"""scripts/refresh_hour_gates.py — emit data-driven hour gate evidence.

Reads `data/warehouse.sqlite`, groups closed trades by entry hour (UTC),
and writes `data/hour_gate_evidence.json` with the set of hours the bot
should avoid. Consumed by `core/bot_engine._classify_hour` at runtime.

Block rule (and-conditions): n>=8 trades AND wr<35% AND total_pnl<-$3.
The threshold is intentionally conservative — empty `blocked` is fine
(bot falls through to static `ALLOWED_HOURS_UTC`).

Run weekly from `scripts/retrain_weekly.ps1`.

Usage:
    python scripts/refresh_hour_gates.py [--db data/warehouse.sqlite]
                                         [--out data/hour_gate_evidence.json]
                                         [--lookback-days 60]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MIN_TRADES_PER_HOUR = 8
WR_BLOCK_THRESHOLD  = 35.0    # percent
PNL_BLOCK_THRESHOLD = -3.0    # USDT total
WR_GREEN_THRESHOLD  = 55.0
PNL_GREEN_THRESHOLD = 3.0


def _load_hour_stats(db_path: Path, lookback_days: int) -> dict[int, dict]:
    """Group warehouse closed trades by UTC entry hour."""
    if not db_path.exists():
        return {}
    cutoff = time.time() - lookback_days * 86400
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                CAST(strftime('%H', datetime(ts_entry, 'unixepoch')) AS INTEGER) AS hr,
                COUNT(*) AS n,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(realized_pnl) AS pnl
            FROM trades
            WHERE status='CLOSED' AND ts_entry >= ?
            GROUP BY hr
            ORDER BY hr
            """,
            (cutoff,),
        )
        out: dict[int, dict] = {}
        for hr, n, wins, pnl in cur.fetchall():
            if hr is None:
                continue
            out[int(hr)] = {
                "n":   int(n or 0),
                "wins": int(wins or 0),
                "wr":  round(100.0 * (wins or 0) / max(1, n or 0), 2),
                "pnl": round(float(pnl or 0.0), 4),
            }
        return out
    finally:
        conn.close()


def _classify(stats: dict[int, dict]) -> tuple[list[int], list[int], list[int]]:
    """Return (blocked, green, neutral) hour lists."""
    blocked, green, neutral = [], [], []
    for hr in range(24):
        s = stats.get(hr)
        if s is None or s["n"] < MIN_TRADES_PER_HOUR:
            neutral.append(hr)
            continue
        if s["wr"] < WR_BLOCK_THRESHOLD and s["pnl"] < PNL_BLOCK_THRESHOLD:
            blocked.append(hr)
        elif s["wr"] >= WR_GREEN_THRESHOLD and s["pnl"] >= PNL_GREEN_THRESHOLD:
            green.append(hr)
        else:
            neutral.append(hr)
    return blocked, green, neutral


def build_evidence(db_path: Path, lookback_days: int = 60) -> dict:
    stats = _load_hour_stats(db_path, lookback_days)
    blocked, green, neutral = _classify(stats)
    return {
        "computed_at":     datetime.now(timezone.utc).isoformat(),
        "lookback_days":   lookback_days,
        "min_trades":      MIN_TRADES_PER_HOUR,
        "thresholds": {
            "wr_block_below":  WR_BLOCK_THRESHOLD,
            "pnl_block_below": PNL_BLOCK_THRESHOLD,
            "wr_green_above":  WR_GREEN_THRESHOLD,
            "pnl_green_above": PNL_GREEN_THRESHOLD,
        },
        "blocked": blocked,
        "green":   green,
        "neutral": neutral,
        "stats":   stats,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db",  default="data/warehouse.sqlite", type=Path)
    ap.add_argument("--out", default="data/hour_gate_evidence.json", type=Path)
    ap.add_argument("--lookback-days", default=60, type=int)
    args = ap.parse_args()

    evidence = build_evidence(args.db, args.lookback_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        f"[hour-gate] wrote {args.out} "
        f"(blocked={evidence['blocked']}, "
        f"green={evidence['green']}, "
        f"neutral_n={len(evidence['neutral'])})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
