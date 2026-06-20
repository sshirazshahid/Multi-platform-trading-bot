"""Lever B - fee tier & bracket breakeven analyzer."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FEE_TIERS = {
    "current_taker_binance": 0.0005,
    "current_taker_bybit": 0.0006,
    "futures_maker_binance": 0.0002,
    "futures_maker_bybit": 0.0001,
    "vip1_taker_example": 0.00035,
    "zero_fee_floor": 0.0000,
}
BRACKETS = [
    (0.008, 0.013),
    (0.010, 0.018),
    (0.010, 0.010),
    (0.008, 0.020),
    (0.015, 0.015),
    (0.005, 0.015),
]


def load_closed(db, mode):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    where = "status='CLOSED' AND realized_pnl IS NOT NULL"
    params = []
    if mode:
        where += " AND mode=?"
        params.append(mode)
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT entry_px,size,fee,realized_pnl,market_type,exchange,side FROM trades WHERE {where}",
            params,
        )
    ]


def decompose(rows):
    n = len(rows)
    fees = sum((r.get("fee") or 0.0) for r in rows)
    net = sum((r.get("realized_pnl") or 0.0) for r in rows)
    wins = sum(1 for r in rows if (r.get("realized_pnl") or 0.0) > 0)
    return {
        "n": n,
        "fees": fees,
        "net_pnl": net,
        "gross_pnl": net + fees,
        "win_rate": wins / n if n else 0.0,
    }


def reprice_fee(rows, per_leg):
    total = 0.0
    for r in rows:
        gross = (r.get("realized_pnl") or 0.0) + (r.get("fee") or 0.0)
        notional = (r.get("entry_px") or 0.0) * (r.get("size") or 0.0)
        total += gross - notional * per_leg * 2
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse.sqlite"))
    ap.add_argument("--mode", default="CONTROLLED_LIVE")
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"[BLOCKED] warehouse not found: {args.db}")
        return 2
    rows = load_closed(args.db, args.mode) or load_closed(args.db, "")
    if not rows:
        print("No closed trades.")
        return 2
    d = decompose(rows)
    print("=" * 70)
    print(f"FEE & BRACKET BREAKEVEN  ({d['n']} closed trades, WR={d['win_rate'] * 100:.1f}%)")
    print("=" * 70)
    print(f"  recorded net PnL   ${d['net_pnl']:.2f}")
    print(f"  gross (pre-fee)    ${d['gross_pnl']:.2f}   <- outcome with ALL fees removed")
    print("\n  --- LEVER 1: same trades, different fee tier ---")
    for name, leg in FEE_TIERS.items():
        net = reprice_fee(rows, leg)
        print(
            f"    {name:24s} round-trip={leg * 2 * 100:.3f}%  net=${net:8.2f}  (vs recorded {net - d['net_pnl']:+.2f})"
        )
    print(
        f"  => {'fees are the whole problem' if d['gross_pnl'] > 0 else 'negative even at ZERO fees - NO fee tier rescues it'}"
    )
    print("\n  --- LEVER 2: bracket breakeven WR vs actual WR ---")
    actual_wr = d["win_rate"]
    rt = 0.0022
    print(f"    (actual WR = {actual_wr * 100:.1f}%; round-trip cost {rt * 100:.2f}%)")
    for sl, tp in BRACKETS:
        noedge = sl / (sl + tp)
        be = noedge + rt / (sl + tp)
        gap = actual_wr - be
        ok = "CLEARS" if gap >= 0 else f"short {(-gap) * 100:.1f}pp"
        print(
            f"    SL={sl * 100:.1f}% TP={tp * 100:.1f}%  no-edge WR={noedge * 100:.1f}%  breakeven WR={be * 100:.1f}%  -> {ok}"
        )
    print("\nBOTTOM LINE:")
    if d["gross_pnl"] <= 0:
        print("  Entry has no edge (negative pre-fee). Fee tiers/brackets change HOW FAST you")
        print("  lose, not the sign. The wider-TP 'CLEARS' rows need a REPLAY test (WR shifts")
        print("  with the bracket) - run scalp_replay_backtest.py to confirm or kill.")
    else:
        print("  Pre-fee positive: fee reduction is a genuine lever.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
