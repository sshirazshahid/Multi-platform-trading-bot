"""Liquidation edge screen (FUTURES, after-cost, beta-adjusted) — source-agnostic.

Tests whether hourly liquidation flow predicts forward price returns, three hypotheses,
all leakage-safe (signal at hour t uses liq aggregated DURING hour t, which is known at the
hour's close; target is the forward return from close[t] to close[t+H]):

  H1 LONG-LIQ CASCADE -> BOUNCE: a large long-liquidation spike (forced selling) is
     exhaustion -> go LONG. Signal = z(long_usd) >= K over a trailing window.
  H2 SHORT-LIQ CASCADE -> FADE: a large short-liquidation spike (forced buying) -> go SHORT.
     Signal = z(short_usd) >= K.
  H3 IMBALANCE: imb = (short_usd - long_usd)/(short_usd + long_usd). Test whether net liq
     direction predicts the forward return (trade sign(imb)).

RIGOR (same bar as every prior screen):
  - after-cost: forward return net of ~10bps round-trip futures cost.
  - beta-adjusted: alpha = coin_fwd_ret - beta * BTC_fwd_ret (beta from a trailing window),
    so a signal that only "works" because the whole market bounced scores ~0.
  - multiple comparisons: Bonferroni |t| bar over all (coin x horizon x hypothesis) tests.
  - a survivor must clear: net>0 in the traded direction AND |t|>=Bonferroni AND n>=MIN_N.

Honest prior: NO_EDGE (vol/timing gate at best). Read-only. Reads the canonical
data/liquidations_history.jsonl (hour, symbol, long_usd, short_usd, count) + the 1h OHLCV
parquet cache. Writes nothing.

Usage:
  python scripts/run_liquidation_edge_screen.py            # screen real data
  python scripts/run_liquidation_edge_screen.py --selftest # validate the math on synthetic data
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LIQ = ROOT / "data" / "liquidations_history.jsonl"
CACHE = ROOT / "data" / "ohlcv_cache"

HORIZONS = [1, 4, 12]            # forward hours to hold
Z_WIN = 168                     # trailing window for spike z-score / beta (7 days of hours)
Z_K = 2.5                       # spike threshold (z >= K)
COST_RT = 10.0 / 1e4            # 10 bps round-trip
MIN_N = 30                      # min signal occurrences


def _load_liq() -> dict[str, pd.DataFrame]:
    """coin -> hourly DataFrame[hour, long_usd, short_usd] (symbol 'ALL' rows skipped)."""
    if not LIQ.exists():
        return {}
    by: dict[str, list] = defaultdict(list)
    with LIQ.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            sym = str(r.get("symbol", ""))
            if sym in ("", "ALL"):
                continue
            coin = sym.replace("USDT_PERP", "").replace("USDT", "").replace("-", "").split(".")[0]
            by[coin].append((int(r["hour"]), float(r.get("long_usd", 0)), float(r.get("short_usd", 0))))
    out = {}
    for coin, rows in by.items():
        df = pd.DataFrame(rows, columns=["hour", "long_usd", "short_usd"]).drop_duplicates("hour")
        out[coin] = df.sort_values("hour").reset_index(drop=True)
    return out


def _load_price_1h(coin: str) -> pd.DataFrame | None:
    p = CACHE / f"{coin}-USDT_1h.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if not {"ts", "close"}.issubset(df.columns):
        return None
    df = df[["ts", "close"]].dropna().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    ts = df["ts"].astype("int64")
    div = 1000 if int(ts.iloc[-1]) > 1_000_000_000_000 else 1   # cache ts may be ms or s
    df["hour"] = (ts // div // 3600 * 3600).astype(int)
    return df[["hour", "close"]].drop_duplicates("hour")


def _zscore_trailing(x: np.ndarray, win: int) -> np.ndarray:
    """z of x[t] vs the window [t-win, t-1] (strictly past). NaN until enough history."""
    z = np.full(len(x), np.nan)
    for t in range(win, len(x)):
        w = x[t - win:t]
        m, s = w.mean(), w.std()
        if s > 0:
            z[t] = (x[t] - m) / s
    return z


def _tstat(a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    if len(a) < 2:
        return 0.0
    s = a.std(ddof=1)
    return float(a.mean() / (s / math.sqrt(len(a)))) if s > 0 else 0.0


def _rolling_beta(y: np.ndarray, x: np.ndarray, win: int) -> np.ndarray:
    beta = np.full(len(y), np.nan)
    for t in range(win, len(y)):
        ys, xs = y[t - win:t], x[t - win:t]
        good = ~np.isnan(ys) & ~np.isnan(xs)
        if good.sum() > win // 2:
            xv = xs[good]
            if xv.var() > 0:
                beta[t] = np.cov(ys[good], xv)[0, 1] / xv.var()
    return np.nan_to_num(beta, nan=1.0)


def _screen_coin(coin, liq, price, btc_by_hour):
    m = liq.merge(price, on="hour", how="inner").sort_values("hour").reset_index(drop=True)
    if len(m) < Z_WIN + max(HORIZONS) + MIN_N:
        return []
    hour = m["hour"].values
    close = m["close"].values.astype(float)
    longu = m["long_usd"].values.astype(float)
    shortu = m["short_usd"].values.astype(float)
    btc = np.array([btc_by_hour.get(h, np.nan) for h in hour], dtype=float)
    z_long = _zscore_trailing(longu, Z_WIN)
    z_short = _zscore_trailing(shortu, Z_WIN)
    imb = (shortu - longu) / (shortu + longu + 1.0)
    res = []
    for H in HORIZONS:
        fwd = np.full(len(close), np.nan); fwd[:-H] = close[H:] / close[:-H] - 1.0
        bfwd = np.full(len(close), np.nan); bfwd[:-H] = btc[H:] / btc[:-H] - 1.0
        beta = _rolling_beta(fwd, bfwd, Z_WIN)
        alpha = fwd - beta * bfwd
        for name, mask, direction in (
            ("H1_longliq_bounce", z_long >= Z_K, +1),
            ("H2_shortliq_fade", z_short >= Z_K, -1),
        ):
            ok = mask & ~np.isnan(alpha)
            ok[-H:] = False
            if ok.sum() < MIN_N:
                continue
            r = direction * alpha[ok] - COST_RT
            res.append({"coin": coin, "h": H, "hyp": name, "n": int(ok.sum()),
                        "mean_net_pct": float(np.nanmean(r)) * 100, "t": _tstat(r)})
        ok = ~np.isnan(alpha)
        ok[-H:] = False
        if ok.sum() >= MIN_N:
            r = np.sign(imb[ok]) * alpha[ok] - COST_RT
            res.append({"coin": coin, "h": H, "hyp": "H3_imbalance_mom", "n": int(ok.sum()),
                        "mean_net_pct": float(np.nanmean(r)) * 100, "t": _tstat(r)})
    return res


def _norm_ppf(p):
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
    pl, ph = 0.02425, 0.97575
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def run(liq_by_coin):
    if not liq_by_coin:
        print("NO liquidation data found at data/liquidations_history.jsonl.\n"
              "Fetch it first (free Coinalyze key, or the WS harvester) — then re-run.")
        return
    btc_price = _load_price_1h("BTC")
    if btc_price is None:
        print("BTC 1h price missing from cache — needed for beta adjustment."); return
    btc_by_hour = dict(zip(btc_price["hour"].values, btc_price["close"].values))
    rows = []
    for coin, liq in sorted(liq_by_coin.items()):
        price = _load_price_1h(coin)
        if price is None:
            continue
        rows.extend(_screen_coin(coin, liq, price, btc_by_hour))
    if not rows:
        print("No coin had enough overlapping liquidation+price history to screen "
              f"(need >= {Z_WIN + max(HORIZONS) + MIN_N} hours).")
        return
    n_tests = len(rows)
    bonf = abs(_norm_ppf(1 - 0.05 / (2 * n_tests)))
    print(f"\n=== LIQUIDATION EDGE SCREEN | {len(liq_by_coin)} coins | {n_tests} tests | "
          f"Bonferroni |t| = {bonf:.2f} | cost {COST_RT*1e4:.0f}bps | beta-adjusted ===")
    pos = sum(1 for r in rows if r["mean_net_pct"] > 0)
    print(f"net>0 in {pos}/{n_tests} tests (chance ~50%)")
    surv = [r for r in rows if r["mean_net_pct"] > 0 and abs(r["t"]) >= bonf and r["n"] >= MIN_N]
    print(f"\nSURVIVORS (net>0 AND |t|>=Bonferroni {bonf:.2f}): {len(surv)}")
    for r in sorted(surv, key=lambda x: -x["t"])[:25]:
        print(f"  {r['coin']:<8} h={r['h']:<3} {r['hyp']:<20} n={r['n']:<5} "
              f"net={r['mean_net_pct']:+.3f}% t={r['t']:.2f}")
    print("\nTOP 8 BY |t| (regardless of survival):")
    for r in sorted(rows, key=lambda x: -abs(x["t"]))[:8]:
        print(f"  {r['coin']:<8} h={r['h']:<3} {r['hyp']:<20} n={r['n']:<5} "
              f"net={r['mean_net_pct']:+.3f}% t={r['t']:.2f}")


def _selftest():
    """Validate the math: inject long-liq spikes followed by a +1.5% next-bar move on
    SELFCOIN (with BTC flat-ish so it survives beta adjustment) -> H1 should surface with a
    large positive t. Proves the screen can DETECT a real signal when one exists."""
    rng = np.random.default_rng(7)
    n = 1500
    hours = (1_700_000_000 + np.arange(n) * 3600) // 3600 * 3600
    btc = 100 * np.cumprod(1 + rng.normal(0, 0.002, n))      # low-beta market
    coin_px = 50 * np.cumprod(1 + rng.normal(0, 0.004, n))
    longu = np.abs(rng.normal(1e5, 3e4, n)); shortu = np.abs(rng.normal(1e5, 3e4, n))
    spike = rng.random(n) < 0.05
    longu[spike] += 1e6
    for t in np.where(spike)[0]:
        if t + 1 < n:
            coin_px[t + 1:] *= 1.015                          # injected bounce after long-liq
    liq_by = {"SELFCOIN": pd.DataFrame({"hour": hours.astype(int), "long_usd": longu, "short_usd": shortu})}
    g = globals(); orig = g["_load_price_1h"]
    g["_load_price_1h"] = lambda coin: pd.DataFrame(
        {"hour": hours.astype(int), "close": (coin_px if coin == "SELFCOIN" else btc)})
    try:
        print("[selftest] injected long-liq->+1.5% bounce on SELFCOIN; expect H1 t large & positive:")
        run(liq_by)
    finally:
        g["_load_price_1h"] = orig


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        run(_load_liq())
