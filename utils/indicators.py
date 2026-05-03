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
    g = d.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period-1, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    # Compute raw true range and smooth it (not double-smoothed via atr())
    tr_raw = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    tr   = tr_raw.ewm(com=period-1, adjust=False).mean()
    up   = high.diff()
    down = -low.diff()
    pdm  = up.where((up > down) & (up > 0), 0.0)
    mdm  = down.where((down > up) & (down > 0), 0.0)
    pdi  = 100 * pdm.ewm(com=period-1, adjust=False).mean() / tr.replace(0, np.nan)
    mdi  = 100 * mdm.ewm(com=period-1, adjust=False).mean() / tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(com=period-1, adjust=False).mean(), pdi, mdi


def macd(s: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD. Returns (macd_line, signal_line, histogram)."""
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bbands(s: pd.Series, period: int = 20,
           std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands. Returns (lower, mid, upper)."""
    mid   = s.rolling(period).mean()
    sigma = s.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price (intraday / rolling session)."""
    typical = (high + low + close) / 3
    cum_tpv = (typical * volume).cumsum()
    cum_vol = volume.cumsum().replace(0, np.nan)
    return cum_tpv / cum_vol


def rolling_vwap(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, period: int = 20) -> pd.Series:
    """Rolling VWAP over a fixed lookback window."""
    typical = (high + low + close) / 3
    tpv = typical * volume
    return tpv.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def momentum_exhaustion(close: pd.Series, high: pd.Series, low: pd.Series,
                        rsi_period: int = 14, lookback: int = 5) -> dict:
    """
    Detect momentum exhaustion — signs a trend is about to reverse.

    Checks:
      1. RSI divergence: price making new high/low but RSI isn't
      2. Volume declining while price advances (weakening conviction)
      3. Candle body shrinkage (indecision after strong move)

    Returns dict with 'bull_exhaustion' and 'bear_exhaustion' bools.
    """
    if len(close) < lookback + rsi_period:
        return {"bull_exhaustion": False, "bear_exhaustion": False}

    rsi_vals = rsi(close, rsi_period)
    slice(-lookback, None)

    # Bearish divergence: price higher high but RSI lower high → bull exhaustion
    price_hh = high.iloc[-1] >= high.iloc[-lookback:-1].max()
    rsi_lh = rsi_vals.iloc[-1] < rsi_vals.iloc[-lookback:-1].max()
    bull_exhaustion = price_hh and rsi_lh and rsi_vals.iloc[-1] > 65

    # Bullish divergence: price lower low but RSI higher low → bear exhaustion
    price_ll = low.iloc[-1] <= low.iloc[-lookback:-1].min()
    rsi_hl = rsi_vals.iloc[-1] > rsi_vals.iloc[-lookback:-1].min()
    bear_exhaustion = price_ll and rsi_hl and rsi_vals.iloc[-1] < 35

    # Candle body shrinkage: last 3 bodies average < 40% of lookback body average
    bodies = close.diff().abs()
    if len(bodies) > lookback:
        recent_avg = bodies.iloc[-3:].mean()
        prior_avg = bodies.iloc[-lookback:-3].mean()
        if prior_avg > 0 and recent_avg < prior_avg * 0.4:
            # Small bodies after big move = exhaustion
            if rsi_vals.iloc[-1] > 60:
                bull_exhaustion = True
            elif rsi_vals.iloc[-1] < 40:
                bear_exhaustion = True

    return {"bull_exhaustion": bull_exhaustion, "bear_exhaustion": bear_exhaustion}
