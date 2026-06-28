"""Causal fair-value deviation signal.

This is a machine-style "valuation" model for liquid, continuously-traded
assets where true fundamentals are sparse or slow. It estimates short-horizon
fair value from causal price/volume history and emits:

* ``+1`` when price is materially below fair value (mean-reversion long)
* ``-1`` when price is materially above fair value (mean-reversion short)
* ``0`` otherwise

No future data, no labels, no discretionary chart reading.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def fair_value_frame(df: pd.DataFrame, **params) -> pd.DataFrame:
    """Return fair-value diagnostics.

    Required columns: ``close`` and optionally ``volume``. All rolling/EMA
    calculations are causal; row ``i`` uses only rows ``0..i``.
    """
    lookback = int(params.get("lookback", 96))
    ema_span = int(params.get("ema_span", max(8, lookback // 2)))

    close = df["close"].astype(float)
    volume = df.get("volume", pd.Series(1.0, index=df.index)).astype(float)
    volume = volume.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    ema = close.ewm(span=ema_span, adjust=False, min_periods=max(3, ema_span // 3)).mean()
    pv = (close * volume).rolling(lookback, min_periods=max(5, lookback // 4)).sum()
    vv = volume.rolling(lookback, min_periods=max(5, lookback // 4)).sum()
    vwap = pv / vv.replace(0, np.nan)
    fair = (0.55 * ema + 0.45 * vwap).where(vwap.notna(), ema)

    deviation = (close - fair) / fair.replace(0, np.nan)
    dev_std = deviation.rolling(lookback, min_periods=max(10, lookback // 3)).std(ddof=0)
    z = deviation / dev_std.replace(0, np.nan)

    return pd.DataFrame(
        {
            "fair_value": fair,
            "deviation_pct": deviation * 100.0,
            "valuation_z": z.replace([np.inf, -np.inf], np.nan),
        },
        index=df.index,
    )


def signal(df: pd.DataFrame, **params) -> pd.Series:
    """Return side-aware mean-reversion valuation signal in ``{-1, 0, +1}``."""
    z_entry = float(params.get("z_entry", 1.25))
    hold_bars = int(params.get("hold_bars", 3))

    diag = fair_value_frame(df, **params)
    z = diag["valuation_z"]
    raw = pd.Series(0, index=df.index, dtype="int8")
    raw[z <= -z_entry] = 1
    raw[z >= z_entry] = -1

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
