"""Bollinger Squeeze Breakout (BSB) — long-only volatility-release signal.

Squeeze detection (causal):
* Bollinger Bands ``(bb_period, bb_std)`` around the SMA.
* Keltner Channel ``(kc_period, kc_mult)`` around the same SMA, ATR = mean
  (high - low).
* ``is_squeezed`` = BB bandwidth in the bottom ``squeeze_pctl`` percentile of a
  trailing 60-bar window OR Bollinger Bands nested inside the Keltner Channel.

Entry: a squeeze *release* (squeezed at ``i-1``, not squeezed at ``i``) AND the
close breaks above the prior-bar upper Bollinger Band. Once entered, the long is
held for at least ``min_bars_held`` bars (external SL/TP closes thereafter).

Strictly causal: every rolling stat and the ``.shift(1)`` release/breakout
checks use only rows ``0..i``. Long-biased -> returns ``{0, +1}``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _bands(df: pd.DataFrame, **params):
    """Return (bb_upper, bb_lower, bb_width, kc_upper, kc_lower, is_squeezed)."""
    bb_period = int(params.get("bb_period", 20))
    bb_std = float(params.get("bb_std", 2.0))
    kc_period = int(params.get("kc_period", 20))
    kc_mult = float(params.get("kc_mult", 1.5))
    squeeze_pctl = float(params.get("squeeze_pctl", 15))

    close = df["close"]
    high = df["high"]
    low = df["low"]

    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std(ddof=0)
    bb_upper = sma + bb_std * std
    bb_lower = sma - bb_std * std
    bb_width = bb_upper - bb_lower

    atr = (high - low).rolling(kc_period).mean()
    kc_upper = sma + kc_mult * atr
    kc_lower = sma - kc_mult * atr

    def _bottom_pctl(window: np.ndarray) -> float:
        vals = window[~np.isnan(window)]
        if len(vals) == 0 or np.isnan(window[-1]):
            return 0.0
        thresh = np.percentile(vals, squeeze_pctl)
        return 1.0 if window[-1] <= thresh else 0.0

    width_squeeze = (
        bb_width.rolling(window=60, min_periods=bb_period)
        .apply(_bottom_pctl, raw=True)
        .fillna(0.0)
        .astype(bool)
    )

    nested = (bb_upper <= kc_upper) & (bb_lower >= kc_lower)
    nested = nested.fillna(False)

    is_squeezed = width_squeeze | nested
    return bb_upper, bb_lower, bb_width, kc_upper, kc_lower, is_squeezed


def squeeze_state(df: pd.DataFrame, **params) -> pd.Series:
    """Expose the boolean ``is_squeezed`` series (test helper / introspection)."""
    _, _, _, _, _, is_squeezed = _bands(df, **params)
    return is_squeezed


def signal(df: pd.DataFrame, **params) -> pd.Series:
    min_bars_held = int(params.get("min_bars_held", 6))

    out = pd.Series(0, index=df.index, dtype="int8")
    if len(df) == 0:
        return out

    bb_upper, _, _, _, _, is_squeezed = _bands(df, **params)
    close = df["close"]

    # Squeeze release: was squeezed last bar, not squeezed now (causal).
    prev_squeezed = is_squeezed.shift(1, fill_value=False)
    squeeze_released = prev_squeezed & ~is_squeezed
    # Breakout above the prior-bar upper band (causal).
    breakout = close > bb_upper.shift(1)
    entry = (squeeze_released & breakout).to_numpy()

    in_position = False
    bars_held = 0
    sig = np.zeros(len(df), dtype="int8")

    for i in range(len(df)):
        if entry[i] and not in_position:
            in_position = True
            bars_held = 0
        if in_position:
            bars_held += 1
            sig[i] = 1
            # After the minimum hold, hand control to external SL/TP: flatten so
            # a downstream exit can act on the next bar.
            if bars_held >= min_bars_held:
                in_position = False

    out.iloc[:] = sig
    return out
