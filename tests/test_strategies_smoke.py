"""
Smoke tests for every strategy + decision engine.

Why this exists
===============
The 580+ tests in tests/ cover infrastructure (orphan cleanup, double-fill
guard, balance handling, hour gates, tier caps, etc.) but DO NOT cover:
  - DCAStrategy import + instantiate
  - RebalancingStrategy import + instantiate
  - MCPBrain import + expected entry-decision methods present
  - SpotPortfolioManager import
  - CapitalAllocator import
  - Legacy strategies (parked in strategies/legacy/) still importable
    so backtest_v3.py / backtest_all.py don't break

If any of these regress, backtests stop working or live entries fail to
load — and we wouldn't catch it until live. These tests are deliberately
shallow: they don't validate behavior, they validate that the wiring is
intact.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def synthetic_ohlcv():
    """Gentle uptrend so TF strategies can emit a signal."""
    n = 120
    close = np.linspace(100, 110, n) + np.random.RandomState(42).randn(n) * 0.3
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="1h"),
        "open":   close - 0.1,
        "high":   close + 0.5,
        "low":    close - 0.5,
        "close":  close,
        "volume": np.full(n, 1000.0),
    })


@pytest.fixture
def mock_om():
    om = MagicMock()
    om.dry_run = True
    om.tracker = MagicMock()
    om.tracker.open_positions = {}
    om.tracker.get_open = lambda: []
    return om


@pytest.fixture
def mock_rm():
    rm = MagicMock()
    rm.is_halted = False
    rm.calculate_position_size = MagicMock(return_value=10.0)
    rm.get_sl_tp = MagicMock(return_value=(0.95, 1.10))
    return rm


# ─── 1. Active strategies (live wired) ────────────────────────────────


def test_dca_strategy_instantiates(mock_om, mock_rm, synthetic_ohlcv):
    from strategies.dca_strategy import DCAStrategy
    s = DCAStrategy(mock_om, mock_rm, market_type="spot")
    assert s.generate_signal(synthetic_ohlcv) is None


def test_rebalancing_strategy_instantiates(mock_om, mock_rm, synthetic_ohlcv):
    from strategies.rebalancing import RebalancingStrategy
    s = RebalancingStrategy(mock_om, mock_rm, market_type="spot")
    assert s.generate_signal(synthetic_ohlcv) is None


# ─── 2. Legacy strategies (backtest-only, must stay importable) ───────


LEGACY_STRATEGIES = [
    ("strategies.legacy.supertrend",       "SupertrendStrategy"),
    ("strategies.legacy.mean_reversion",   "MeanReversionStrategy"),
    ("strategies.legacy.multi_tf",         "MultiTFStrategy"),
    ("strategies.legacy.grid_trading",     "GridTradingStrategy"),
    ("strategies.legacy.scalping",         "ScalpingStrategy"),
    ("strategies.legacy.funding_rate_arb", "FundingRateArbStrategy"),
    ("strategies.legacy.trend_following",  "TrendFollowingStrategy"),
]


@pytest.mark.parametrize("mod_name,cls_name", LEGACY_STRATEGIES)
def test_legacy_strategy_importable(mod_name, cls_name, mock_om, mock_rm, synthetic_ohlcv):
    mod = __import__(mod_name, fromlist=[cls_name])
    cls = getattr(mod, cls_name)
    s = cls(mock_om, mock_rm, market_type="futures")
    s.generate_signal(synthetic_ohlcv)


# ─── 3. Decision engines (the real strategy logic) ────────────────────


def test_mcp_brain_importable():
    from core.mcp_brain import MCPBrain
    assert MCPBrain is not None


def test_mcp_brain_has_expected_entry_methods():
    from core.mcp_brain import MCPBrain
    expected = [
        "analyze_portfolio", "monitor_positions", "_score_coin",
        "score_via_model", "_algorithmic_portfolio",
        "_algorithmic_position_monitor", "should_take_profit",
        "should_hold_position", "is_entry_invalidated",
    ]
    missing = [m for m in expected if not hasattr(MCPBrain, m)]
    assert not missing, f"MCPBrain missing methods: {missing}"


def test_spot_portfolio_manager_importable():
    from core.spot_manager import SpotPortfolioManager
    assert SpotPortfolioManager is not None


def test_capital_allocator_importable():
    from core.capital_allocator import CapitalAllocator
    assert CapitalAllocator is not None


def test_strategy_selector_importable():
    from core.strategy_selector import StrategySelector
    assert StrategySelector is not None
