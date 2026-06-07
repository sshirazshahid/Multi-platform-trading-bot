"""Tests for the index strategy generators — every one must be causal (no look-ahead)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.alpha_zoo.bias_checks import lookahead_violations  # noqa: E402
from index_strategies import (  # noqa: E402
    STRATEGIES,
    bollinger_meanrev,
    breakout,
    ema_cross,
    macd_cross,
    rsi_reversal,
    sma_cross,
)


def _series(n=600, seed=0):
    rng = np.random.default_rng(seed)
    return 100 + np.cumsum(rng.normal(0, 1, n))


CASES = [
    (sma_cross, (10, 50)),
    (ema_cross, (10, 50)),
    (rsi_reversal, (14, 30, 70)),
    (macd_cross, (12, 26, 9)),
    (bollinger_meanrev, (20, 2.0)),
    (breakout, (50,)),
]


@pytest.mark.parametrize("fn,params", CASES)
def test_strategy_is_causal(fn, params):
    close = _series()
    wrapped = lambda c: fn(np.asarray(c, dtype=float), *params)  # noqa: E731
    assert lookahead_violations(wrapped, close, check_indices=range(450, 590)) == []


@pytest.mark.parametrize("fn,params", CASES)
def test_position_is_long_or_flat(fn, params):
    pos = fn(_series(), *params)
    assert set(np.unique(pos)).issubset({0.0, 1.0})


def test_sma_cross_long_on_uptrend():
    close = np.arange(1, 300, dtype=float)
    assert sma_cross(close, 20, 50)[-1] == 1.0


def test_breakout_enters_on_new_high():
    # flat then a clean breakout above the prior 20-bar high
    close = np.concatenate([np.full(40, 100.0), np.linspace(101, 130, 40)])
    pos = breakout(close, 20)
    assert pos[-1] == 1.0
    assert pos[10] == 0.0          # no breakout while flat


def test_strategies_registry_grids_valid():
    for name, (fn, grid) in STRATEGIES.items():
        assert callable(fn) and len(grid) >= 3
        for p in grid:
            out = fn(_series(300), *p)
            assert len(out) == 300
