"""Tests for core.labeler.triple_barrier (Phase 3.1).

Coverage:
  * up-only / down-only / flat synthetic paths (long and short)
  * wick-touch first-touch detection (high/low not just close)
  * both-barriers-on-same-bar tie-break (conservative SL-first)
  * symmetric random walks with wide barriers → mostly time-bar (0)
  * edge cases (entry at last bar, out-of-range, negative pcts)
  * vectorization sanity (1000 entries on shared OHLC < 0.5s)
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from core.labeler import triple_barrier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohlc_from_closes(closes: np.ndarray, wick_pct: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build (highs, lows, closes) from a closes path with optional symmetric wicks."""
    closes = np.asarray(closes, dtype=float)
    highs = closes * (1.0 + wick_pct)
    lows = closes * (1.0 - wick_pct)
    return highs, lows, closes


# ---------------------------------------------------------------------------
# Long-side synthetic paths
# ---------------------------------------------------------------------------

def test_up_only_path_long_returns_plus_one():
    closes = np.linspace(100.0, 110.0, 21)  # +10% over 20 bars
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == 1


def test_down_only_path_long_returns_minus_one():
    closes = np.linspace(100.0, 90.0, 21)  # -10% over 20 bars
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == -1


def test_flat_path_long_returns_zero():
    closes = np.full(21, 100.0)
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == 0


def test_wick_touches_tp_then_reverses_long_returns_plus_one():
    """High wick touches TP on bar 1 (low stays above SL), then close ends below entry — first-touch wins."""
    closes = np.array([100.0, 99.5, 98.7, 97.5, 96.0])  # closes drift down
    highs = np.array([100.0, 102.5, 99.6, 98.6, 97.0])  # bar 1 wick > +2% TP
    lows = np.array([100.0, 99.0, 97.5, 96.5, 95.5])    # bar 1 low above SL (98.5)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=4)
    assert label == 1


# ---------------------------------------------------------------------------
# Short-side mirror
# ---------------------------------------------------------------------------

def test_down_only_path_short_returns_plus_one():
    closes = np.linspace(100.0, 90.0, 21)
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="short",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == 1


def test_up_only_path_short_returns_minus_one():
    closes = np.linspace(100.0, 110.0, 21)
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="short",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == -1


def test_flat_path_short_returns_zero():
    closes = np.full(21, 100.0)
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="short",
                           tp_pct=0.02, sl_pct=0.015, time_bars=20)
    assert label == 0


def test_wick_touches_tp_then_reverses_short_returns_plus_one():
    """Low wick touches short TP on bar 1 (high stays below SL), then closes recover — first-touch wins."""
    closes = np.array([100.0, 100.5, 101.3, 102.5, 104.0])  # closes drift up (against short)
    highs = np.array([100.0, 101.0, 102.0, 103.0, 104.5])    # bar 1 high below short SL (101.5)
    lows = np.array([100.0, 97.5, 100.5, 101.5, 102.5])      # bar 1 wick < -2% TP for short
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="short",
                           tp_pct=0.02, sl_pct=0.015, time_bars=4)
    assert label == 1


# ---------------------------------------------------------------------------
# Both-barriers-same-bar tie-break (conservative: SL fires first)
# ---------------------------------------------------------------------------

def test_both_barriers_same_bar_long_resolves_to_sl():
    """When a bar's [low, high] envelope contains both TP and SL, label as -1 (conservative)."""
    closes = np.array([100.0, 100.0])
    highs = np.array([100.0, 103.0])  # touches +2% TP
    lows = np.array([100.0, 98.0])    # also touches -1.5% SL
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=1)
    assert label == -1


def test_both_barriers_same_bar_short_resolves_to_sl():
    closes = np.array([100.0, 100.0])
    highs = np.array([100.0, 102.0])  # touches +1.5% (short SL)
    lows = np.array([100.0, 97.0])    # also touches -2% (short TP)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="short",
                           tp_pct=0.02, sl_pct=0.015, time_bars=1)
    assert label == -1


# ---------------------------------------------------------------------------
# Symmetric random walks: wide barriers → mostly 0
# ---------------------------------------------------------------------------

def test_symmetric_random_walks_wide_barriers_mostly_zero():
    rng = np.random.default_rng(seed=42)
    n_paths = 100
    n_bars = 30
    zero_count = 0
    for _ in range(n_paths):
        # tiny per-bar vol relative to barriers
        rets = rng.normal(loc=0.0, scale=0.002, size=n_bars)
        closes = 100.0 * np.exp(np.cumsum(rets))
        highs, lows, closes_arr = _ohlc_from_closes(closes, wick_pct=0.0005)
        label = triple_barrier(highs, lows, closes_arr, entry_idx=0, side="long",
                               tp_pct=0.05, sl_pct=0.05, time_bars=n_bars - 1)
        if label == 0:
            zero_count += 1
    assert zero_count / n_paths > 0.7, f"only {zero_count}/{n_paths} were time-stops"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_entry_at_last_bar_returns_zero():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=2, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=10)
    assert label == 0


def test_entry_beyond_array_raises():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    with pytest.raises(ValueError):
        triple_barrier(highs, lows, closes, entry_idx=5, side="long",
                       tp_pct=0.02, sl_pct=0.015, time_bars=10)


def test_negative_entry_idx_raises():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    with pytest.raises(ValueError):
        triple_barrier(highs, lows, closes, entry_idx=-1, side="long",
                       tp_pct=0.02, sl_pct=0.015, time_bars=10)


def test_negative_tp_pct_raises():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    with pytest.raises(ValueError):
        triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                       tp_pct=-0.02, sl_pct=0.015, time_bars=2)


def test_negative_sl_pct_raises():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    with pytest.raises(ValueError):
        triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                       tp_pct=0.02, sl_pct=-0.015, time_bars=2)


def test_invalid_side_raises():
    closes = np.array([100.0, 101.0, 102.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    with pytest.raises(ValueError):
        triple_barrier(highs, lows, closes, entry_idx=0, side="sideways",
                       tp_pct=0.02, sl_pct=0.015, time_bars=2)


def test_zero_time_bars_returns_zero():
    closes = np.array([100.0, 105.0, 110.0])
    highs, lows, closes = _ohlc_from_closes(closes)
    label = triple_barrier(highs, lows, closes, entry_idx=0, side="long",
                           tp_pct=0.02, sl_pct=0.015, time_bars=0)
    assert label == 0


# ---------------------------------------------------------------------------
# Vectorization sanity: 1000 entries < 0.5s
# ---------------------------------------------------------------------------

def test_vectorization_sanity_1000_entries_under_half_second():
    rng = np.random.default_rng(seed=0)
    n_bars = 5000
    rets = rng.normal(loc=0.0, scale=0.003, size=n_bars)
    closes = 100.0 * np.exp(np.cumsum(rets))
    highs, lows, closes_arr = _ohlc_from_closes(closes, wick_pct=0.001)

    n_entries = 1000
    entry_idxs = rng.integers(0, n_bars - 50, size=n_entries)
    sides = rng.choice(["long", "short"], size=n_entries)

    t0 = time.perf_counter()
    labels = [
        triple_barrier(highs, lows, closes_arr, int(eidx), str(side),
                       tp_pct=0.02, sl_pct=0.015, time_bars=24)
        for eidx, side in zip(entry_idxs, sides)
    ]
    elapsed = time.perf_counter() - t0
    assert len(labels) == n_entries
    assert all(label_val in (-1, 0, 1) for label_val in labels)
    assert elapsed < 0.5, f"1000 entries took {elapsed:.3f}s (>0.5s threshold)"
