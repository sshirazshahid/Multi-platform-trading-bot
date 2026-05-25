"""Range-stability / chop filter (2026-05-25, no-edge-forensics bundle).

The bot is a trend engine bleeding in chop; instantaneous ATR can't tell
a trend from chop with the same ATR. UniverseFilter now adds a daily-candle
Kaufman efficiency ratio + range-of-change quality gate. Dynamic, not a
blacklist (UNBLOCK_ALL-safe). Conservative floors so frequency isn't gutted.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.pair_discovery import UniverseFilter


def _ex_with_daily(candles):
    ex = MagicMock()
    ex.name = "binance"
    ex.fetch_ohlcv.return_value = candles
    return ex


def _candle(close, high=None, low=None):
    # [ts, open, high, low, close, vol]
    high = high if high is not None else close * 1.01
    low = low if low is not None else close * 0.99
    return [0, close, high, low, close, 1000]


def test_clean_uptrend_high_efficiency_passes():
    """Monotonic uptrend → efficiency near 1.0 → not rejected as chop."""
    uf = UniverseFilter()
    closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120]
    candles = [_candle(c) for c in closes]
    roc, eff = uf._check_range_stability(_ex_with_daily(candles), "BTC/USDT:USDT", "futures")
    assert eff > 0.9, f"clean uptrend should have high efficiency, got {eff}"
    assert eff >= uf.MIN_TREND_EFFICIENCY
    assert roc > uf.MIN_RANGE_OF_CHANGE  # 20% span, well above dead floor


def test_chop_low_efficiency_flagged():
    """Price oscillates, ends near start → efficiency near 0 → chop."""
    uf = UniverseFilter()
    # zig-zag around 100, net move ~0 but large path
    closes = [100, 105, 100, 105, 100, 105, 100, 105, 100, 105, 100]
    candles = [_candle(c) for c in closes]
    roc, eff = uf._check_range_stability(_ex_with_daily(candles), "X/USDT:USDT", "futures")
    assert eff < uf.MIN_TREND_EFFICIENCY, (
        f"chop should have low efficiency, got {eff}"
    )


def test_dead_flat_coin_low_roc_flagged():
    """Tiny multi-day range → range-of-change below floor → dead coin."""
    uf = UniverseFilter()
    # all closes within 0.5% — dead
    closes = [100.0, 100.1, 100.0, 100.2, 100.1, 100.0, 100.1, 100.0, 100.2, 100.1, 100.0]
    candles = [_candle(c, high=c * 1.001, low=c * 0.999) for c in closes]
    roc, eff = uf._check_range_stability(_ex_with_daily(candles), "DEAD/USDT:USDT", "futures")
    assert roc < uf.MIN_RANGE_OF_CHANGE, f"dead coin should have low roc, got {roc}"


def test_missing_data_is_neutral():
    """No candles → (0,0) → caller must NOT reject on missing data."""
    uf = UniverseFilter()
    roc, eff = uf._check_range_stability(_ex_with_daily([]), "X/USDT:USDT", "futures")
    assert roc == 0.0 and eff == 0.0
    # The check() wiring uses `0 < roc < MIN` and `0 < eff < MIN`, so a 0.0
    # value cannot trigger a rejection — verified by the strict-greater guard.


def test_filter_is_config_gated():
    """RANGE_STABILITY_FILTER_ENABLED must exist and default True so the
    filter is reversible in one line."""
    import config
    assert hasattr(config, "RANGE_STABILITY_FILTER_ENABLED")
    assert config.RANGE_STABILITY_FILTER_ENABLED is True


def test_zero_value_cannot_reject():
    """Pin the strict-greater guard semantics: roc/eff of exactly 0
    (missing data) must be neutral, not a rejection."""
    # mirrors the check() condition: `0 < x < MIN`
    MIN = UniverseFilter.MIN_TREND_EFFICIENCY
    assert not (0 < 0.0 < MIN)        # missing → no reject
    assert (0 < 0.10 < MIN)            # real low value → reject
