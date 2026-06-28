"""ICT-inspired liquidity sweep / imbalance signal.

This is not discretionary ICT. It is a strict, causal variation:

Bullish setup:
* current low sweeps below the prior rolling liquidity low;
* current close reclaims that prior low;
* and either a bullish fair-value gap exists or the candle shows displacement.

Bearish setup mirrors the above around prior liquidity highs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ict_frame(df: pd.DataFrame, **params) -> pd.DataFrame:
    lookback = int(params.get("liquidity_lookback", 24))
    body_atr_mult = float(params.get("body_atr_mult", 0.8))

    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prior_high = high.rolling(lookback, min_periods=max(3, lookback // 3)).max().shift(1)
    prior_low = low.rolling(lookback, min_periods=max(3, lookback // 3)).min().shift(1)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    body = (close - open_).abs()

    bull_sweep = (low < prior_low) & (close > prior_low)
    bear_sweep = (high > prior_high) & (close < prior_high)

    bull_fvg = low > high.shift(2)
    bear_fvg = high < low.shift(2)
    bull_displacement = (close > open_) & (body >= atr * body_atr_mult)
    bear_displacement = (close < open_) & (body >= atr * body_atr_mult)

    return pd.DataFrame(
        {
            "prior_liquidity_high": prior_high,
            "prior_liquidity_low": prior_low,
            "bull_sweep": bull_sweep.fillna(False),
            "bear_sweep": bear_sweep.fillna(False),
            "bull_fvg": bull_fvg.fillna(False),
            "bear_fvg": bear_fvg.fillna(False),
            "bull_displacement": bull_displacement.fillna(False),
            "bear_displacement": bear_displacement.fillna(False),
        },
        index=df.index,
    )


def signal(df: pd.DataFrame, **params) -> pd.Series:
    hold_bars = int(params.get("hold_bars", 4))
    require_imbalance = bool(params.get("require_imbalance", True))
    f = ict_frame(df, **params)

    bull_quality = f["bull_fvg"] | f["bull_displacement"]
    bear_quality = f["bear_fvg"] | f["bear_displacement"]
    if require_imbalance:
        bull = f["bull_sweep"] & bull_quality
        bear = f["bear_sweep"] & bear_quality
    else:
        bull = f["bull_sweep"]
        bear = f["bear_sweep"]

    raw = pd.Series(0, index=df.index, dtype="int8")
    raw[bull] = 1
    raw[bear] = -1

    if hold_bars <= 1:
        return raw

    out = pd.Series(0, index=df.index, dtype="int8")
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
