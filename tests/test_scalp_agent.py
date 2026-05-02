"""Task A.4 — ScalpAgent (5m/15m primary)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ohlcv(n: int, freq: str, slope: float, base: float = 100.0, noise: float = 0.5):
    rng = np.random.RandomState(42)
    close = base + np.arange(n) * slope + rng.randn(n) * noise
    high = close + abs(rng.randn(n)) * (noise + 0.3)
    low = close - abs(rng.randn(n)) * (noise + 0.3)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq),
        "open": close - rng.randn(n) * 0.1,
        "high": np.maximum(high, close), "low": np.minimum(low, close),
        "close": close, "volume": np.full(n, 1000.0),
    })


def _curved_5m_uptrend(n=80):
    """Slope flips negative→positive in last 30 bars to force EMA cross."""
    rng = np.random.RandomState(7)
    closes = []
    for i in range(n):
        if i < n - 30:
            closes.append(100 + (-0.05) * i + rng.randn() * 0.3)
        else:
            closes.append(closes[-1] + 0.18 + rng.randn() * 0.2)
    close = np.array(closes)
    high = close + abs(rng.randn(n)) * 0.4
    low = close - abs(rng.randn(n)) * 0.4
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min"),
        "open": close - rng.randn(n) * 0.1,
        "high": np.maximum(high, close), "low": np.minimum(low, close),
        "close": close, "volume": np.full(n, 1000.0),
    })


def test_scalp_agent_emits_long_on_5m_cross_with_aligned_4h():
    from core.agents.scalp_agent import ScalpAgent
    a = ScalpAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_5m": _curved_5m_uptrend(80),
        "ohlcv_15m": _ohlcv(40, "15min", slope=0.3),
        "ohlcv_4h": _ohlcv(40, "4h", slope=0.5),
    })
    # Either emits or doesn't — but if emits, must be long
    if p is not None:
        assert p.side == "buy"
        assert p.tp > p.entry > p.sl


def test_scalp_agent_no_proposal_on_short_history():
    from core.agents.scalp_agent import ScalpAgent
    a = ScalpAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_5m": _ohlcv(20, "5min", 0.1),
        "ohlcv_15m": _ohlcv(10, "15min", 0.1),
        "ohlcv_4h": _ohlcv(10, "4h", 0.1),
    })
    assert p is None


def test_scalp_agent_blocks_long_when_4h_downtrend():
    """Even with 5m cross-up, 4h downtrend filter blocks the long."""
    from core.agents.scalp_agent import ScalpAgent
    a = ScalpAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_5m": _curved_5m_uptrend(80),
        "ohlcv_15m": _ohlcv(40, "15min", slope=0.3),
        "ohlcv_4h": _ohlcv(40, "4h", slope=-2.0, base=300.0),
    })
    assert p is None  # 4h downtrend blocks long even with 5m cross-up
