"""core/stats_inference.py — math correctness pins (2026-06-11).

Reference values cross-checked against published tables:
- Wilson 95% CI for 5/10 = (0.2366, 0.7634)
- t critical value at 97.5%, df=4 = 2.776
"""
from __future__ import annotations

import pytest

from core.stats_inference import (
    bootstrap_delta_ci,
    mean_ci,
    mean_ci_bootstrap,
    t_cdf,
    t_ppf,
    two_sample_verdict,
    welch_t_test,
    wilson_interval,
)


def test_wilson_known_value():
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3)
    assert hi == pytest.approx(0.7634, abs=1e-3)


def test_wilson_edges():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(10, 10)
    assert hi == pytest.approx(1.0, abs=1e-9) and lo > 0.6
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0 and hi0 < 0.35
    # never raises on garbage
    assert wilson_interval("x", None) == (0.0, 1.0)


def test_t_cdf_known_values():
    assert t_cdf(0.0, 10) == pytest.approx(0.5, abs=1e-12)
    assert t_cdf(2.776, 4) == pytest.approx(0.975, abs=1e-3)
    assert t_cdf(-2.776, 4) == pytest.approx(0.025, abs=1e-3)
    # converges to normal at large df
    assert t_cdf(1.959964, 1e6) == pytest.approx(0.975, abs=1e-4)


def test_t_ppf_inverts_cdf():
    assert t_ppf(0.975, 4) == pytest.approx(2.776, abs=1e-3)
    assert t_ppf(0.975, 1e6) == pytest.approx(1.96, abs=1e-3)
    assert t_ppf(0.5, 7) == pytest.approx(0.0, abs=1e-6)


def test_mean_ci_basic():
    m, lo, hi = mean_ci([1.0, 2.0, 3.0, 4.0, 5.0])
    assert m == pytest.approx(3.0)
    assert lo < 3.0 < hi
    # zero variance -> degenerate CI at the mean
    m2, lo2, hi2 = mean_ci([2.0, 2.0, 2.0])
    assert (m2, lo2, hi2) == (2.0, 2.0, 2.0)
    # n=1 -> nan bounds
    m1, lo1, hi1 = mean_ci([7.0])
    assert m1 == 7.0 and lo1 != lo1 and hi1 != hi1


def test_bootstrap_deterministic():
    vals = [(-1.0) ** i * (1 + i % 5) for i in range(40)]
    a = mean_ci_bootstrap(vals, seed=42)
    b = mean_ci_bootstrap(vals, seed=42)
    assert a == b
    c = mean_ci_bootstrap(vals, seed=7)
    assert c != a  # different seed, different resamples


def test_welch_separated_vs_identical():
    flat = [0.9, 1.1] * 25
    shifted = [9.9, 10.1] * 25
    t, df, p = welch_t_test(flat, shifted)
    assert p < 1e-6 and t > 0
    t2, df2, p2 = welch_t_test(flat, list(flat))
    assert p2 > 0.9
    # degenerate
    assert welch_t_test([1.0], [1.0, 2.0]) == (0.0, 1.0, 1.0)


def test_bootstrap_delta_sign():
    pre = [-0.1, 0.1] * 25
    post = [0.9, 1.1] * 25
    d, lo, hi = bootstrap_delta_ci(pre, post)
    assert d == pytest.approx(1.0, abs=1e-9)
    assert lo > 0.8 and hi < 1.2


def test_verdict_improved_worse_inconclusive():
    pre = [-0.1, 0.1] * 25
    post = [0.9, 1.1] * 25
    assert two_sample_verdict(pre, post)["verdict"] == "IMPROVED"
    assert two_sample_verdict(post, pre)["verdict"] == "WORSE"
    noisy = [v * 50 for v in pre]
    r = two_sample_verdict(noisy, [v + 0.01 for v in noisy])
    assert r["verdict"] == "INCONCLUSIVE"
    small = two_sample_verdict([1.0] * 5, [2.0] * 5)
    assert small["verdict"] == "INCONCLUSIVE"
    assert small["reason"] == "insufficient_n"
