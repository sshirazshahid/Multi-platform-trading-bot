"""Kalman-filter pairs-trading falsification primitives (WS7-A).

These tests pin the contract the screen relies on:
  * the dynamic hedge ratio converges to the true ratio,
  * the standardized innovation (the trading z-score) is strictly CAUSAL — no look-ahead,
  * the strategy makes money on a genuinely mean-reverting (cointegrated) pair and ~nothing on
    two independent random walks (so a later NO_EDGE verdict is trustworthy, not a broken harness),
  * fees monotonically reduce net Sharpe, and
  * the cointegration p-value separates a real pair from noise.

All synthetic data is seeded → deterministic. No network, no live account.
"""
from __future__ import annotations

import numpy as np

from core.alpha_zoo.kalman_pairs import (
    cointegration_pvalue,
    half_life,
    kalman_hedge_ratio,
    pair_strategy_returns,
)


def _rw(n: int, seed: int, sigma: float = 1.0) -> np.ndarray:
    """A driftless Gaussian random walk (treated as a log-price level)."""
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0.0, sigma, n))


def _ou_spread(n: int, seed: int, phi: float = 0.95, sigma: float = 0.5) -> np.ndarray:
    """A stationary AR(1)/OU spread (mean-reverting)."""
    rng = np.random.default_rng(seed)
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0.0, sigma)
    return s


def _sr(r: np.ndarray) -> float:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def test_kalman_recovers_constant_hedge_ratio():
    x = _rw(2000, 1)
    rng = np.random.default_rng(2)
    y = 2.0 * x + rng.normal(0.0, 0.01, x.size)  # y = 2x + tiny noise
    out = kalman_hedge_ratio(y, x)
    assert abs(float(np.median(out["beta"][-500:])) - 2.0) < 0.1


def test_kalman_innovation_is_causal_no_lookahead():
    # The z-score at bar t must NOT change when future bars are appended/removed.
    x = _rw(1000, 3)
    rng = np.random.default_rng(4)
    y = 1.5 * x + rng.normal(0.0, 0.05, x.size)
    full = kalman_hedge_ratio(y, x)
    T = 600
    trunc = kalman_hedge_ratio(y[:T], x[:T])
    assert np.allclose(full["z"][:T], trunc["z"][:T], atol=1e-9, equal_nan=True)
    assert np.allclose(full["beta"][:T], trunc["beta"][:T], atol=1e-9, equal_nan=True)


def test_real_pair_beats_independent_pair_at_zero_fee():
    # Cointegrated pair: y = x + stationary spread -> the strategy should have a clear edge.
    n = 4000
    x = _rw(n, 10)
    y = x + _ou_spread(n, 11)
    sr_real = _sr(pair_strategy_returns(y, x, fee=0.0))

    # Two independent random walks: no cointegration -> ~no edge.
    xi = _rw(n, 20)
    yi = _rw(n, 21)
    sr_noise = _sr(pair_strategy_returns(yi, xi, fee=0.0))

    assert sr_real > 0.0
    assert sr_real > sr_noise + 0.05  # real edge clearly separates from spurious


def test_fees_reduce_net_sharpe():
    n = 4000
    x = _rw(n, 10)
    y = x + _ou_spread(n, 11)
    sr_free = _sr(pair_strategy_returns(y, x, fee=0.0))
    sr_fee = _sr(pair_strategy_returns(y, x, fee=0.005))
    assert sr_fee < sr_free


def test_cointegration_pvalue_separates_signal_from_noise():
    n = 3000
    x = _rw(n, 30)
    y_coint = x + _ou_spread(n, 31, phi=0.9)
    y_indep = _rw(n, 32)
    p_coint = cointegration_pvalue(y_coint, x)
    p_indep = cointegration_pvalue(y_indep, x)
    assert p_coint < 0.05
    assert p_indep > p_coint


def test_half_life_positive_for_mean_reverting_spread():
    hl = half_life(_ou_spread(3000, 40, phi=0.9))
    assert 0.0 < hl < 100.0
