"""LiquidityAgent — stop-hunt + OB imbalance."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _df(rows):
    """rows = list of (open, high, low, close)."""
    n = len(rows)
    arr = np.array(rows, dtype=float)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="1h"),
        "open": arr[:, 0], "high": arr[:, 1],
        "low": arr[:, 2], "close": arr[:, 3],
        "volume": np.full(n, 1000.0),
    })


def _swing_with_sweep_high(n=25):
    """Range-bound for n-1 bars then last bar wicks above swing high."""
    rows = []
    rng = np.random.RandomState(7)
    for i in range(n - 1):
        c = 100 + rng.randn() * 0.4
        rows.append((c - 0.05, c + 0.4, c - 0.4, c))
    # Last bar: high pierces 101.0 swing high (max of prior range), closes below
    rows.append((100.5, 102.5, 100.4, 100.6))
    return _df(rows)


def _swing_with_sweep_low(n=25):
    rows = []
    rng = np.random.RandomState(11)
    for i in range(n - 1):
        c = 100 + rng.randn() * 0.4
        rows.append((c - 0.05, c + 0.4, c - 0.4, c))
    # Last bar: low pierces lowest, closes back above
    rows.append((99.5, 99.6, 97.5, 99.4))
    return _df(rows)


def _flat_no_sweep(n=25):
    rng = np.random.RandomState(17)
    rows = []
    for _ in range(n):
        c = 100 + rng.randn() * 0.4
        rows.append((c - 0.05, c + 0.3, c - 0.3, c))
    return _df(rows)


def test_liquidity_agent_buys_on_sweep_low():
    from core.agents.liquidity_agent import LiquidityAgent
    a = LiquidityAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _swing_with_sweep_low(),
    })
    assert p is not None
    assert p.side == "buy"
    assert p.tp > p.entry > p.sl


def test_liquidity_agent_sells_on_sweep_high():
    from core.agents.liquidity_agent import LiquidityAgent
    a = LiquidityAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _swing_with_sweep_high(),
    })
    assert p is not None
    assert p.side == "sell"
    assert p.tp < p.entry < p.sl


def test_liquidity_agent_no_sweep_no_proposal():
    from core.agents.liquidity_agent import LiquidityAgent
    a = LiquidityAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _flat_no_sweep(),
    })
    assert p is None


def test_liquidity_agent_ob_imbalance_calc():
    from core.agents.liquidity_agent import _ob_imbalance
    # Heavy bid: 10x bid notional vs ask
    ob = {"bids": [(100, 100), (99, 100)], "asks": [(101, 1), (102, 1)]}
    imb = _ob_imbalance(ob)
    assert imb > 0.95  # strongly bid-heavy
    # Heavy ask: mirror
    ob2 = {"bids": [(100, 1)], "asks": [(101, 100), (102, 100)]}
    imb2 = _ob_imbalance(ob2)
    assert imb2 < -0.95


def test_liquidity_agent_blocks_buy_when_ob_strongly_ask_heavy():
    """Sweep-low says buy, but OB strongly ask-heavy contradicts → no fire."""
    from core.agents.liquidity_agent import LiquidityAgent
    a = LiquidityAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _swing_with_sweep_low(),
        "orderbook": {"bids": [(100, 1)], "asks": [(101, 50), (102, 50)]},
    })
    assert p is None  # OB contradicts


def test_liquidity_agent_higher_confidence_with_ob_confirm():
    from core.agents.liquidity_agent import LiquidityAgent
    a = LiquidityAgent()
    p = a.propose({
        "symbol": "BTC/USDT:USDT",
        "ohlcv_1h": _swing_with_sweep_low(),
        "orderbook": {"bids": [(100, 50), (99, 50)], "asks": [(101, 1)]},
    })
    assert p is not None
    assert p.confidence >= 0.60  # OB-confirmed bumps confidence
