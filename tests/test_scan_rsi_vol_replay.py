"""Unit tests for the pure signal functions in research/scan_rsi_vol_replay.py.

Synthetic fixtures only — no network, no cache, no live code touched.
"""

import numpy as np
import pandas as pd

from research.scan_rsi_vol_replay import (
    ACCURACY_TP_FLOOR_PCT,
    VOL_MULT,
    accuracy_tp_pct,
    compute_rsi,
    confluence_mask,
    entry_barriers,
    trailing_vol_ratio,
)


def test_rsi_oversold_on_monotonic_decline():
    """A long, steady price decline drives RSI well below 30."""
    closes = pd.Series(np.linspace(100.0, 60.0, 60))
    r = compute_rsi(closes)
    assert r.iloc[-1] < 30.0
    # A rising series with real pullbacks pins RSI above 70. The pullbacks
    # (-1) must exceed the up-steps' noise so genuine down-bars exist, avoiding
    # the zero-losses -> NaN degenerate case of the Wilder RSI.
    steps = np.tile([3.0, 3.0, 3.0, -1.0], 20)
    up = pd.Series(60.0 + np.cumsum(steps))
    assert compute_rsi(up).iloc[-1] > 70.0


def test_trailing_vol_ratio_excludes_spike_bar():
    """Baseline is the 20 prior bars; a 4x spike after a flat baseline reads ~4.0."""
    vol = pd.Series([10.0] * 20 + [40.0])  # 21 bars; last is the spike
    vr = trailing_vol_ratio(vol, window=20)
    assert np.isnan(vr.iloc[0])            # warmup: no baseline yet
    assert abs(vr.iloc[-1] - 4.0) < 1e-9   # 40 / mean(20*[10]) == 4.0
    assert vr.iloc[-1] >= VOL_MULT         # clears the +200% gate


def test_confluence_requires_both_conditions():
    """Confluence fires only when RSI<30 AND vol>=3x baseline coincide."""
    n = 40
    # Declining price -> oversold RSI by the end.
    closes = pd.Series(np.linspace(100.0, 55.0, n))
    volume = pd.Series([10.0] * n)
    # Oversold but NO volume spike -> no confluence.
    assert not confluence_mask(closes, volume).iloc[-1]
    # Add a 5x volume spike on the final (oversold) bar -> confluence True.
    volume.iloc[-1] = 50.0
    assert confluence_mask(closes, volume).iloc[-1]
    # Volume spike on a NON-oversold bar (flat/high RSI) -> no confluence.
    flat = pd.Series([100.0] * n)
    volspike = pd.Series([10.0] * n)
    volspike.iloc[-1] = 50.0
    assert not confluence_mask(flat, volspike).iloc[-1]


def test_entry_barriers_long_and_short():
    """SL below / TP above for longs; mirrored for shorts (pct off close)."""
    sl_px, tp_px = entry_barriers("buy", 100.0, sl_pct=2.0, tp_pct=4.0)
    assert abs(sl_px - 98.0) < 1e-9
    assert abs(tp_px - 104.0) < 1e-9
    sl_px, tp_px = entry_barriers("sell", 100.0, sl_pct=2.0, tp_pct=4.0)
    assert abs(sl_px - 102.0) < 1e-9
    assert abs(tp_px - 96.0) < 1e-9


def test_accuracy_tp_pct_geometry_and_floor():
    """tp = 0.45 x SL, but never below the 0.5% floor."""
    assert abs(accuracy_tp_pct(4.0) - 1.8) < 1e-9        # 0.45 * 4.0
    assert accuracy_tp_pct(0.5) == ACCURACY_TP_FLOOR_PCT  # floored
    assert accuracy_tp_pct(1.0) == ACCURACY_TP_FLOOR_PCT  # 0.45 < 0.5 -> floor
