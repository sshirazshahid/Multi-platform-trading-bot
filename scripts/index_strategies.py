"""Causal long/flat strategy generators for index backtests (SMA/EMA/RSI/MACD/Bollinger/breakout).

Each function maps a close array -> a position array in {0.0, 1.0} (long or cash), where the
position at bar t depends ONLY on data up to t (no look-ahead — verified in tests via
core.alpha_zoo.bias_checks.lookahead_violations). Designed for walk-forward validation across
indices; close-only (no intrabar fills) so they're fully vectorisable/replayable.

STRATEGIES maps name -> (generator, param_grid) for the walk-forward sweep.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.indicators import ema as _ema  # noqa: E402
from core.agents.indicators import rsi as _rsi  # noqa: E402


def _sma(c, n):
    return pd.Series(c).rolling(n).mean().to_numpy()


def sma_cross(close, fast, slow):
    f, s = _sma(close, fast), _sma(close, slow)
    pos = np.where(f > s, 1.0, 0.0)
    pos[np.isnan(f) | np.isnan(s)] = 0.0
    return pos


def ema_cross(close, fast, slow):
    f = _ema(pd.Series(close), fast).to_numpy()
    s = _ema(pd.Series(close), slow).to_numpy()
    pos = np.where(f > s, 1.0, 0.0)
    pos[:slow] = 0.0                                  # warmup flat
    return pos


def rsi_reversal(close, period, oversold, overbought):
    """Long when RSI dips below `oversold`; exit when it rises above `overbought`."""
    r = _rsi(pd.Series(close), period).to_numpy()
    pos = np.zeros(len(close))
    state = 0.0
    for t in range(len(close)):
        if np.isnan(r[t]):
            continue
        if state == 0.0 and r[t] < oversold:
            state = 1.0
        elif state == 1.0 and r[t] > overbought:
            state = 0.0
        pos[t] = state
    return pos


def macd_cross(close, fast, slow, signal):
    c = pd.Series(close)
    macd_line = _ema(c, fast) - _ema(c, slow)
    sig = _ema(macd_line, signal)
    pos = np.where(macd_line.to_numpy() > sig.to_numpy(), 1.0, 0.0)
    pos[: slow + signal] = 0.0
    return pos


def bollinger_meanrev(close, period, k):
    """Mean-reversion long: enter when close < lower band, exit at the midline."""
    c = pd.Series(close)
    mid = c.rolling(period).mean()
    sd = c.rolling(period).std(ddof=0)
    lower = (mid - k * sd).to_numpy()
    midv = mid.to_numpy()
    pos = np.zeros(len(close))
    state = 0.0
    for t in range(len(close)):
        if np.isnan(lower[t]):
            continue
        if state == 0.0 and close[t] < lower[t]:
            state = 1.0
        elif state == 1.0 and close[t] >= midv[t]:
            state = 0.0
        pos[t] = state
    return pos


def breakout(close, lookback):
    """Donchian: long when close breaks above the PRIOR `lookback` high; exit below prior low."""
    c = pd.Series(close)
    hi = c.rolling(lookback).max().shift(1).to_numpy()   # prior window only -> no look-ahead
    lo = c.rolling(lookback).min().shift(1).to_numpy()
    pos = np.zeros(len(close))
    state = 0.0
    for t in range(len(close)):
        if np.isnan(hi[t]):
            continue
        if state == 0.0 and close[t] > hi[t]:
            state = 1.0
        elif state == 1.0 and close[t] < lo[t]:
            state = 0.0
        pos[t] = state
    return pos


# name -> (generator, [param tuples])
STRATEGIES = {
    "SMA_cross": (sma_cross, [(10, 50), (10, 100), (20, 50), (20, 100), (20, 200),
                              (50, 100), (50, 200)]),
    "EMA_cross": (ema_cross, [(10, 50), (10, 100), (20, 50), (20, 100), (20, 200),
                              (50, 100), (50, 200)]),
    "RSI_reversal": (rsi_reversal, [(14, 30, 70), (14, 30, 60), (14, 35, 65), (14, 25, 55)]),
    "MACD": (macd_cross, [(12, 26, 9), (5, 35, 5), (8, 21, 5), (10, 30, 9)]),
    "Bollinger_MR": (bollinger_meanrev, [(20, 1.5), (20, 2.0), (20, 2.5), (50, 2.0)]),
    "Breakout": (breakout, [(20,), (50,), (100,), (200,)]),
}
