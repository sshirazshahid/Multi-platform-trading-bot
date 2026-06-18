"""Dow Swing Structure — long-biased swing-pivot signal with fractal confirmation.

Structure rules (all causal; row ``i`` uses only rows ``0..i``):

* For each bar ``i`` with at least ``k_swing`` prior bars, look at the window of
  the ``k_swing`` bars immediately preceding ``i``.
* ``higher_high``  : ``high[i]  > max(high[i-k .. i-1])``
* ``higher_low``   : ``low[i]   > min(low[i-k  .. i-1])``
* ``lower_low``    : ``low[i]   < min(low[i-k  .. i-1])``   (structure break)
* An **uptrend** is *confirmed* on the bar where BOTH ``higher_high`` and
  ``higher_low`` hold. The confirmation latches and persists.
* While the uptrend is confirmed, an **entry (+1)** fires on the first
  *pullback low* — a bar whose low is above the most-recent pivot-low and that
  does not itself break structure. Once long, the signal stays +1.
* A **lower_low** while in an uptrend is a **structure break**: it cancels the
  uptrend latch and returns the signal to 0 until a fresh HH+HL re-confirms.

Long-biased -> returns ``{0, +1}``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def signal(df: pd.DataFrame, **params) -> pd.Series:
    k = int(params.get("k_swing", 5))

    n = len(df)
    out = pd.Series(0, index=df.index, dtype="int8")
    if n == 0:
        return out

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)

    uptrend = False  # is an uptrend currently confirmed?
    in_position = False  # are we holding a long?
    pivot_low = np.nan  # most-recent pivot (swing) low while in the trend

    for i in range(n):
        if i < k:
            out.iloc[i] = 1 if in_position else 0
            continue

        prior_high_max = np.max(high[i - k : i])
        prior_low_min = np.min(low[i - k : i])

        higher_high = high[i] > prior_high_max
        higher_low = low[i] > prior_low_min
        lower_low = low[i] < prior_low_min

        if uptrend and lower_low:
            # Structure break -> cancel trend and exit.
            uptrend = False
            in_position = False
            pivot_low = np.nan
            out.iloc[i] = 0
            continue

        if not uptrend:
            # Look for fresh HH + HL to confirm a new uptrend.
            if higher_high and higher_low:
                uptrend = True
                pivot_low = prior_low_min
                # The confirming bar made a higher low above the prior pivot-low:
                # that IS the first pullback-above-pivot, so enter immediately.
                in_position = True
            out.iloc[i] = 1 if in_position else 0
            continue

        # uptrend already confirmed (and not broken this bar)
        if not in_position:
            # Enter on the first pullback low that holds above the pivot-low.
            if low[i] > pivot_low:
                in_position = True
        # Refresh pivot-low as new swing lows form (still above prior pivot).
        if higher_low:
            pivot_low = prior_low_min

        out.iloc[i] = 1 if in_position else 0

    return out
