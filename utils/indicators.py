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
    g = d.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    # Compute raw true range and smooth it (not double-smoothed via atr())
    tr_raw = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr = tr_raw.ewm(com=period - 1, adjust=False).mean()
    up = high.diff()
    down = -low.diff()
    pdm = up.where((up > down) & (up > 0), 0.0)
    mdm = down.where((down > up) & (down > 0), 0.0)
    pdi = 100 * pdm.ewm(com=period - 1, adjust=False).mean() / tr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(com=period - 1, adjust=False).mean() / tr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(com=period - 1, adjust=False).mean(), pdi, mdi


def macd(
    s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD. Returns (macd_line, signal_line, histogram)."""
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bbands(
    s: pd.Series, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands. Returns (lower, mid, upper)."""
    mid = s.rolling(period).mean()
    sigma = s.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price (intraday / rolling session)."""
    typical = (high + low + close) / 3
    cum_tpv = (typical * volume).cumsum()
    cum_vol = volume.cumsum().replace(0, np.nan)
    return cum_tpv / cum_vol


def rolling_vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20
) -> pd.Series:
    """Rolling VWAP over a fixed lookback window."""
    typical = (high + low + close) / 3
    tpv = typical * volume
    return tpv.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def bollinger_bands(
    close: pd.Series, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands. Returns (lower, mid, upper). Alias-shaped twin of bbands."""
    mid = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma


def keltner_channel(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20, mult: float = 1.5
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel. Returns (lower, mid, upper) = EMA(close) ± ATR*mult."""
    mid = ema(close, period)
    band = atr(high, low, close, period) * mult
    return mid - band, mid, mid + band


def bb_squeeze(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
) -> pd.Series:
    """
    TTM-style squeeze. True when the Bollinger Bands sit INSIDE the Keltner
    Channel (low volatility coiling before a breakout).

    Fallback: where Keltner is undefined (warmup), or as a secondary signal,
    fall back to a BB-width percentile — squeeze when BB width is in the
    lowest 25% of its own trailing window. The two are OR-combined so a
    squeeze fires when either the channel test or the width-percentile test
    flags low volatility. Strictly causal (rolling/expanding only).
    """
    bb_lower, bb_mid, bb_upper = bollinger_bands(close, bb_period, bb_std)
    kc_lower, _, kc_upper = keltner_channel(high, low, close, kc_period, kc_mult)

    # Primary: BB inside KC.
    inside = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Fallback: BB-width percentile vs its trailing window.
    width = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan)
    # CAUSAL: rank bar i's width only against bars 0..i (its own past and itself);
    # w[:-1] are strictly prior bars and w[-1] is the decision bar, so no bar > i
    # is ever referenced. Equivalent to expanding().apply over (w <= w[-1]).mean().
    rank = width.expanding(min_periods=bb_period).apply(
        lambda w: float((w[:-1] <= w[-1]).sum() + 1) / len(w), raw=True
    )
    width_low = rank <= 0.25

    squeeze = inside.fillna(False) | width_low.fillna(False)
    return squeeze.astype(bool)


def swing_points(
    high: pd.Series, low: pd.Series, left: int = 2, right: int = 2
) -> tuple[pd.Series, pd.Series]:
    """
    Confirmed fractal pivots. Returns (pivot_high, pivot_low) boolean Series.

    A pivot high at bar i is True when high[i] is the strict maximum of the
    window [i-left, i+right]. CAUSAL: a pivot at i is only confirmable `right`
    bars later, so the boolean is placed at i but cannot be known until bar
    i+right. We never set a pivot using bars that would be in the future at
    decision time — the last `right` bars are therefore always False.
    """
    n = len(high)
    ph = pd.Series(False, index=high.index)
    pl = pd.Series(False, index=low.index)
    hv = high.to_numpy()
    lv = low.to_numpy()
    for i in range(left, n - right):
        win_h = hv[i - left : i + right + 1]
        if hv[i] == win_h.max() and (win_h == hv[i]).sum() == 1:
            ph.iloc[i] = True
        win_l = lv[i - left : i + right + 1]
        if lv[i] == win_l.min() and (win_l == lv[i]).sum() == 1:
            pl.iloc[i] = True
    return ph, pl


def session_range(df: pd.DataFrame, start_hour: int, end_hour: int) -> tuple[pd.Series, pd.Series]:
    """
    Rolling (session_high, session_low) over bars whose UTC hour is in
    [start_hour, end_hour] inclusive. UTC hour is derived from the `ts` column
    (unix SECONDS). CAUSAL: at bar i the running extreme reflects only session
    bars at index <= i (cumulative max/min, never forward-looking). Non-session
    bars carry the last known session extreme forward; before the first session
    bar the value is NaN.
    """
    ts = df["ts"].to_numpy()
    hours = (ts // 3600) % 24
    in_session = (hours >= start_hour) & (hours <= end_hour)

    sess_high = df["high"].where(in_session)
    sess_low = df["low"].where(in_session)

    run_high = sess_high.cummax()
    run_low = sess_low.cummin()
    return run_high, run_low


def momentum_exhaustion(
    close: pd.Series, high: pd.Series, low: pd.Series, rsi_period: int = 14, lookback: int = 5
) -> dict:
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
