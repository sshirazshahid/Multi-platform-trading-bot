"""scripts/refresh_hour_gates.py — emit data-driven hour gate evidence.

Reads `data/warehouse.sqlite`, groups closed trades by entry hour (UTC),
and writes `data/hour_gate_evidence.json` with the set of hours the bot
should avoid. Consumed by `core/bot_engine._classify_hour` at runtime.

Block rule (and-conditions): n>=8 trades AND wr<35% AND total_pnl<-$3.
The threshold is intentionally conservative — empty `blocked` is fine
(bot falls through to static `ALLOWED_HOURS_UTC`).

2026-06-11 (owner: "Trade only in those hours where its profitable"):
also emits `profitable` = hours with n>=8 AND whole-trade pnl > 0,
consumed by the HOUR_GATE_PROFIT_ONLY allow-list gate in
bot_engine._classify_hour. PnL is whole-trade (realized + partial-TP
legs) and scoped to the CURRENT operating mode (PAPER rows must not be
judged by CONTROLLED_LIVE-era history and vice versa).

Run weekly from `scripts/retrain_weekly.ps1`.

Usage:
    python scripts/refresh_hour_gates.py [--db data/warehouse.sqlite]
                                         [--out data/hour_gate_evidence.json]
                                         [--lookback-days 60]
                                         [--mode PAPER|CONTROLLED_LIVE]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 2026-06-11: informational CI enrichment (gate rule unchanged).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    from core.stats_inference import mean_ci, wilson_interval
except ImportError:  # degraded standalone run: skip CI fields
    mean_ci = wilson_interval = None

MIN_TRADES_PER_HOUR = 8
WR_BLOCK_THRESHOLD = 35.0  # percent
PNL_BLOCK_THRESHOLD = -3.0  # USDT total
WR_GREEN_THRESHOLD = 55.0
PNL_GREEN_THRESHOLD = 3.0


def _default_mode() -> str:
    """Current operating mode for warehouse scoping (PAPER fallback)."""
    try:
        from config import DRY_RUN

        return "PAPER" if DRY_RUN else "CONTROLLED_LIVE"
    except Exception:
        return "PAPER"


def _load_hour_stats(db_path: Path, lookback_days: int, mode: str = None) -> dict[int, dict]:
    """Group warehouse closed trades by UTC entry hour.

    Whole-trade PnL (realized + partial-TP legs), scoped to `mode`."""
    if not db_path.exists():
        return {}
    cutoff = time.time() - lookback_days * 86400
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # Per-row fetch (not GROUP BY) so per-trade pnls are available for
        # the informational mean CI (2026-06-11). Output keys preserved.
        cur.execute(
            """
            SELECT
                CAST(strftime('%H', datetime(ts_entry, 'unixepoch')) AS INTEGER) AS hr,
                realized_pnl + COALESCE(partial_realized_pnl, 0) AS pnl
            FROM trades
            WHERE status='CLOSED' AND ts_entry >= ? AND mode = ?
            """,
            (cutoff, mode or _default_mode()),
        )
        by_hr: dict[int, list] = {}
        for hr, pnl in cur.fetchall():
            if hr is None:
                continue
            by_hr.setdefault(int(hr), []).append(float(pnl or 0.0))
        out: dict[int, dict] = {}
        for hr, pnls in sorted(by_hr.items()):
            n = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            row = {
                "n": n,
                "wins": wins,
                "wr": round(100.0 * wins / max(1, n), 2),
                "pnl": round(sum(pnls), 4),
            }
            if wilson_interval is not None:
                lo, hi = wilson_interval(wins, n)
                m, mlo, mhi = mean_ci(pnls)
                row["wr_ci95"] = [round(100.0 * lo, 2), round(100.0 * hi, 2)]
                row["pnl_mean"] = round(m, 4)
                row["pnl_mean_ci95"] = [
                    None if mlo != mlo else round(mlo, 4),
                    None if mhi != mhi else round(mhi, 4),
                ]
            out[hr] = row
        return out
    finally:
        conn.close()


def _classify(stats: dict[int, dict]) -> tuple[list[int], list[int], list[int], list[int]]:
    """Return (blocked, green, neutral, profitable) hour lists.

    `profitable` (2026-06-11): hours with n>=MIN_TRADES and whole-trade
    pnl > 0 — the allow-list for HOUR_GATE_PROFIT_ONLY. Independent of
    the blocked/green/neutral buckets."""
    blocked, green, neutral, profitable = [], [], [], []
    for hr in range(24):
        s = stats.get(hr)
        if s is None or s["n"] < MIN_TRADES_PER_HOUR:
            neutral.append(hr)
            continue
        if s["pnl"] > 0:
            profitable.append(hr)
        if s["wr"] < WR_BLOCK_THRESHOLD and s["pnl"] < PNL_BLOCK_THRESHOLD:
            blocked.append(hr)
        elif s["wr"] >= WR_GREEN_THRESHOLD and s["pnl"] >= PNL_GREEN_THRESHOLD:
            green.append(hr)
        else:
            neutral.append(hr)
    return blocked, green, neutral, profitable


def build_evidence(db_path: Path, lookback_days: int = 60, mode: str = None) -> dict:
    mode = mode or _default_mode()
    stats = _load_hour_stats(db_path, lookback_days, mode)
    blocked, green, neutral, profitable = _classify(stats)
    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "mode": mode,
        "min_trades": MIN_TRADES_PER_HOUR,
        "thresholds": {
            "wr_block_below": WR_BLOCK_THRESHOLD,
            "pnl_block_below": PNL_BLOCK_THRESHOLD,
            "wr_green_above": WR_GREEN_THRESHOLD,
            "pnl_green_above": PNL_GREEN_THRESHOLD,
        },
        "blocked": blocked,
        "green": green,
        "neutral": neutral,
        # Allow-list for the HOUR_GATE_PROFIT_ONLY gate (2026-06-11)
        "profitable": profitable,
        # CI fields in stats are INFORMATIONAL ONLY — the gate rule is
        # unchanged (n>=8 AND whole-trade pnl > 0, owner directive).
        "ci_note": "informational only; gate rule unchanged (n>=8 AND pnl>0)",
        "stats": stats,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/warehouse.sqlite", type=Path)
    ap.add_argument("--out", default="data/hour_gate_evidence.json", type=Path)
    ap.add_argument("--lookback-days", default=60, type=int)
    ap.add_argument(
        "--mode", default=None, help="warehouse mode scope (default: current operating mode)"
    )
    args = ap.parse_args()

    evidence = build_evidence(args.db, args.lookback_days, args.mode)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        f"[hour-gate] wrote {args.out} "
        f"(mode={evidence['mode']}, "
        f"blocked={evidence['blocked']}, "
        f"green={evidence['green']}, "
        f"profitable={evidence['profitable']}, "
        f"neutral_n={len(evidence['neutral'])})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
