"""
utils/indicators.py — Pure pandas technical indicator functions.

Shared by mcp_brain.py (multi-TF exchange indicators) and strategy_selector.py.
No strategy logic, no exchange I/O — just math on price series.
"""

import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return s.ewm(span=period, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return s.rolling(period).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    d = s.diff()
    g = d.clip(lower=0).ewm(span=period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=period, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    tr   = atr(high, low, close, period)
    up   = high.diff()
    down = -low.diff()
    pdm  = up.where((up > down) & (up > 0), 0.0)
    mdm  = down.where((down > up) & (down > 0), 0.0)
    pdi  = 100 * pdm.ewm(span=period, adjust=False).mean() / tr.replace(0, np.nan)
    mdi  = 100 * mdm.ewm(span=period, adjust=False).mean() / tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean(), pdi, mdi


def bbands(s: pd.Series, period: int = 20,
           std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands. Returns (lower, mid, upper)."""
    mid   = s.rolling(period).mean()
    sigma = s.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma
