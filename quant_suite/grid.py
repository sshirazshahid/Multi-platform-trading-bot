"""grid.py - grid trading for sideways/range markets (low risk). Builds an
ATR/range grid and accumulates on dips / sells on rips. Best in RANGE regime."""
from __future__ import annotations
import numpy as np
import pandas as pd
from . import indicators as ind


def grid_from_atr(df: pd.DataFrame, n: int = 10, span_atr: float = 3.0) -> dict:
    c = float(df["close"].iloc[-1]); a = float(ind.atr(df, 14).iloc[-1] or 0)
    lower, upper = c - span_atr * a, c + span_atr * a
    levels = np.linspace(lower, upper, n)
    grid = [{"level": round(float(l), 8), "side": "buy" if l < c else "sell"} for l in levels]
    return {"price": c, "lower": round(lower, 8), "upper": round(upper, 8), "grid": grid}


def backtest(df: pd.DataFrame, n: int = 10, span_atr: float = 3.0, fee: float = 0.0005, qty: float = 1.0) -> dict:
    c = df["close"].values.astype(float)
    if len(c) < 80:
        return {"trades": 0, "note": "insufficient_history"}
    a = ind.atr(df, 14).bfill().values
    base, av = c[50], a[50]
    levels = np.linspace(base - span_atr * av, base + span_atr * av, n)
    step = levels[1] - levels[0]
    cash = 0.0; pos = 0.0; trades = 0; last = c[50]
    for i in range(51, len(c)):
        p = c[i]
        while last - p >= step and pos < n * qty:
            cash -= p * qty * (1 + fee); pos += qty; last -= step; trades += 1
        while p - last >= step and pos > 0:
            cash += p * qty * (1 - fee); pos -= qty; last += step; trades += 1
    equity = cash + pos * c[-1]
    return {"trades": trades, "final_inventory": round(pos, 4), "pnl_quote": round(float(equity), 2),
            "note": "grid PnL (quote ccy) over window; profits in range, bleeds inventory in strong trends"}
