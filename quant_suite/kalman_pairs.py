"""
kalman_pairs.py - Kalman-filter pairs trading. Estimates a DYNAMIC hedge ratio
between two cointegrated-ish assets (e.g. ETH vs BTC) via a scalar Kalman filter,
then trades the spread z-score (mean reversion). Pure quant; market-neutral.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def kalman_hedge(y, x, delta: float = 1e-4, R: float = 1e-3):
    """Scalar Kalman: state beta (random walk), obs y = beta*x + noise. Returns beta series."""
    y = np.asarray(y, float); x = np.asarray(x, float); n = len(y)
    beta = np.zeros(n); P = 1.0; b = 0.0; Q = delta / (1 - delta)
    for t in range(n):
        P = P + Q
        xt = x[t]
        denom = xt * xt * P + R
        K = P * xt / denom if denom != 0 else 0.0
        b = b + K * (y[t] - b * xt)
        P = (1 - K * xt) * P
        beta[t] = b
    return beta


def spread_zscore(y_prices, x_prices, z_win: int = 48, delta: float = 1e-4):
    y = np.log(np.asarray(y_prices, float)); x = np.log(np.asarray(x_prices, float))
    beta = kalman_hedge(y, x, delta=delta)
    spread = pd.Series(y - beta * x)
    z = (spread - spread.rolling(z_win).mean()) / (spread.rolling(z_win).std() + 1e-12)
    return beta, spread.values, z.values


def current(y_prices, x_prices, z_win: int = 48, entry: float = 2.0) -> dict:
    beta, spread, z = spread_zscore(y_prices, x_prices, z_win)
    zi = float(z[-1]) if z[-1] == z[-1] else 0.0
    side = "short_spread" if zi >= entry else ("long_spread" if zi <= -entry else "flat")
    return {"zscore": round(zi, 3), "beta": round(float(beta[-1]), 4), "signal": side,
            "interpret": {"short_spread": "short Y / long X", "long_spread": "long Y / short X",
                          "flat": "no trade"}[side]}


def backtest(y_prices, x_prices, z_win: int = 48, entry: float = 2.0, exit: float = 0.5,
             fee: float = 0.0005, max_hold: int = 240) -> dict:
    beta, spread, z = spread_zscore(y_prices, x_prices, z_win)
    n = len(z); pos = 0; entry_i = None; trades = []
    for i in range(z_win + 1, n - 1):
        zi = z[i]
        if zi != zi:
            continue
        if pos == 0:
            if zi >= entry:
                pos, entry_i = -1, i
            elif zi <= -entry:
                pos, entry_i = 1, i
        else:
            if (pos == 1 and zi >= -exit) or (pos == -1 and zi <= exit) or (i - entry_i) >= max_hold:
                trades.append(pos * (spread[i] - spread[entry_i]) - 2 * fee)
                pos, entry_i = 0, None
    trades = np.array(trades)
    if len(trades) == 0:
        return {"trades": 0}
    wins = trades[trades > 0]; loss = -trades[trades <= 0].sum()
    return {"trades": len(trades), "win_rate": round(float((trades > 0).mean() * 100), 1),
            "avg_pnl_logspread": round(float(trades.mean()), 5), "total": round(float(trades.sum()), 4),
            "profit_factor": round(float(wins.sum() / (loss + 1e-12)), 2)}
