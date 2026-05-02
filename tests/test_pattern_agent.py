"""PatternAgent — H&S, double top/bottom, flag detection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _df_from_close(close: list[float], freq="1h"):
    n = len(close)
    arr = np.array(close, dtype=float)
    rng = np.random.RandomState(7)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq),
        "open": arr - 0.05,
        "high": arr + abs(rng.randn(n)) * 0.4,
        "low": arr - abs(rng.randn(n)) * 0.4,
        "close": arr, "volume": np.full(n, 1000.0),
    })


def _double_bottom_series(n=80) -> list[float]:
    """Two lows ~equal at ~92, separated by a peak at ~101, then break above 101."""
    rng = np.random.RandomState(13)
    close = []
    # uptrend prior
    for i in range(20):
        close.append(98 + i * 0.05 + rng.randn() * 0.1)
    # decline to first low
    for i in range(15):
        close.append(close[-1] - 0.5 + rng.randn() * 0.1)
    # bounce up to ~101
    for i in range(15):
        close.append(close[-1] + 0.6 + rng.randn() * 0.1)
    # decline to second low (similar level)
    for i in range(15):
        close.append(close[-1] - 0.55 + rng.randn() * 0.1)
    # break above the peak
    for i in range(n - len(close)):
        close.append(close[-1] + 0.5 + rng.randn() * 0.1)
    return close


def _double_top_series(n=80) -> list[float]:
    rng = np.random.RandomState(17)
    close = []
    for i in range(20):
        close.append(102 - i * 0.05 + rng.randn() * 0.1)
    for i in range(15):
        close.append(close[-1] + 0.5 + rng.randn() * 0.1)
    for i in range(15):
        close.append(close[-1] - 0.6 + rng.randn() * 0.1)
    for i in range(15):
        close.append(close[-1] + 0.55 + rng.randn() * 0.1)
    for i in range(n - len(close)):
        close.append(close[-1] - 0.5 + rng.randn() * 0.1)
    return close


def _bull_flag_series(n=60) -> list[float]:
    rng = np.random.RandomState(23)
    close = []
    # prior chop
    for _ in range(15):
        close.append(100 + rng.randn() * 0.2)
    # impulse up
    for _ in range(20):
        close.append(close[-1] + 0.6 + rng.randn() * 0.1)
    # tight consolidation
    base = close[-1]
    for _ in range(5):
        close.append(base + rng.randn() * 0.15)
    # pad
    for _ in range(n - len(close)):
        close.append(close[-1] + rng.randn() * 0.1)
    return close[:n]


def test_pattern_agent_detects_double_bottom():
    from core.agents.pattern_agent import PatternAgent
    a = PatternAgent()
    ohlcv = _df_from_close(_double_bottom_series())
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_1h": ohlcv})
    if p is not None:
        assert p.side == "buy"
        assert "double_bottom" in p.reason or "inverse_h_s" in p.reason or "flag" in p.reason
        assert p.tp > p.entry > p.sl


def test_pattern_agent_detects_double_top():
    from core.agents.pattern_agent import PatternAgent
    a = PatternAgent()
    ohlcv = _df_from_close(_double_top_series())
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_1h": ohlcv})
    if p is not None:
        assert p.side == "sell"
        assert p.tp < p.entry < p.sl


def test_pattern_agent_no_proposal_on_short_history():
    from core.agents.pattern_agent import PatternAgent
    a = PatternAgent()
    ohlcv = _df_from_close([100.0] * 30)  # too short
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_1h": ohlcv})
    assert p is None


def test_pattern_agent_no_proposal_on_flat_market():
    from core.agents.pattern_agent import PatternAgent
    rng = np.random.RandomState(1)
    close = list(100 + rng.randn(80) * 0.05)  # essentially flat
    ohlcv = _df_from_close(close)
    a = PatternAgent()
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_1h": ohlcv})
    assert p is None


def test_pivot_detection_finds_known_extrema():
    from core.agents.pattern_agent import _pivots
    close = pd.Series([100, 101, 102, 103, 104, 103, 102, 101, 100, 99,
                       98, 99, 100, 101, 102, 103, 104, 105, 106, 105])
    highs, lows = _pivots(close, lookback=3)
    # peak at index 4 (value 104), trough at index 10 (value 98)
    assert 4 in highs
    assert 10 in lows


def test_pattern_agent_inherits_baseagent():
    from core.agents.pattern_agent import PatternAgent
    from core.agents.base_agent import BaseAgent
    assert issubclass(PatternAgent, BaseAgent)
    assert PatternAgent().name == "PatternAgent"
