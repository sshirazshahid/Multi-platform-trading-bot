"""Red->green FLIP candle -> next-candle predictability screen (FUTURES, after-cost).

Tests the user's EXACT hypothesis: "a candle that turns from red to green predicts the
next candle (hits TP)." Signal at bar t = green(t) AND red(t-1), i.e. a bullish reversal
candle. We then measure whether the NEXT bar (t+1) is predictable enough to profit after
cost, two ways:

  (A) Directional: P(next bar up | flip) vs the unconditional base rate P(up). The EDGE is
      hit_rate - base_rate. In an up-market base_rate is already >50% (that is BTC beta, not
      skill), so only the DELTA over base is a real signal. We also report mean next-bar
      NET return (after ~10bps round-trip futures cost) and its t-stat.

  (B) TP-first (1:1, 0.4%): enter long at flip close; within the next bar's wick, does
      +0.40% (TP) trigger before -0.40% (SL)? Reports tp_first_rate and after-cost EV.
      Breakeven tp_first for 1:1 after 10bps cost is ~0.55.

Multiple comparisons: ~N coins x 5 timeframes tests are run; with that many, ~lots of
|t|>=2 hits happen by chance. A real survivor must clear a Bonferroni bar AND positive net.

Read-only. Reads local parquet cache only. Prints a table; writes nothing.

Usage: python scripts/red_green_flip_screen.py [--named-only]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ohlcv_cache"

NAMED = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "LINK",
    "TRX",
    "SUI",
    "ATOM",
    "DOGE",
    "ALGO",
    "ZEC",
    "ADA",
    "XRP",
    "LTC",
    "GRT",
]
NATIVE_TFS = ["15m", "1h", "4h"]
RESAMPLE = {"30m": ("15m", 2), "45m": ("15m", 3)}
ALL_TFS = ["15m", "30m", "45m", "1h", "4h"]
COST_RT = 10.0 / 1e4  # 10 bps round-trip (5 bps/side taker, majors)
TP_SL = 0.0040  # 0.40% symmetric TP/SL for the wick test
MIN_N = 50  # min flip events for a usable stat


def _load(coin: str, tf: str) -> pd.DataFrame | None:
    if tf in NATIVE_TFS:
        p = CACHE / f"{coin}-USDT_{tf}.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        need = {"ts", "open", "high", "low", "close"}
        if not need.issubset(df.columns):
            return None
        df = df[["ts", "open", "high", "low", "close"]].dropna()
        return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    base_tf, k = RESAMPLE[tf]
    b = _load(coin, base_tf)
    if b is None or len(b) < k * (MIN_N + 5):
        return None
    n = (len(b) // k) * k
    b = b.iloc[:n]
    g = np.arange(n) // k
    out = pd.DataFrame(
        {
            "ts": b["ts"].groupby(g).first().values,
            "open": b["open"].groupby(g).first().values,
            "high": b["high"].groupby(g).max().values,
            "low": b["low"].groupby(g).min().values,
            "close": b["close"].groupby(g).last().values,
        }
    )
    return out


def screen(df: pd.DataFrame) -> dict | None:
    o, h, low, c = (df[x].values.astype(float) for x in ("open", "high", "low", "close"))
    if len(c) < MIN_N + 5:
        return None
    green = c > o
    red = c < o
    flip = green[1:] & red[:-1]  # flip at bar t (index aligns to t starting at 1)
    flip = np.concatenate([[False], flip])  # back to length N, index t
    t = np.where(flip)[0]
    t = t[t < len(c) - 1]  # need t+1 to exist
    if len(t) < MIN_N:
        return None
    # (A) directional next-bar
    nxt_ret = c[t + 1] / c[t] - 1.0  # enter close[t], exit close[t+1]
    up_all = c[1:] > c[:-1]  # unconditional next-bar up
    base_up = float(up_all.mean())
    hit_up = float((nxt_ret > 0).mean())
    net = nxt_ret - COST_RT
    mean_net = float(net.mean())
    sd = float(net.std(ddof=1)) if len(net) > 1 else 0.0
    tstat = (mean_net / (sd / math.sqrt(len(net)))) if sd > 0 else 0.0
    # (B) TP-first wick test on bar t+1: long from close[t]
    entry = c[t]
    tp = entry * (1 + TP_SL)
    sl = entry * (1 - TP_SL)
    hi1, lo1 = h[t + 1], low[t + 1]
    hit_tp = hi1 >= tp
    hit_sl = lo1 <= sl
    tp_first = hit_tp & ~hit_sl  # conservative: both-in-bar => SL first (worst case)
    tp_first_rate = float(tp_first.mean())
    ev = tp_first_rate * (TP_SL - COST_RT) + (1 - tp_first_rate) * (-TP_SL - COST_RT)
    return {
        "n": int(len(t)),
        "base_up": base_up,
        "hit_up": hit_up,
        "edge": hit_up - base_up,
        "mean_net_pct": mean_net * 100,
        "t": tstat,
        "tp_first": tp_first_rate,
        "ev_pct": ev * 100,
    }


def main():
    named_only = "--named-only" in sys.argv
    if named_only:
        coins = NAMED
    else:
        coins = sorted(
            {
                p.name.split("-USDT")[0]
                for p in CACHE.glob("*-USDT_*.parquet")
                if "USDTUSDT" not in p.name
            }
        )
    rows = []
    for coin in coins:
        for tf in ALL_TFS:
            try:
                df = _load(coin, tf)
            except Exception:
                df = None
            if df is None:
                continue
            r = screen(df)
            if r:
                r["coin"], r["tf"] = coin, tf
                rows.append(r)
    if not rows:
        print("no usable series")
        return
    n_tests = len(rows)
    bonf_t = abs(_norm_ppf(1 - 0.05 / (2 * n_tests)))  # two-sided Bonferroni z-bar

    named_rows = [r for r in rows if r["coin"] in NAMED]
    print(
        f"\n=== RED->GREEN FLIP -> NEXT CANDLE | {n_tests} (coin x tf) tests | "
        f"Bonferroni |t| bar = {bonf_t:.2f} | cost {COST_RT * 1e4:.0f}bps RT ==="
    )

    def _summ(label, rs):
        if not rs:
            print(f"\n{label}: no rows")
            return
        edges = np.array([r["edge"] for r in rs])
        nets = np.array([r["mean_net_pct"] for r in rs])
        tpf = np.array([r["tp_first"] for r in rs])
        evs = np.array([r["ev_pct"] for r in rs])
        print(f"\n{label} ({len(rs)} coin-tf):")
        print(
            f"  next-bar UP edge vs base : mean {edges.mean() * 100:+.2f}pp  "
            f"(>0 in {int((edges > 0).sum())}/{len(rs)})"
        )
        print(
            f"  next-bar NET return/trade: mean {nets.mean():+.3f}%  "
            f"(>0 in {int((nets > 0).sum())}/{len(rs)})"
        )
        print(
            f"  TP-first rate (1:1 0.4%) : mean {tpf.mean():.3f}  "
            f"(breakeven ~0.55; >=0.55 in {int((tpf >= 0.55).sum())}/{len(rs)})"
        )
        print(
            f"  wick-trade EV/trade      : mean {evs.mean():+.3f}%  "
            f"(>0 in {int((evs > 0).sum())}/{len(rs)})"
        )

    _summ("NAMED-15 POOLED", named_rows)
    if not named_only:
        _summ("ALL COINS POOLED", rows)

    surv = [r for r in rows if r["mean_net_pct"] > 0 and r["edge"] > 0 and abs(r["t"]) >= bonf_t]
    print(f"\n=== SURVIVORS (net>0 AND hit>base AND |t|>=Bonferroni {bonf_t:.2f}): {len(surv)} ===")
    for r in sorted(surv, key=lambda x: -x["t"])[:25]:
        print(
            f"  {r['coin']:<8} {r['tf']:<4} n={r['n']:<5} hit={r['hit_up']:.3f} "
            f"base={r['base_up']:.3f} edge={r['edge'] * 100:+.2f}pp net={r['mean_net_pct']:+.3f}% "
            f"t={r['t']:.2f} tp1st={r['tp_first']:.3f} ev={r['ev_pct']:+.3f}%"
        )
    print("\n=== TOP 10 BY |t| (regardless of survival) ===")
    for r in sorted(rows, key=lambda x: -abs(x["t"]))[:10]:
        print(
            f"  {r['coin']:<8} {r['tf']:<4} n={r['n']:<5} hit={r['hit_up']:.3f} "
            f"base={r['base_up']:.3f} edge={r['edge'] * 100:+.2f}pp net={r['mean_net_pct']:+.3f}% "
            f"t={r['t']:.2f} tp1st={r['tp_first']:.3f}"
        )


def _norm_ppf(p: float) -> float:
    # Acklam inverse-normal approximation (no scipy dependency)
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    cc = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((cc[0] * q + cc[1]) * q + cc[2]) * q + cc[3]) * q + cc[4]) * q + cc[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((cc[0] * q + cc[1]) * q + cc[2]) * q + cc[3]) * q + cc[4]) * q + cc[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


if __name__ == "__main__":
    main()
