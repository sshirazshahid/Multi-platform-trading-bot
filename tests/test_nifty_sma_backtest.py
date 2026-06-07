"""Tests for the Nifty 50 SMA-crossover backtest engine (pure core)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.alpha_zoo.bias_checks import lookahead_violations  # noqa: E402
from nifty_sma_backtest import (  # noqa: E402
    _max_drawdown,
    metrics,
    run_backtest,
    sma,
    sma_crossover_position,
)


def test_sma_values_and_warmup():
    x = np.array([1, 2, 3, 4, 5], dtype=float)
    s = sma(x, 3)
    assert np.isnan(s[0]) and np.isnan(s[1])
    assert s[2] == 2.0 and s[3] == 3.0 and s[4] == 4.0


def test_position_long_on_uptrend_flat_in_warmup():
    close = np.arange(1, 80, dtype=float)        # strictly rising
    pos = sma_crossover_position(close, 10, 30)
    assert pos[5] == 0.0                          # warmup → flat
    assert pos[-1] == 1.0                         # rising → fast>slow → long


def test_position_has_no_lookahead():
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, 400))
    fn = lambda c: sma_crossover_position(np.asarray(c, dtype=float), 10, 30)  # noqa: E731
    assert lookahead_violations(fn, close, check_indices=range(300, 390)) == []


def test_backtest_pnl_long_no_cost():
    close = np.array([100, 110, 110], dtype=float)   # +10% then flat
    pos = np.array([1, 1, 1], dtype=float)
    bt = run_backtest(close, pos, cost_oneway=0.0)
    # prev=[0,1,1]; ret=[0,0.1,0]; strat=[0,0.1,0]; equity[-1]=1.1
    assert abs(bt["equity"][-1] - 1.1) < 1e-9


def test_backtest_charges_cost_on_turnover():
    close = np.array([100, 100, 100], dtype=float)
    pos = np.array([1, 1, 1], dtype=float)           # enter at t0 → 1 turn
    bt = run_backtest(close, pos, cost_oneway=0.01)
    assert abs(bt["strat_ret"][0] - (-0.01)) < 1e-9


def test_max_drawdown():
    eq = np.array([1.0, 1.2, 0.9, 1.0])              # trough 0.9 vs peak 1.2 → -25%
    assert abs(_max_drawdown(eq) - (-0.25)) < 1e-9


def test_metrics_monotonic_gain_has_no_drawdown():
    r = np.full(300, 0.001)
    eq = np.cumprod(1.0 + r)
    m = metrics(r, eq, pos=np.ones(300))
    assert abs(m["max_drawdown"]) < 1e-12
    assert m["sharpe"] > 0 and m["total_return"] > 0
    assert m["time_in_market"] == 1.0
