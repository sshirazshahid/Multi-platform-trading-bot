"""Triple-barrier labeler for the LR model layer (Phase 3.1).

Each candidate trade is assigned a label of `+1`, `-1`, or `0` based on
which of three barriers fires first within a forward window:

  * `+1` — take-profit (TP) wick is touched first.
  * `-1` — stop-loss (SL) wick is touched first.
  *  `0` — neither barrier touched within `time_bars`; the time bar fires.

Detection uses realized OHLC wicks (`high` / `low`) rather than closes —
a wick that touches TP and reverses still counts as a TP hit, mirroring
how an exchange-side stop or take-profit order would actually trigger.

Tie-break: when a single bar's `[low, high]` envelope contains BOTH
barriers, we resolve to `-1` (SL first). This is the conservative choice
recommended by López de Prado (AFML Ch.3) because intra-bar order is
unobservable and assuming TP-first inflates the empirical win rate.

Reference: López de Prado, "Advances in Financial Machine Learning" Ch.3.

Used by: `scripts/train_models.py` (Phase 3.2) to label every candidate
in the warehouse for the logistic regression fit. The `time_bars` horizon
is parameterised so the same function serves multiple holding periods.
"""
from __future__ import annotations

import numpy as np

_VALID_SIDES = ("long", "short")


def triple_barrier(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    entry_idx: int,
    side: str,
    tp_pct: float,
    sl_pct: float,
    time_bars: int,
) -> int:
    """Label a single entry via the triple-barrier method.

    Parameters
    ----------
    highs, lows, closes : np.ndarray
        Aligned OHLC arrays of equal length. `closes[entry_idx]` is the
        entry price.
    entry_idx : int
        Index into `closes` where the position opens. The forward window
        starts at `entry_idx + 1` (the entry bar itself never triggers).
    side : {"long", "short"}
        Direction of the candidate position.
    tp_pct : float
        Take-profit distance as a positive fraction of entry (e.g. 0.02
        for 2%).
    sl_pct : float
        Stop-loss distance as a positive fraction of entry.
    time_bars : int
        Maximum number of forward bars to scan before the time barrier
        fires. Must be >= 0.

    Returns
    -------
    int
        +1 if TP hit first, -1 if SL hit first, 0 if time-bar fired.

    Raises
    ------
    ValueError
        If `side` is not 'long' / 'short', `entry_idx` is out of range,
        `tp_pct` or `sl_pct` is negative, or arrays have mismatched
        lengths.
    """
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)

    n = closes.shape[0]
    if highs.shape[0] != n or lows.shape[0] != n:
        raise ValueError("highs, lows, closes must be the same length")
    if side not in _VALID_SIDES:
        raise ValueError(f"side must be one of {_VALID_SIDES}, got {side!r}")
    if entry_idx < 0 or entry_idx >= n:
        raise ValueError(f"entry_idx={entry_idx} is out of range for length {n}")
    if tp_pct < 0:
        raise ValueError(f"tp_pct must be >= 0, got {tp_pct}")
    if sl_pct < 0:
        raise ValueError(f"sl_pct must be >= 0, got {sl_pct}")
    if time_bars < 0:
        raise ValueError(f"time_bars must be >= 0, got {time_bars}")
    if time_bars == 0:
        return 0

    entry = float(closes[entry_idx])
    end = min(entry_idx + 1 + time_bars, n)
    if end <= entry_idx + 1:
        return 0

    window_high = highs[entry_idx + 1:end]
    window_low = lows[entry_idx + 1:end]

    if side == "long":
        tp_lvl = entry * (1.0 + tp_pct)
        sl_lvl = entry * (1.0 - sl_pct)
        tp_hits = window_high >= tp_lvl
        sl_hits = window_low <= sl_lvl
    else:  # short
        tp_lvl = entry * (1.0 - tp_pct)
        sl_lvl = entry * (1.0 + sl_pct)
        tp_hits = window_low <= tp_lvl
        sl_hits = window_high >= sl_lvl

    # First-touch bar for each barrier (np.argmax on bool returns first True).
    tp_any = bool(tp_hits.any())
    sl_any = bool(sl_hits.any())

    if not tp_any and not sl_any:
        return 0
    if tp_any and not sl_any:
        return 1
    if sl_any and not tp_any:
        return -1

    tp_first = int(np.argmax(tp_hits))
    sl_first = int(np.argmax(sl_hits))
    if sl_first < tp_first:
        return -1
    if tp_first < sl_first:
        return 1
    # Same bar — conservative tie-break: SL fires first.
    return -1
