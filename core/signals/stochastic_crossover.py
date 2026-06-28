"""Stochastic oscillator crossover signal.

Rules are deliberately mechanical:

* Bullish pulse when %K crosses above %D from an oversold recovery zone.
* Bearish pulse when %K crosses below %D from an overbought rejection zone.
* Optional short hold converts a pulse into a small scalping window.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def stochastic_frame(df: pd.DataFrame, **params) -> pd.DataFrame:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    smooth_k = int(params.get("smooth_k", 3))

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    denom = (highest - lowest).replace(0, np.nan)
    k_raw = 100.0 * (close - lowest) / denom
    k = k_raw.rolling(smooth_k, min_periods=1).mean().clip(0, 100)
    d = k.rolling(d_period, min_periods=1).mean().clip(0, 100)
    return pd.DataFrame({"stoch_k": k, "stoch_d": d}, index=df.index)


def signal(df: pd.DataFrame, **params) -> pd.Series:
    oversold_recover = float(params.get("oversold_recover", 35.0))
    overbought_reject = float(params.get("overbought_reject", 65.0))
    hold_bars = int(params.get("hold_bars", 3))

    st = stochastic_frame(df, **params)
    k = st["stoch_k"]
    d = st["stoch_d"]

    prev_k = k.shift(1)
    prev_d = d.shift(1)
    cross_up = (prev_k <= prev_d) & (k > d) & (k <= oversold_recover)
    cross_down = (prev_k >= prev_d) & (k < d) & (k >= overbought_reject)

    raw = pd.Series(0, index=df.index, dtype="int8")
    raw[cross_up.fillna(False)] = 1
    raw[cross_down.fillna(False)] = -1

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
