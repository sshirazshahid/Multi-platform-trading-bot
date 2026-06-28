"""EMA + RSI chart-state detector.

The detector converts common chart reading into causal rules:

* trend regime: fast EMA vs slow EMA, slow EMA vs trend EMA, and trend slope;
* trigger: EMA crossover or pullback/reclaim around the fast/slow EMA zone;
* momentum confirmation: RSI recovery/rejection around the 50 midline.

It emits ``+1`` for bullish continuation/reclaim setups, ``-1`` for bearish
continuation/rejection setups, and ``0`` otherwise. The value at row ``i`` uses
only rows ``0..i`` and is intended as a NEXT-bar position signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    period = max(2, int(period))
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    both_flat = (avg_gain == 0.0) & (avg_loss == 0.0)
    only_gain = (avg_gain > 0.0) & (avg_loss == 0.0)
    only_loss = (avg_loss > 0.0) & (avg_gain == 0.0)
    rsi = rsi.mask(both_flat, 50.0).mask(only_gain, 100.0).mask(only_loss, 0.0)
    return rsi.clip(0.0, 100.0)


def _hold_signal(raw: pd.Series, hold_bars: int) -> pd.Series:
    if hold_bars <= 1:
        return raw.astype("int8")

    out = pd.Series(0, index=raw.index, dtype="int8")
    active = 0
    ttl = 0
    for i, v in enumerate(raw.to_numpy(dtype="int8")):
        if v != 0:
            active = int(v)
            ttl = hold_bars
        if ttl > 0:
            out.iloc[i] = active
            ttl -= 1
        else:
            active = 0
    return out


def ema_rsi_frame(df: pd.DataFrame, **params) -> pd.DataFrame:
    """Return causal EMA/RSI diagnostics for strategy research and tests."""
    fast_ema = int(params.get("fast_ema", 9))
    slow_ema = int(params.get("slow_ema", 21))
    trend_ema = int(params.get("trend_ema", 50))
    rsi_period = int(params.get("rsi_period", 14))
    rsi_signal_ema = int(params.get("rsi_signal_ema", 5))
    trend_slope_bars = int(params.get("trend_slope_bars", 4))

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    fast = close.ewm(span=fast_ema, adjust=False, min_periods=max(3, fast_ema)).mean()
    slow = close.ewm(span=slow_ema, adjust=False, min_periods=max(5, slow_ema)).mean()
    trend = close.ewm(span=trend_ema, adjust=False, min_periods=max(10, trend_ema)).mean()
    rsi = _wilder_rsi(close, rsi_period)
    rsi_ma = rsi.ewm(
        span=max(2, rsi_signal_ema),
        adjust=False,
        min_periods=max(2, rsi_signal_ema),
    ).mean()

    ema_spread_pct = (fast - slow) / close.replace(0.0, np.nan)
    trend_slope_pct = trend.pct_change(max(1, trend_slope_bars))

    ema_cross_up = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
    ema_cross_down = (fast.shift(1) >= slow.shift(1)) & (fast < slow)
    rsi_cross_up = (rsi.shift(1) <= 50.0) & (rsi > 50.0)
    rsi_cross_down = (rsi.shift(1) >= 50.0) & (rsi < 50.0)

    touch_fast = (low <= fast) & (high >= fast)
    touch_slow = (low <= slow) & (high >= slow)
    ema_zone_touch = (touch_fast | touch_slow).rolling(3, min_periods=1).max().astype(bool)

    reclaim_fast = (close > fast) & (close.shift(1) <= fast.shift(1))
    reject_fast = (close < fast) & (close.shift(1) >= fast.shift(1))

    return pd.DataFrame(
        {
            "ema_fast": fast,
            "ema_slow": slow,
            "ema_trend": trend,
            "ema_spread_pct": ema_spread_pct.replace([np.inf, -np.inf], np.nan),
            "trend_slope_pct": trend_slope_pct.replace([np.inf, -np.inf], np.nan),
            "rsi": rsi,
            "rsi_ma": rsi_ma,
            "ema_cross_up": ema_cross_up.fillna(False),
            "ema_cross_down": ema_cross_down.fillna(False),
            "rsi_cross_up": rsi_cross_up.fillna(False),
            "rsi_cross_down": rsi_cross_down.fillna(False),
            "ema_zone_touch": ema_zone_touch.fillna(False),
            "reclaim_fast": reclaim_fast.fillna(False),
            "reject_fast": reject_fast.fillna(False),
        },
        index=df.index,
    )


def signal(df: pd.DataFrame, **params) -> pd.Series:
    """Return EMA/RSI trend-continuation signals in ``{-1, 0, +1}``."""
    hold_bars = int(params.get("hold_bars", 3))
    min_trend_slope = float(params.get("min_trend_slope", 0.00005))
    min_ema_spread_pct = float(params.get("min_ema_spread_pct", 0.0002))
    max_ema_spread_pct = float(params.get("max_ema_spread_pct", 0.035))
    long_rsi_floor = float(params.get("long_rsi_floor", 45.0))
    long_rsi_ceiling = float(params.get("long_rsi_ceiling", 70.0))
    short_rsi_floor = float(params.get("short_rsi_floor", 30.0))
    short_rsi_ceiling = float(params.get("short_rsi_ceiling", 55.0))

    out = pd.Series(0, index=df.index, dtype="int8")
    if len(df) == 0:
        return out

    f = ema_rsi_frame(df, **params)
    close = df["close"].astype(float)

    spread = f["ema_spread_pct"]
    abs_spread = spread.abs()
    warm = f[["ema_fast", "ema_slow", "ema_trend", "rsi", "rsi_ma"]].notna().all(axis=1)
    spread_ok = (abs_spread >= min_ema_spread_pct) & (abs_spread <= max_ema_spread_pct)

    long_regime = (
        warm
        & spread_ok
        & (f["ema_fast"] > f["ema_slow"])
        & (f["ema_slow"] >= f["ema_trend"])
        & (close >= f["ema_trend"])
        & (f["trend_slope_pct"] >= min_trend_slope)
    )
    short_regime = (
        warm
        & spread_ok
        & (f["ema_fast"] < f["ema_slow"])
        & (f["ema_slow"] <= f["ema_trend"])
        & (close <= f["ema_trend"])
        & (f["trend_slope_pct"] <= -min_trend_slope)
    )

    rsi_long_quality = f["rsi"].between(long_rsi_floor, long_rsi_ceiling, inclusive="both")
    rsi_short_quality = f["rsi"].between(short_rsi_floor, short_rsi_ceiling, inclusive="both")
    rsi_rising = (f["rsi"] >= f["rsi_ma"]) | (f["rsi"] > f["rsi"].shift(1))
    rsi_falling = (f["rsi"] <= f["rsi_ma"]) | (f["rsi"] < f["rsi"].shift(1))

    long_cross = f["ema_cross_up"] & (f["rsi"] >= long_rsi_floor) & (f["rsi"] <= long_rsi_ceiling)
    short_cross = f["ema_cross_down"] & (f["rsi"] >= short_rsi_floor) & (f["rsi"] <= short_rsi_ceiling)
    long_reclaim = f["ema_zone_touch"] & f["reclaim_fast"] & rsi_long_quality & rsi_rising
    short_reject = f["ema_zone_touch"] & f["reject_fast"] & rsi_short_quality & rsi_falling
    long_midline = f["rsi_cross_up"] & (close >= f["ema_fast"]) & rsi_long_quality
    short_midline = f["rsi_cross_down"] & (close <= f["ema_fast"]) & rsi_short_quality

    raw = pd.Series(0, index=df.index, dtype="int8")
    raw[long_regime & (long_cross | long_reclaim | long_midline)] = 1
    raw[short_regime & (short_cross | short_reject | short_midline)] = -1
    return _hold_signal(raw, hold_bars)
