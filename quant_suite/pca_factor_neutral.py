"""
pca_factor_neutral.py - PCA factor-neutral long/short. Strips the top-K principal
components (market beta + dominant factors) from the cross-sectional returns
panel and trades the residual (mean-revert the idiosyncratic part). Pure NumPy SVD.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _resid_z(R: np.ndarray, k: int):
    Xc = R - R.mean(axis=0)
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    k = min(k, Vt.shape[0])
    proj = Xc @ Vt[:k].T @ Vt[:k]
    resid = Xc - proj
    lr = resid[-1]
    return (lr - lr.mean()) / (lr.std() + 1e-12)


def residual_signals(prices: pd.DataFrame, lookback: int = 120, k: int = 2, z_gate: float = 1.0) -> dict:
    rets = prices.pct_change().dropna()
    if rets.shape[1] < 4 or len(rets) < lookback:
        return {}
    z = _resid_z(rets.tail(lookback).values, k)
    w = -np.where(np.abs(z) >= z_gate, z, 0.0); w = w - w.mean()
    g = np.abs(w).sum(); w = w / g if g > 0 else w * 0
    out = {}
    for i, s in enumerate(prices.columns):
        if abs(z[i]) < z_gate:
            continue
        out[s] = {"resid_z": round(float(z[i]), 3), "side": "sell" if z[i] > 0 else "buy",
                  "weight": round(float(w[i]), 4)}
    return out


def backtest(prices: pd.DataFrame, lookback: int = 120, k: int = 2, hold: int = 6,
             fee: float = 0.0005, z_gate: float = 1.0) -> dict:
    rets = prices.pct_change()
    n = len(prices)
    if n < lookback + hold or prices.shape[1] < 4:
        return {"periods": 0, "note": "insufficient_panel"}
    per = []; w = None; prev = None
    for i in range(lookback, n - 1):
        if w is None or (i - lookback) % hold == 0:
            R = rets.iloc[i - lookback + 1:i + 1].dropna().values
            if len(R) < lookback // 2:
                per.append(0.0); continue
            z = _resid_z(R, k)
            ww = -np.where(np.abs(z) >= z_gate, z, 0.0); ww = ww - ww.mean()
            g = np.abs(ww).sum(); w = ww / g if g > 0 else ww * 0
            turn = np.abs(w - prev).sum() if prev is not None else np.abs(w).sum()
            cost = float(turn) * fee; prev = w
        else:
            cost = 0.0
        per.append(float(np.nansum(w * rets.iloc[i + 1].values)) - cost)
    per = np.array(per)
    if len(per) == 0:
        return {"periods": 0}
    eq = (1 + per).cumprod(); dd = (eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)
    return {"periods": len(per), "total_return_pct": round(float((eq[-1] - 1) * 100), 2),
            "win_rate": round(float((per > 0).mean() * 100), 1),
            "sharpe_like": round(float(per.mean() / (per.std() + 1e-12) * np.sqrt(len(per))), 2),
            "max_dd_pct": round(float(dd.min() * 100), 2)}
