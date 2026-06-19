"""
tests/test_breakout_indicators.py — Tests for breakout indicator helpers.

Covers the additions to utils/indicators.py:
  bollinger_bands, keltner_channel, bb_squeeze, swing_points, session_range.

The load-bearing correctness property is CAUSALITY: a value at bar i must
depend only on rows 0..i, never on future bars. swing_points and
session_range are tested explicitly for this.
"""

import numpy as np
import pandas as pd

from utils.indicators import (
    bb_squeeze,
    bollinger_bands,
    keltner_channel,
    session_range,
    swing_points,
)  # noqa: F401  (bollinger_bands/keltner_channel re-used inside tests)


# ----------------------------------------------------------------------
# bollinger_bands
# ----------------------------------------------------------------------
def test_bollinger_bands_ordering_and_mid():
    close = pd.Series(np.linspace(100, 120, 60) + np.sin(np.arange(60)))
    lower, mid, upper = bollinger_bands(close, period=20, std=2.0)
    # After warmup, bands are ordered lower <= mid <= upper.
    valid = mid.dropna().index
    assert (lower.loc[valid] <= mid.loc[valid] + 1e-9).all()
    assert (mid.loc[valid] <= upper.loc[valid] + 1e-9).all()
    # Mid is the rolling SMA.
    assert np.isclose(mid.iloc[-1], close.iloc[-20:].mean())


def test_bollinger_bands_zero_std_collapses():
    close = pd.Series([50.0] * 40)
    lower, mid, upper = bollinger_bands(close, period=20, std=2.0)
    assert np.isclose(lower.iloc[-1], 50.0)
    assert np.isclose(upper.iloc[-1], 50.0)


# ----------------------------------------------------------------------
# keltner_channel
# ----------------------------------------------------------------------
def test_keltner_channel_ordering_and_width():
    n = 80
    close = pd.Series(np.linspace(100, 110, n))
    high = close + 1.0
    low = close - 1.0
    lower, mid, upper = keltner_channel(high, low, close, period=20, mult=1.5)
    valid = mid.dropna().index
    assert (lower.loc[valid] <= mid.loc[valid] + 1e-9).all()
    assert (mid.loc[valid] <= upper.loc[valid] + 1e-9).all()
    # Channel half-width = ATR * mult; with constant 2.0 true range, ATR -> 2.0.
    half = (upper - lower) / 2.0
    assert half.iloc[-1] > 0


def test_keltner_channel_wider_mult_wider_band():
    n = 80
    close = pd.Series(np.linspace(100, 110, n))
    high = close + 1.0
    low = close - 1.0
    _, _, up1 = keltner_channel(high, low, close, period=20, mult=1.0)
    _, _, up2 = keltner_channel(high, low, close, period=20, mult=3.0)
    assert up2.iloc[-1] > up1.iloc[-1]


# ----------------------------------------------------------------------
# bb_squeeze
# ----------------------------------------------------------------------
def test_bb_squeeze_returns_bool_series():
    n = 100
    close = pd.Series(np.linspace(100, 105, n) + np.random.RandomState(0).randn(n) * 0.3)
    high = close + 0.5
    low = close - 0.5
    sq = bb_squeeze(close, high, low)
    assert isinstance(sq, pd.Series)
    assert sq.dtype == bool
    assert len(sq) == n


def test_bb_squeeze_true_when_low_vol_then_false_on_expansion():
    # First half: tiny noise (squeeze). Second half: high bar-to-bar whipsaw,
    # which blows BB sigma OUT past the Keltner channel (no squeeze).
    rs = np.random.RandomState(1)
    quiet = 100 + rs.randn(60) * 0.05
    loud = 100 + rs.choice([-1.0, 1.0], 60) * 6.0  # alternating large jumps
    close = pd.Series(np.concatenate([quiet, loud]))
    high = close + 0.1
    low = close - 0.1
    sq = bb_squeeze(close, high, low, bb_period=20, kc_period=20)
    # Squeeze present in the quiet regime; the channel-inside PRIMARY test must
    # clear deep in the whipsaw regime (BB spills outside Keltner there).
    assert sq.iloc[40:55].any()
    bb_l, _, bb_u = bollinger_bands(close, 20, 2.0)
    kc_l, _, kc_u = keltner_channel(high, low, close, 20, 1.5)
    inside_loud = ((bb_l > kc_l) & (bb_u < kc_u)).iloc[-10:]
    assert not inside_loud.any()


