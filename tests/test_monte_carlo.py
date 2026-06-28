"""WS3 — trade-sequence Monte Carlo: known-edge promotes, pure-noise is rejected.

Import-light: only numpy + core.decision.monte_carlo (no ccxt/schedule/config).
"""
from __future__ import annotations

import numpy as np

from core.decision.monte_carlo import (
    MonteCarloResult,
    block_bootstrap,
    monte_carlo_trade_sequence,
)


def _series(mean, std, n, seed):
    rng = np.random.default_rng(seed)
    return rng.normal(mean, std, size=n)


def test_positive_edge_passes():
    # 200 small positive-EV trades, low noise -> robustly positive, bounded drawdown.
    rets = _series(0.012, 0.006, 200, seed=1)
    res = monte_carlo_trade_sequence(rets, n_resamples=1000, seed=3)
    assert res.n_trades == 200
    assert res.p_total_positive > 0.95
    assert res.max_drawdown_p95 < 0.25
    assert res.passes() is True


def test_pure_noise_rejected():
    # Zero-mean noise -> ~coin-flip on total sign, NOT a capital-preservation pass.
    rets = _series(0.0, 0.02, 200, seed=2)
    res = monte_carlo_trade_sequence(rets, n_resamples=1000, seed=3)
    assert 0.30 < res.p_total_positive < 0.70   # indistinguishable from 0.5
    assert res.passes() is False


def test_negative_edge_rejected():
    rets = _series(-0.01, 0.01, 200, seed=5)
    res = monte_carlo_trade_sequence(rets, n_resamples=800, seed=3)
    assert res.p_total_positive < 0.05
    assert res.passes() is False


def test_too_few_trades_never_passes():
    rets = _series(0.05, 0.001, 10, seed=7)   # tiny but strongly positive
    res = monte_carlo_trade_sequence(rets, n_resamples=500, seed=3)
    assert res.passes(min_trades=30) is False   # sample too small to trust


def test_empty_and_degenerate_inputs():
    empty = monte_carlo_trade_sequence([], n_resamples=100)
    assert isinstance(empty, MonteCarloResult)
    assert empty.n_trades == 0 and empty.passes() is False
    boot = block_bootstrap([], 5, 100, 1)
    assert boot.size == 0


def test_block_bootstrap_shape_and_determinism():
    rets = _series(0.0, 0.01, 50, seed=9)
    a = block_bootstrap(rets, block_len=5, n_resamples=200, seed=42)
    b = block_bootstrap(rets, block_len=5, n_resamples=200, seed=42)
    assert a.shape == (200, 50)
    assert np.array_equal(a, b)   # seeded -> reproducible
