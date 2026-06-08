"""
xsec_meanrev.py - Cross-sectional mean reversion (market-neutral long/short).
Pure price/quant. Ranks coins by lookback return cross-sectionally; longs the
relative losers, shorts the relative winners, dollar-neutral. Diversifies the
directional trend book.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def current_signals(prices: pd.DataFrame, lookback: int = 24, z_gate: float = 0.5) -> dict:
    """prices: DataFrame index=dt, cols=symbols (last row = now). Returns per-symbol L/S weights."""
    if prices is None or prices.shape[1] < 3 or len(prices) <= lookback:
        return {}
    mom = prices.pct_change(lookback).iloc[-1]
    z = (mom - mom.mean()) / (mom.std() + 1e-12)
    w = -z.where(z.abs() >= z_gate, 0.0)
    w = w - w.mean()
    g = w.abs().sum()
    w = w / g if g > 0 else w * 0.0
    out = {}
    for s in prices.columns:
        zz = float(z[s])
        if abs(zz) < z_gate:
            continue
        out[s] = {"zscore": round(zz, 3), "side": "sell" if zz > 0 else "buy",
                  "weight": round(float(w[s]), 4)}
    return out


def backtest(prices: pd.DataFrame, lookback: int = 24, hold: int = 6,
             fee: float = 0.0005, z_gate: float = 0.5) -> dict:
    if prices is None or prices.shape[1] < 3 or len(prices) <= lookback + hold:
        return {"periods": 0, "note": "insufficient_panel"}
    rets = prices.pct_change()
    mom = prices.pct_change(lookback)
    n = len(prices); w = None; prev_w = None; per = []
    for i in range(lookback, n - 1):
        if w is None or (i - lookback) % hold == 0:
            m = mom.iloc[i]
            z = (m - m.mean()) / (m.std() + 1e-12)
            ww = -z.where(z.abs() >= z_gate, 0.0)
            ww = ww - ww.mean()
            g = ww.abs().sum()
            w = ww / g if g > 0 else ww * 0.0
            turn = (w - prev_w).abs().sum() if prev_w is not None else w.abs().sum()
            cost = float(turn) * fee
            prev_w = w
        else:
            cost = 0.0
        per.append(float((w * rets.iloc[i + 1]).sum()) - cost)
    per = np.array(per)
    if len(per) == 0:
        return {"periods": 0}
    eq = (1 + per).cumprod()
    dd = (eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)
    sharpe = per.mean() / (per.std() + 1e-12) * np.sqrt(len(per))
    return {"periods": len(per), "total_return_pct": round(float((eq[-1] - 1) * 100), 2),
            "avg_period_pct": round(float(per.mean() * 100), 4),
            "win_rate": round(float((per > 0).mean() * 100), 1),
            "sharpe_like": round(float(sharpe), 2), "max_dd_pct": round(float(dd.min() * 100), 2)}
