"""Options-implied (DVOL) edge screen — does implied vol predict forward SPOT returns?

The last untested orthogonal family. Fetches Deribit's DVOL implied-volatility index (free
public API, no key) for the only liquid-options coins (BTC, ETH), aligns it with 1h spot from
the cache, and tests whether the IV level / change predicts forward spot RETURN (direction)
after cost. Leakage-safe: signal at hour t uses DVOL known at t; target is fwd return [t,t+H].

Hypotheses (each tested long & via sign):
  H1 high-IV -> bounce : dvol_z >= K -> go long (fear exhaustion / mean-revert)
  H2 high-IV -> risk-off: dvol_z >= K -> go short (vol spike = downside)
  H3 IV rising         : sign(d_dvol) predicts forward return
  H4 IV level          : sign(dvol_z) predicts forward return

HONEST PRIOR (theory, not just empirics): implied vol forecasts future *realized vol*, NOT
*direction*. So a directional edge from DVOL is unlikely on first principles; this is a
vol-timing signal at best (the bot already has a BTC-vol pause). Only BTC/ETH have liquid
options; the more directional options signal (25d risk-reversal SKEW) needs paid historical
data and is NOT tested here. After-cost (10bps), beta-adjusted (ETH vs BTC), Bonferroni-gated.

Read-only w.r.t. the bot. Caches DVOL to data/dvol_history.jsonl. Usage:
  python scripts/run_options_iv_edge_screen.py
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ohlcv_cache"
DVOL_CACHE = ROOT / "data" / "dvol_history.jsonl"
DERIBIT = "https://www.deribit.com/api/v2/public/get_volatility_index_data"

CCYS = ["BTC", "ETH"]
DAYS = 365
HORIZONS = [4, 12, 24]  # forward hours (IV is a low-frequency signal)
Z_WIN = 168  # trailing window for z-score / beta
Z_K = 1.5  # IV-spike threshold (IV moves smoother than liq)
COST_RT = 10.0 / 1e4
MIN_N = 30


def _fetch_dvol(ccy: str) -> pd.DataFrame:
    now = int(time.time())
    s_ms = (now - DAYS * 86400) * 1000
    end_ms = now * 1000
    win = 30 * 86400 * 1000
    rows = []
    schema_note = False
    while s_ms < end_ms:
        e_ms = min(s_ms + win, end_ms)
        try:
            r = requests.get(
                DERIBIT,
                params={
                    "currency": ccy,
                    "start_timestamp": s_ms,
                    "end_timestamp": e_ms,
                    "resolution": 3600,
                },
                timeout=30,
            )
            j = r.json()
        except Exception as e:
            print(f"[dvol] {ccy} window failed: {e}")
            s_ms = e_ms
            continue
        data = (j.get("result") or {}).get("data") or []
        if data and not schema_note:
            print(f"[dvol] {ccy} sample row (ts_ms,o,h,l,c): {data[0]}")
            schema_note = True
        for pt in data:
            ts_ms = int(pt[0])
            close = float(pt[-1])  # Deribit: [ts_ms,o,h,l,c]
            rows.append((ts_ms // 1000 // 3600 * 3600, close))
        s_ms = e_ms
        time.sleep(0.3)
    if not rows:
        return pd.DataFrame(columns=["hour", "dvol"])
    return (
        pd.DataFrame(rows, columns=["hour", "dvol"])
        .drop_duplicates("hour")
        .sort_values("hour")
        .reset_index(drop=True)
    )


def _load_spot_1h(coin: str) -> pd.DataFrame | None:
    p = CACHE / f"{coin}-USDT_1h.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)[["ts", "close"]].dropna().sort_values("ts").drop_duplicates("ts")
    ts = df["ts"].astype("int64")
    div = 1000 if int(ts.iloc[-1]) > 1_000_000_000_000 else 1
    df["hour"] = (ts // div // 3600 * 3600).astype(int)
    return df[["hour", "close"]].drop_duplicates("hour").reset_index(drop=True)


def _ztrail(x, win):
    z = np.full(len(x), np.nan)
    for t in range(win, len(x)):
        w = x[t - win : t]
        m, s = w.mean(), w.std()
        if s > 0:
            z[t] = (x[t] - m) / s
    return z


def _tstat(a):
    a = a[~np.isnan(a)]
    if len(a) < 2:
        return 0.0
    s = a.std(ddof=1)
    return float(a.mean() / (s / math.sqrt(len(a)))) if s > 0 else 0.0


def _rolling_beta(y, x, win):
    beta = np.full(len(y), np.nan)
    for t in range(win, len(y)):
        ys, xs = y[t - win : t], x[t - win : t]
        g = ~np.isnan(ys) & ~np.isnan(xs)
        if g.sum() > win // 2 and xs[g].var() > 0:
            beta[t] = np.cov(ys[g], xs[g])[0, 1] / xs[g].var()
    return np.nan_to_num(beta, nan=1.0)


def _norm_ppf(p):
    a = [
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.3577518672690,
        -30.66479806614716,
        2.506628277459239,
    ]
    b = [
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    ]
    c = [
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    ]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 0.97575:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def main():
    allrows, dvol_by = [], {}
    for ccy in CCYS:
        df = _fetch_dvol(ccy)
        print(f"[dvol] {ccy}: {len(df)} hourly points")
        dvol_by[ccy] = df
        for _, row in df.iterrows():
            allrows.append({"hour": int(row["hour"]), "currency": ccy, "dvol": float(row["dvol"])})
    if allrows:
        DVOL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with DVOL_CACHE.open("w", encoding="utf-8") as f:
            for r in allrows:
                f.write(json.dumps(r) + "\n")
    btc_spot = _load_spot_1h("BTC")
    if btc_spot is None:
        print("BTC spot missing — cannot beta-adjust.")
        return
    btc_by_hour = dict(zip(btc_spot["hour"], btc_spot["close"]))
    rows = []
    for ccy in CCYS:
        dv, spot = dvol_by[ccy], _load_spot_1h(ccy)
        if spot is None or len(dv) < Z_WIN + max(HORIZONS) + MIN_N:
            print(f"[screen] {ccy}: insufficient data")
            continue
        m = dv.merge(spot, on="hour", how="inner").sort_values("hour").reset_index(drop=True)
        if len(m) < Z_WIN + max(HORIZONS) + MIN_N:
            print(f"[screen] {ccy}: only {len(m)} overlapping hours")
            continue
        hour = m["hour"].values
        close = m["close"].values.astype(float)
        dvol = m["dvol"].values.astype(float)
        dz = _ztrail(dvol, Z_WIN)
        ddv = np.concatenate([[np.nan], np.diff(dvol)])
        btc = np.array([btc_by_hour.get(h, np.nan) for h in hour], dtype=float)
        for H in HORIZONS:
            fwd = np.full(len(close), np.nan)
            fwd[:-H] = close[H:] / close[:-H] - 1.0
            bfwd = np.full(len(close), np.nan)
            bfwd[:-H] = btc[H:] / btc[:-H] - 1.0
            beta = _rolling_beta(fwd, bfwd, Z_WIN)
            alpha = fwd - beta * bfwd
            tests = [
                ("H1_highIV_long", (dz >= Z_K).astype(float)),
                ("H2_highIV_short", -(dz >= Z_K).astype(float)),
                ("H3_IVrising_sign", np.sign(ddv)),
                ("H4_IVlevel_sign", np.sign(dz)),
            ]
            for name, sig in tests:
                ok = ~np.isnan(alpha) & ~np.isnan(sig) & (sig != 0)
                ok[-H:] = False
                if ok.sum() < MIN_N:
                    continue
                r = sig[ok] * alpha[ok] - COST_RT
                rows.append(
                    {
                        "coin": ccy,
                        "h": H,
                        "hyp": name,
                        "n": int(ok.sum()),
                        "mean_net_pct": float(np.nanmean(r)) * 100,
                        "t": _tstat(r),
                    }
                )
    if not rows:
        print("\nNo testable DVOL+spot overlap — nothing screened.")
        return
    nt = len(rows)
    bonf = abs(_norm_ppf(1 - 0.05 / (2 * nt)))
    print(
        f"\n=== OPTIONS-IMPLIED (DVOL) SCREEN | {nt} tests | Bonferroni |t|={bonf:.2f} | "
        f"cost {COST_RT * 1e4:.0f}bps | beta-adj ==="
    )
    print(f"net>0 in {sum(1 for r in rows if r['mean_net_pct'] > 0)}/{nt}")
    surv = [r for r in rows if r["mean_net_pct"] > 0 and abs(r["t"]) >= bonf and r["n"] >= MIN_N]
    print(f"\nSURVIVORS (net>0 AND |t|>=Bonferroni): {len(surv)}")
    for r in sorted(surv, key=lambda x: -x["t"]):
        print(
            f"  {r['coin']} h={r['h']} {r['hyp']:<18} n={r['n']} net={r['mean_net_pct']:+.3f}% t={r['t']:.2f}"
        )
    print("\nALL TESTS:")
    for r in sorted(rows, key=lambda x: (x["coin"], x["h"])):
        print(
            f"  {r['coin']} h={r['h']:<3} {r['hyp']:<18} n={r['n']:<5} net={r['mean_net_pct']:+.3f}% t={r['t']:.2f}"
        )


if __name__ == "__main__":
    main()
