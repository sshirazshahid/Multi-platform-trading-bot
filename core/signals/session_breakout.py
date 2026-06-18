"""Asian Range Breakout (ARB) — session-range volatility-filtered long signal.

Strategy
--------
1. Compute the Asian session (UTC) daily high/low for each calendar day.
2. Gate on a *narrow* range: only arm the day if the rolling ATR%% percentile
   at the first active-window bar sits in the bottom ``vol_pctl_threshold``.
3. During the active window (UTC hour >= ``asian_end_hour``), emit +1 on every
   bar whose high breaks ABOVE the Asian range high.

Strictly causal: the value at row ``i`` uses only rows ``0..i``. The Asian range
for a day is built only from bars whose UTC hour is in
``[asian_start_hour, asian_end_hour)`` of that same calendar day, all of which
precede the active window; the volatility gate is a trailing rolling window that
ends at (and includes) the evaluated bar. Long-biased -> returns ``{0, +1}``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def signal(df: pd.DataFrame, **params) -> pd.Series:
    asian_start_hour = int(params.get("asian_start_hour", 0))
    asian_end_hour = int(params.get("asian_end_hour", 6))
    vol_pctl_threshold = float(params.get("vol_pctl_threshold", 25))
    # range_multiple is a sizing/TP param, not used to shape the {0,+1} signal.

    out = pd.Series(0, index=df.index, dtype="int8")
    if len(df) == 0:
        return out

    utc = pd.to_datetime(df["ts"], unit="s", utc=True)
    date = utc.dt.date
    hour = utc.dt.hour

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # 14-bar Wilder ATR% (causal): uses only prior + current bar.
    prev_close = close.shift()
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    atr_pct = 100.0 * atr / close.replace(0, np.nan)

    # Rolling 30-bar percentile gate (causal): is the *current* atr_pct in the
    # bottom ``vol_pctl_threshold`` percentile of the trailing 30-bar window?
    def _narrow(window: np.ndarray) -> float:
        vals = window[~np.isnan(window)]
        if len(vals) == 0 or np.isnan(window[-1]):
            return 0.0
        thresh = np.percentile(vals, vol_pctl_threshold)
        return 1.0 if window[-1] <= thresh else 0.0

    narrow_gate = atr_pct.rolling(window=30, min_periods=15).apply(_narrow, raw=True)

    work = pd.DataFrame(
        {"_date": date.values, "_hour": hour.values},
        index=df.index,
    )

    for _day, group in work.groupby("_date", sort=False):
        asian_idx = group.index[
            (group["_hour"] >= asian_start_hour) & (group["_hour"] < asian_end_hour)
        ]
        if len(asian_idx) == 0:
            continue

        asian_high = high.loc[asian_idx].max()
        asian_low = low.loc[asian_idx].min()
        if not np.isfinite(asian_high) or (asian_high - asian_low) <= 0:
            continue

        active_idx = group.index[group["_hour"] >= asian_end_hour]
        if len(active_idx) == 0:
            continue

        # Volatility gate evaluated at the first active-window bar (causal).
        first_active = active_idx[0]
        gate = narrow_gate.loc[first_active]
        if pd.isna(gate) or gate < 1.0:
            continue  # range not narrow enough -> skip the whole active window

        # Breakout: +1 on every active-window bar whose high pierces the range high.
        broke = high.loc[active_idx] > asian_high
        out.loc[active_idx[broke.values]] = 1

    return out
