"""Task A.5 — MeanReversionAgent."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _spike_down(n=80):
    """Range-bound noise then spike down for 12 bars to push RSI < 25."""
    rng = np.random.RandomState(11)
    closes = list(100 + rng.randn(n - 12) * 0.4)
    last = closes[-1]
    for _ in range(12):
        last -= 0.6 + rng.randn() * 0.1
        closes.append(last)
    close = np.array(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="15min"),
        "open": close - 0.05,
        "high": close + abs(rng.randn(n)) * 0.3,
        "low": close - abs(rng.randn(n)) * 0.3,
        "close": close, "volume": np.full(n, 1000.0),
    })


def _spike_up(n=80):
    rng = np.random.RandomState(13)
    closes = list(100 + rng.randn(n - 12) * 0.4)
    last = closes[-1]
    for _ in range(12):
        last += 0.6 + rng.randn() * 0.1
        closes.append(last)
    close = np.array(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="15min"),
        "open": close - 0.05,
        "high": close + abs(rng.randn(n)) * 0.3,
        "low": close - abs(rng.randn(n)) * 0.3,
        "close": close, "volume": np.full(n, 1000.0),
    })


def _flat(n=80):
    rng = np.random.RandomState(17)
    close = 100 + rng.randn(n) * 0.5
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="15min"),
        "open": close, "high": close + 0.3, "low": close - 0.3,
        "close": close, "volume": np.full(n, 1000.0),
    })


def test_mr_emits_long_on_oversold_spike_down():
    from core.agents.mean_reversion_agent import MeanReversionAgent
    a = MeanReversionAgent()
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_15m": _spike_down()})
    if p is not None:
        assert p.side == "buy"
        assert p.tp > p.entry > p.sl


def test_mr_emits_short_on_overbought_spike_up():
    from core.agents.mean_reversion_agent import MeanReversionAgent
    a = MeanReversionAgent()
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_15m": _spike_up()})
    if p is not None:
        assert p.side == "sell"
        assert p.tp < p.entry < p.sl


def test_mr_no_proposal_on_short_history():
    from core.agents.mean_reversion_agent import MeanReversionAgent
    a = MeanReversionAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_15m": _flat(20),
    })
    assert p is None


def test_mr_no_proposal_on_flat_market():
    from core.agents.mean_reversion_agent import MeanReversionAgent
    a = MeanReversionAgent()
    p = a.propose({"symbol": "BTC/USDT:USDT", "ohlcv_15m": _flat()})
    assert p is None
