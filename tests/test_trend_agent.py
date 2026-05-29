"""Task A.3 — TrendAgent."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ohlcv(n: int, slope: float, noise: float = 1.5, base: float = 100.0):
    """Generate synthetic OHLCV. slope is per-bar drift; noise scales H/L wicks."""
    rng = np.random.RandomState(42)
    close = base + np.arange(n) * slope + rng.randn(n) * noise
    high = close + abs(rng.randn(n)) * (noise + 0.5)
    low = close - abs(rng.randn(n)) * (noise + 0.5)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="1h"),
        "open": close - rng.randn(n) * 0.3,
        "high": np.maximum(high, close), "low": np.minimum(low, close),
        "close": close, "volume": np.full(n, 1000.0),
    })


def test_trend_agent_emits_long_on_uptrend():
    from core.agents.trend_agent import TrendAgent
    a = TrendAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _ohlcv(80, slope=0.5),
        "ohlcv_4h": _ohlcv(40, slope=2.0),
    })
    assert p is not None
    assert p.side == "buy"
    assert p.tp > p.entry > p.sl


def test_trend_agent_emits_short_on_downtrend():
    """Tuned to land RSI in 35-60 sweet-spot AND ADX >= 20."""
    from core.agents.trend_agent import TrendAgent
    a = TrendAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _ohlcv(80, slope=-0.35, base=300.0),
        "ohlcv_4h": _ohlcv(40, slope=-1.5, base=300.0),
    })
    assert p is not None
    assert p.side == "sell"
    assert p.tp < p.entry < p.sl


def test_trend_agent_no_proposal_on_flat():
    from core.agents.trend_agent import TrendAgent
    a = TrendAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _ohlcv(80, slope=0.0, noise=0.1),
        "ohlcv_4h": _ohlcv(40, slope=0.0, noise=0.1),
    })
    assert p is None


def test_trend_agent_no_proposal_on_short_history():
    from core.agents.trend_agent import TrendAgent
    a = TrendAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _ohlcv(20, slope=0.5),
        "ohlcv_4h": _ohlcv(10, slope=2.0),
    })
    assert p is None
