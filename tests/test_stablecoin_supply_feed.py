"""DefiLlama stablecoin-supply feed — parse + causal regime-signal tests.

The signal (trailing N-day supply growth) is a market-wide liquidity-regime variable; a
time-series gate (next increment) tests whether it predicts forward returns. Here we pin
parsing robustness and that the signal computation has NO look-ahead (uses the WS6 detector).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.alpha_zoo.bias_checks import is_causal
from core.data_feeds.stablecoin_supply_feed import (
    parse_supply_series,
    supply_growth_signal,
)


def test_parse_supply_series_basic():
    recs = [
        {"date": "0", "totalCirculatingUSD": {"peggedUSD": 100.0}},
        {"date": "86400", "totalCirculatingUSD": {"peggedUSD": 110.0}},
        {"date": "172800", "totalCirculatingUSD": {"peggedUSD": 121.0}},
        {"date": "172800", "totalCirculatingUSD": {"peggedUSD": 121.0}},  # duplicate day
    ]
    s = parse_supply_series(recs)
    assert len(s) == 3
    assert s.iloc[0] == 100.0 and s.iloc[-1] == 121.0
    assert s.index.is_monotonic_increasing


def test_parse_skips_bad_records():
    assert len(parse_supply_series([])) == 0
    assert len(parse_supply_series([{"date": None, "totalCirculatingUSD": {"peggedUSD": 1}}])) == 0
    assert len(parse_supply_series([{"date": "0", "totalCirculatingUSD": {}}])) == 0


def test_supply_growth_signal_values():
    s = pd.Series([100.0, 110.0, 121.0, 133.1])
    g = supply_growth_signal(s, window=1)
    assert np.isnan(g.iloc[0])
    assert g.iloc[1] == pytest.approx(0.10)
    assert g.iloc[2] == pytest.approx(0.10)


def test_supply_growth_signal_is_causal():
    series = np.array([100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 121.0, 128.0], dtype=float)
    f = lambda s: supply_growth_signal(pd.Series(s), window=3).to_numpy()  # noqa: E731
    assert is_causal(f, series)
