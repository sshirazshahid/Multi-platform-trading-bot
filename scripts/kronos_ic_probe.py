"""Phase 2 — Kronos directional-IC pre-screen (the cheap kill gate).

On leakage-safe post-cutoff bars only (ts >= 2025-09-01), measure whether
Kronos's predicted direction has any correlation with realized direction.
If the information coefficient is ~0 / not significant, STOP — no point
building the full after-cost bracket backtest.

One predict(pred_len=4) per decision bar yields horizons h in {1,2,4} for free.

Run:
    python scripts/kronos_ic_probe.py            # 1h primary + 15m secondary
    python scripts/kronos_ic_probe.py 1h 150     # tf, max decision bars / symbol
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "ohlcv_cache"
CUTOFF = pd.Timestamp("2025-09-01")
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA"]
HORIZONS = (1, 2, 4)
LOOKBACK = 400
MAX_H = max(HORIZONS)


def _post_cutoff(sym: str, tf: str) -> pd.DataFrame | None:
    p = CACHE / f"{sym}-USDT_{tf}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    df = df[df["dt"] >= CUTOFF].reset_index(drop=True)
    return df if len(df) > LOOKBACK + MAX_H + 10 else None


def run(tf: str, max_bars: int) -> None:
    from core.kronos_forecaster import get_kronos_forecaster

    fc = get_kronos_forecaster(lookback=LOOKBACK)
    fc.load()

    # pred score and realized return, pooled across symbols, per horizon
    preds = {h: [] for h in HORIZONS}
    reals = {h: [] for h in HORIZONS}
    min_ts_seen = pd.Timestamp.max
    n_inf = 0

    for sym in SYMBOLS:
        df = _post_cutoff(sym, tf)
        if df is None:
            print(f"  [skip] {sym} {tf}: insufficient post-cutoff data")
            continue
        min_ts_seen = min(min_ts_seen, df["dt"].min())
        lo, hi = LOOKBACK, len(df) - MAX_H - 1
        idx = np.arange(lo, hi)
        if len(idx) > max_bars:
            idx = idx[:: max(1, len(idx) // max_bars)][:max_bars]
        for i in idx:
            f = fc.forecast(df.iloc[: i + 1], horizon=MAX_H, samples=5)
            n_inf += 1
            c0 = float(df["close"].iloc[i])
            for h in HORIZONS:
                preds[h].append(f.exp_ret_at(h))
                reals[h].append(float(df["close"].iloc[i + h]) / c0 - 1.0)
        print(f"  [{sym} {tf}] {len(idx)} bars")

    assert min_ts_seen >= CUTOFF, f"LEAKAGE: used bars from {min_ts_seen} < {CUTOFF}"
    print(f"\n=== {tf} directional IC  (n_inferences={n_inf}, min_ts={min_ts_seen.date()}) ===")
    print(
        f"{'h':>3} {'n':>6} {'hit%':>7} {'hit_p':>8} {'spear_IC':>9} {'IC_p':>8} {'mean_signed_R%':>14}"
    )
    for h in HORIZONS:
        p = np.array(preds[h])
        r = np.array(reals[h])
        n = len(p)
        hit = float(np.mean(np.sign(p) == np.sign(r)))
        z = (hit - 0.5) / np.sqrt(0.25 / n)
        hit_p = 2 * (1 - stats.norm.cdf(abs(z)))
        ic, ic_p = stats.spearmanr(p, r)
        # mean realized return when we follow the predicted sign (the tradeable read)
        signed = float(np.mean(np.sign(p) * r)) * 100
        print(
            f"{h:>3} {n:>6} {hit * 100:>6.2f}% {hit_p:>8.4f} {ic:>9.4f} {ic_p:>8.4f} {signed:>13.4f}%"
        )


def main() -> None:
    args = sys.argv[1:]
    if args:
        run(args[0], int(args[1]) if len(args) > 1 else 150)
    else:
        run("1h", 150)  # primary: deeper post-cutoff tail
        run("15m", 150)  # secondary: shallow ~45d, single regime (caveated)


if __name__ == "__main__":
    main()
