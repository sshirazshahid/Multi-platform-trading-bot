"""Phase 3 — Kronos after-cost bracket falsification (the decisive gate).

Reuses the look-ahead-free primitives from scalp_replay_backtest.py
(`bracket_outcome`, `COST_TAKER`, `COST_MAKER`, SL/TP = 0.8%/1.3%) and drives
entries from the Kronos directional forecast. Leakage-safe: post-cutoff bars
only (ts >= 2025-09-01), asserted.

For each decision bar we forecast `horizon` bars ahead; side = sign(exp_ret).
We sweep a CONVICTION threshold on |exp_ret| (does the edge concentrate in
high-conviction forecasts?) and split the post-cutoff window into 3 OOS time
folds.

GATE: net EV > 0 after TAKER costs AND positive in all 3 folds → Phase 4.
Otherwise STOP (documented NO_EDGE-after-costs).

Run:
    python scripts/kronos_bracket_backtest.py            # 1h primary + 15m
    python scripts/kronos_bracket_backtest.py 1h 200
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scalp_replay_backtest import (  # noqa: E402
    COST_MAKER,
    COST_TAKER,
    MAX_HOLD_BARS_1H,
    MAX_HOLD_BARS_15M,
    SL_PCT,
    TP_PCT,
    bracket_outcome,
    load,
)

CUTOFF = pd.Timestamp("2025-09-01")
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA"]
LOOKBACK = 400
SIG_HORIZON = 4  # forecast next 4 bars for the directional signal
THRESHOLDS = [0.0, 0.002, 0.004, 0.006]
BREAKEVEN_NOCOST = SL_PCT / (SL_PCT + TP_PCT)


def collect(tf: str, max_bars: int):
    """One inference + one (taker, maker) bracket per decision bar."""
    from core.kronos_forecaster import get_kronos_forecaster

    fc = get_kronos_forecaster(lookback=LOOKBACK)
    fc.load()
    max_hold = MAX_HOLD_BARS_1H if tf == "1h" else MAX_HOLD_BARS_15M
    rows = []
    min_ts = pd.Timestamp.max
    for sym in SYMBOLS:
        df = load(f"{sym}-USDT", tf)
        if df is None:
            continue
        df = df[pd.to_datetime(df["ts"], unit="s") >= CUTOFF].reset_index(drop=True)
        if len(df) < LOOKBACK + max_hold + 10:
            print(f"  [skip] {sym} {tf}: insufficient post-cutoff data")
            continue
        min_ts = min(min_ts, pd.to_datetime(df["ts"].iloc[0], unit="s"))
        lo, hi = LOOKBACK, len(df) - max_hold - 1
        idx = np.arange(lo, hi)
        if len(idx) > max_bars:
            idx = idx[:: max(1, len(idx) // max_bars)][:max_bars]
        for i in idx:
            f = fc.forecast(df.iloc[: i + 1], horizon=SIG_HORIZON, samples=5)
            side = "buy" if f.exp_ret > 0 else "sell"
            inv = "sell" if side == "buy" else "buy"
            rt, _, _ = bracket_outcome(df, i, side, max_hold, tiebreak="sl", cost=COST_TAKER)
            rm, _, _ = bracket_outcome(df, i, side, max_hold, tiebreak="sl", cost=COST_MAKER)
            # inverted signal (flip side) — falsification-completeness: proves no
            # exploitable edge in EITHER direction (kills the "just invert it" idea).
            rti, _, _ = bracket_outcome(df, i, inv, max_hold, tiebreak="sl", cost=COST_TAKER)
            rows.append((int(df["ts"].iloc[i]), abs(f.exp_ret), rt, rm, rti))
        print(f"  [{sym} {tf}] {len(idx)} bars")
    assert min_ts >= CUTOFF, f"LEAKAGE: bars from {min_ts} < {CUTOFF}"
    rows.sort(key=lambda r: r[0])
    return rows, min_ts


def _stats(rets):
    a = np.array(rets)
    n = len(a)
    if n == 0:
        return 0, 0.0, 0.0
    return n, float(np.mean(a > 0)) * 100, float(np.mean(a)) * 100


def report(tf: str, rows, min_ts):
    print(
        f"\n=== {tf} Kronos bracket  (n={len(rows)}, min_ts={min_ts.date()}, "
        f"SL/TP={SL_PCT * 100:.1f}/{TP_PCT * 100:.1f}, breakeven_nocost={BREAKEVEN_NOCOST * 100:.1f}%) ==="
    )
    convs = np.array([r[1] for r in rows])
    taker = np.array([r[2] for r in rows])
    maker = np.array([r[3] for r in rows])
    inv_taker = np.array([r[4] for r in rows])
    print("  WR% = net-win rate AFTER costs (not TP-hit rate). Gate is EV>0, not WR vs a baseline.")
    print(
        f"{'|exp_ret|>=':>11} {'n':>6} {'WR_tk%':>7} {'EV_tk%':>8} {'WR_mk%':>7} {'EV_mk%':>8} {'EV_inv_tk%':>11}"
    )
    for thr in THRESHOLDS:
        m = convs >= thr
        n, wr_t, ev_t = _stats(taker[m])
        _, wr_m, ev_m = _stats(maker[m])
        _, _, ev_i = _stats(inv_taker[m])
        print(
            f"{thr * 100:>10.1f}% {n:>6} {wr_t:>6.2f}% {ev_t:>+7.4f}% {wr_m:>6.2f}% {ev_m:>+7.4f}% {ev_i:>+10.4f}%"
        )

    # 3 OOS folds (time-ordered) at threshold=0 and best-EV threshold
    best_thr = max(THRESHOLDS, key=lambda t: _stats(taker[convs >= t])[2])
    print(f"\n  3 OOS folds (taker):  {'fold':>5} {'n':>5} {'WR%':>7} {'EV%':>8}")
    for label, thr in [("all", 0.0), (f"|er|>={best_thr * 100:.1f}%", best_thr)]:
        idx = np.where(convs >= thr)[0]
        folds = np.array_split(idx, 3)
        evs = []
        line = []
        for k, fo in enumerate(folds):
            n, wr, ev = _stats(taker[fo])
            evs.append(ev)
            line.append(f"f{k + 1}:n={n} WR={wr:.1f}% EV={ev:+.4f}%")
        allpos = all(e > 0 for e in evs)
        print(f"   thr={label:>10}: " + "  ".join(line) + f"   ALL_FOLDS_POS={allpos}")


def main() -> None:
    args = sys.argv[1:]
    if args:
        tf = args[0]
        mb = int(args[1]) if len(args) > 1 else 200
        rows, mts = collect(tf, mb)
        report(tf, rows, mts)
    else:
        for tf in ("1h", "15m"):
            rows, mts = collect(tf, 200)
            report(tf, rows, mts)


if __name__ == "__main__":
    main()
