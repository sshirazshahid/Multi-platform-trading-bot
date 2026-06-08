"""
vpin.py - VPIN (Volume-Synchronized Probability of Informed Trading) order-flow
toxicity, approximated from OHLCV via Bulk-Volume Classification. High VPIN =
toxic/informed flow => throttle or skip entries (execution risk gate). Pure quant.
"""
from __future__ import annotations
from math import erf, sqrt
import numpy as np


def _ncdf(z):
    return 0.5 * (1 + erf(z / sqrt(2)))


def vpin(df, bucket_frac: float = 0.02, n_recent: int = 50) -> dict:
    v = df["volume"].values.astype(float)
    c = df["close"].values.astype(float)
    if len(c) < 50 or v.sum() <= 0:
        return {"vpin": None}
    dp = np.diff(np.log(c), prepend=np.log(c[0]))
    sd = np.std(dp[1:]) + 1e-12
    fbuy = np.array([_ncdf(x / sd) for x in dp])
    vbuy = v * fbuy; vsell = v * (1 - fbuy)
    Vbucket = max(v.sum() * bucket_frac, v.mean())
    buckets = []; cb = cs = cv = 0.0
    for i in range(len(v)):
        cb += vbuy[i]; cs += vsell[i]; cv += v[i]
        while cv >= Vbucket and Vbucket > 0:
            buckets.append(abs(cb - cs) / (cb + cs + 1e-12))
            cb = cs = 0.0; cv -= Vbucket
    if len(buckets) < 5:
        return {"vpin": None}
    recent = buckets[-n_recent:]
    vp = float(np.mean(recent))
    thr = float(np.percentile(buckets, 80))
    return {"vpin": round(vp, 4), "pctl": round(float((np.array(buckets) <= vp).mean() * 100), 1),
            "toxic": vp >= thr, "n_buckets": len(buckets)}


def execution_gate(df, **kw) -> dict:
    r = vpin(df, **kw)
    if r.get("vpin") is None:
        return {"allow": True, "throttle": 1.0, "reason": "insufficient_data"}
    return {"allow": not r["toxic"], "throttle": 0.5 if r["toxic"] else 1.0,
            "vpin": r["vpin"], "pctl": r["pctl"], "toxic": r["toxic"]}
