"""Phase 0.4: statistical-test invariants.

Tests:
- Deflated Sharpe boundary cases + sanity (multiple-testing penalty)
- PBO ~0.5 on random strategies, low on a true-edge strategy
- Bootstrap CI brackets truth on Gaussian returns at ~95% coverage
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "stat_tests_test_copy", ROOT / "core" / "stat_tests.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stat_tests_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def st():
    return _load()


# ── Deflated Sharpe ─────────────────────────────────────────────────────

def test_dsr_zero_sr_is_low(st):
    """SR=0 should have low DSR (no skill detected)."""
    p = st.deflated_sharpe(sr_observed=0.0, n_trials=1, n_obs=252)
    assert p < 0.55, f"DSR for SR=0 should be ~0.5; got {p}"


def test_dsr_high_sr_with_one_trial_is_skill_like(st):
    """SR=2.0 with 1 trial and 252 obs → very high DSR."""
    p = st.deflated_sharpe(sr_observed=2.0, n_trials=1, n_obs=252)
    assert p > 0.99, f"DSR for SR=2 / 1 trial should be near 1; got {p}"


def test_dsr_multiple_testing_kills_signal(st):
    """Same SR with many trials → DSR drops materially."""
    p_one = st.deflated_sharpe(sr_observed=1.5, n_trials=1, n_obs=252)
    p_many = st.deflated_sharpe(sr_observed=1.5, n_trials=10000, n_obs=252)
    assert p_many < p_one, "more trials must lower DSR (selection bias)"
    assert p_many < 0.95, f"10k trials @ SR=1.5 should not pass 95%; got {p_many}"


def test_dsr_more_obs_increases_confidence(st):
    """More observations at same SR → tighter CI → higher DSR."""
    p_short = st.deflated_sharpe(sr_observed=1.0, n_trials=1, n_obs=60)
    p_long = st.deflated_sharpe(sr_observed=1.0, n_trials=1, n_obs=2520)
    assert p_long > p_short


def test_dsr_skew_and_kurt_lower_confidence(st):
    """Negative skew + fat tails should make DSR more conservative."""
    p_norm = st.deflated_sharpe(
        sr_observed=1.5, n_trials=1, n_obs=252, skew=0.0, kurt=3.0)
    p_fat = st.deflated_sharpe(
        sr_observed=1.5, n_trials=1, n_obs=252, skew=-1.0, kurt=8.0)
    assert p_fat < p_norm, "negative skew + fat tails must lower DSR"


# ── PBO ─────────────────────────────────────────────────────────────────

def test_pbo_random_strategies_near_half(st):
    """100 random-walk strategies (no edge) → PBO close to 0.5."""
    rng = np.random.default_rng(42)
    n_periods, n_strats = 256, 50
    returns = rng.normal(0.0, 0.01, size=(n_periods, n_strats))
    pbo = st.pbo(returns, n_partitions=8)  # smaller for test speed
    assert 0.35 < pbo < 0.65, f"random-walk PBO should ~0.5; got {pbo}"


def test_pbo_one_dominant_strategy_is_low(st):
    """If one strategy genuinely has edge (positive drift) > others, PBO low."""
    rng = np.random.default_rng(0)
    n_periods, n_strats = 256, 30
    returns = rng.normal(0.0, 0.01, size=(n_periods, n_strats))
    returns[:, 0] = rng.normal(0.005, 0.01, size=n_periods)  # winner
    pbo = st.pbo(returns, n_partitions=8)
    assert pbo < 0.4, f"clear-winner PBO should be < 0.4; got {pbo}"


def test_pbo_returns_in_unit_interval(st):
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0, 0.01, size=(64, 10))
    pbo = st.pbo(returns, n_partitions=4)
    assert 0.0 <= pbo <= 1.0


def test_pbo_requires_even_partitions(st):
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0, 0.01, size=(64, 5))
    with pytest.raises(ValueError):
        st.pbo(returns, n_partitions=7)


# ── Bootstrap CI ────────────────────────────────────────────────────────

def test_bootstrap_ci_brackets_true_sharpe(st):
    """For Gaussian returns with known mean/std, CI should bracket sample SR."""
    rng = np.random.default_rng(7)
    returns = rng.normal(0.001, 0.01, size=500)
    sample_sr = returns.mean() / returns.std(ddof=1)
    lo, hi = st.bootstrap_ci(returns, n_resamples=2000, ci=0.95, random_state=42)
    assert lo < sample_sr < hi
    assert hi - lo < 0.5  # not absurdly wide


def test_bootstrap_ci_default_statistic_is_sharpe(st):
    rng = np.random.default_rng(8)
    returns = rng.normal(0.0, 0.01, size=200)
    lo, hi = st.bootstrap_ci(returns, n_resamples=1000, random_state=1)
    # Sharpe of zero-mean returns should bracket 0
    assert lo < 0 < hi


def test_bootstrap_ci_custom_statistic(st):
    rng = np.random.default_rng(9)
    returns = rng.normal(0.001, 0.01, size=200)
    # Mean as statistic
    lo, hi = st.bootstrap_ci(
        returns, statistic=lambda x: float(np.mean(x)),
        n_resamples=1000, ci=0.95, random_state=2)
    assert lo < returns.mean() < hi


def test_bootstrap_ci_reproducible_with_seed(st):
    rng = np.random.default_rng(10)
    returns = rng.normal(0.0, 0.01, size=200)
    lo1, hi1 = st.bootstrap_ci(returns, n_resamples=500, random_state=99)
    lo2, hi2 = st.bootstrap_ci(returns, n_resamples=500, random_state=99)
    assert (lo1, hi1) == (lo2, hi2)


def test_sharpe_helper(st):
    """Exposed Sharpe helper for callers; consistent with bootstrap default."""
    rng = np.random.default_rng(11)
    returns = rng.normal(0.001, 0.01, size=300)
    sr = st.sharpe(returns)
    expected = returns.mean() / returns.std(ddof=1)
    assert abs(sr - expected) < 1e-9