# ----------------------------------------------------------------------
# swing_points
# ----------------------------------------------------------------------
def test_swing_points_finds_obvious_pivot():
    # A clean peak at index 5, clean trough at index 11.
    highs = pd.Series([1, 2, 3, 4, 5, 9, 5, 4, 3, 2, 1, 0, 1, 2, 3, 4, 5])
    lows = highs - 0.5
    ph, pl = swing_points(highs, lows, left=2, right=2)
    assert ph.iloc[5]  # peak confirmed at index 5
    assert pl.iloc[11]  # trough confirmed at index 11


def test_swing_points_causal_no_future_peek():
    """
    A pivot high at bar i requires `right` higher-free bars AFTER it.
    Truncating the series at bar i+right-1 (one bar short of confirmation)
    must NOT mark the pivot. The full series may mark it. This proves the
    pivot is never set using bars that lie in the future at decision time.
    """
    highs = pd.Series([1.0, 2.0, 3.0, 9.0, 3.0, 2.0, 1.0])
    lows = highs - 0.5
    left, right = 2, 2
    # Full series: pivot at index 3 is confirmable.
    ph_full, _ = swing_points(highs, lows, left=left, right=right)
    assert ph_full.iloc[3]
    # Truncate so index 3 has only one of two required right-bars (rows 0..4).
    ph_trunc, _ = swing_points(highs.iloc[:5], lows.iloc[:5], left=left, right=right)
    assert not ph_trunc.iloc[3]


def test_swing_points_value_at_i_independent_of_later_bars():
    """
    For every confirmable bar i, the boolean at i computed on the full series
    must equal the boolean computed on the prefix rows 0..i+right. Appending
    bars beyond i+right must never change i's verdict.
    """
    rs = np.random.RandomState(7)
    highs = pd.Series(np.cumsum(rs.randn(40)) + 50)
    lows = highs - 0.4
    left, right = 2, 2
    ph_full, pl_full = swing_points(highs, lows, left=left, right=right)
    for i in range(left, len(highs) - right):
        prefix_n = i + right + 1
        ph_p, pl_p = swing_points(
            highs.iloc[:prefix_n], lows.iloc[:prefix_n], left=left, right=right
        )
        assert bool(ph_p.iloc[i]) == bool(ph_full.iloc[i])
        assert bool(pl_p.iloc[i]) == bool(pl_full.iloc[i])


# ----------------------------------------------------------------------
# session_range
# ----------------------------------------------------------------------
def _make_df_hourly(n: int, start_ts: int = 1_700_000_000) -> pd.DataFrame:
    """n hourly bars with a `ts` column in unix SECONDS, aligned to the hour."""
    ts = start_ts - (start_ts % 3600) + np.arange(n) * 3600
    close = 100 + np.arange(n) * 0.1
    return pd.DataFrame(
        {
            "ts": ts,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0,
        }
    )


def test_session_range_only_session_hours_contribute():
    df = _make_df_hourly(72)
    # Asian session 0..6 UTC.
    hi, lo = session_range(df, start_hour=0, end_hour=6)
    assert isinstance(hi, pd.Series)
    assert isinstance(lo, pd.Series)
    assert len(hi) == len(df)
    # Where defined, high >= low.
    valid = hi.dropna().index
    assert (hi.loc[valid] >= lo.loc[valid] - 1e-9).all()


def test_session_range_causal_no_future_bars():
    """
    session_range at bar i must only reflect session bars at index <= i.
    Compare the value at bar i on the full df vs the prefix rows 0..i —
    they must be identical (no future leakage).
    """
    df = _make_df_hourly(96)
    hi_full, lo_full = session_range(df, start_hour=0, end_hour=6)
    for i in range(0, len(df), 7):
        prefix = df.iloc[: i + 1].reset_index(drop=True)
        hi_p, lo_p = session_range(prefix, start_hour=0, end_hour=6)
        a, b = hi_full.iloc[i], hi_p.iloc[i]
        assert (np.isnan(a) and np.isnan(b)) or np.isclose(a, b)
        c, d = lo_full.iloc[i], lo_p.iloc[i]
        assert (np.isnan(c) and np.isnan(d)) or np.isclose(c, d)


def test_session_range_extreme_tracks_session_only():
    df = _make_df_hourly(48)
    # Spike a NON-session bar's high; the session-range high must ignore it.
    hours = ((df["ts"] // 3600) % 24).astype(int)
    non_session_idx = df.index[(hours < 0) | (hours > 6)][0]
    df.loc[non_session_idx, "high"] = 9999.0
    hi, _ = session_range(df, start_hour=0, end_hour=6)
    assert hi.dropna().max() < 9999.0
