# tests/test_alpha_screen.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import screen


def test_ic_is_one_when_signal_matches_forward_return():
    # 3 bars × 4 symbols; signal perfectly rank-correlated with fwd_ret each bar
    sig = pd.DataFrame([[1, 2, 3, 4], [4, 3, 2, 1], [1, 3, 2, 4]], dtype=float)
    fwd = sig.copy()
    ic = screen.cross_sectional_ic(sig, fwd, min_width=4)
    assert np.allclose(ic.to_numpy(), 1.0)


def test_ir_and_categorize():
    ic = pd.Series([0.2, 0.25, 0.15, 0.2])  # mean 0.2, low std -> high IR
    assert screen.ir(ic) > 0.5
    assert screen.categorize(screen.ir(ic), 0.5) == "alive"
    assert screen.categorize(-1.0, 0.5) == "reversed"
    assert screen.categorize(0.1, 0.5) == "dead"


def test_min_width_drops_thin_bars():
    sig = pd.DataFrame([[1.0, 2.0, np.nan, np.nan]])
    fwd = pd.DataFrame([[1.0, 2.0, np.nan, np.nan]])
    ic = screen.cross_sectional_ic(sig, fwd, min_width=4)
    assert ic.isna().all()


def test_long_short_returns_positive_when_signal_predicts():
    # signal == fwd_ret each bar -> long winners / short losers -> positive ret
    sig = pd.DataFrame(np.tile([1.0, 2.0, 3.0, 4.0, 5.0], (30, 1)))
    fwd = sig.copy()
    r = screen.long_short_returns(sig, fwd, sign=1.0, q=0.2, min_width=5)
    assert r.dropna().mean() > 0


def test_sign_flip_inverts_portfolio():
    sig = pd.DataFrame(np.tile([1.0, 2.0, 3.0, 4.0, 5.0], (30, 1)))
    fwd = sig.copy()
    r_pos = screen.long_short_returns(sig, fwd, sign=1.0, q=0.2, min_width=5)
    r_neg = screen.long_short_returns(sig, fwd, sign=-1.0, q=0.2, min_width=5)
    assert np.allclose(r_pos.dropna().to_numpy(), -r_neg.dropna().to_numpy())


def test_sharpe_pvalue_small_for_strong_positive_series():
    r = pd.Series(np.full(200, 0.01) + np.random.default_rng(1).normal(0, 1e-4, 200))
    assert screen.sharpe_pvalue(r) < 0.01


def test_bh_fdr_basic():
    # one tiny p among large ones -> only the tiny passes at q=0.05
    flags = screen.fdr_bh([0.001, 0.4, 0.6, 0.8], q=0.05)
    assert flags == [True, False, False, False]


def test_dsr_for_alpha_uses_trials():
    # n_trials>=2 both sides (stat_tests clamps n_trials to >=2), strict
    # inequality so a regression that drops the trials arg would FAIL.
    # Per-bar Sharpe ~0.1 (mean 0.001 / std 0.01): in the discriminating band given the
    # 1/n_obs sr_var scaling, so the n_trials deflation is observable (a Sharpe of ~0.5
    # would saturate both sides to 1.0 once the gate actually discriminates).
    r = np.full(300, 0.001) + np.random.default_rng(2).normal(0, 0.01, 300)
    d_low = screen.dsr_for_returns(r, n_trials=2)
    d_high = screen.dsr_for_returns(r, n_trials=500)
    assert d_low > d_high  # more trials strictly deflates the probability


def test_dsr_discriminates_a_real_per_bar_edge_from_null():
    # The frozen DSR gate must SEPARATE a genuine per-bar edge from a null one at the same
    # n_trials. Pre-fix (sr_var defaulted to 1.0) the expected-max-Sharpe bar was ~3.0 PER BAR,
    # so DSR read 0.00 for ANY realistic per-bar Sharpe -> non-discriminating GO gate.
    rng = np.random.default_rng(7)
    n = 4000
    strong = 0.0012 + rng.normal(0, 0.01, n)    # per-bar Sharpe ~0.12 (a real edge)
    nullish = 0.00005 + rng.normal(0, 0.01, n)  # per-bar Sharpe ~0.005 (noise)
    d_strong = screen.dsr_for_returns(strong, n_trials=200)
    d_null = screen.dsr_for_returns(nullish, n_trials=200)
    assert d_strong >= 0.10   # a genuine per-bar edge can now clear the gate
    assert d_null < 0.10      # a null one cannot
    assert d_strong > d_null


def test_dsr_accepts_explicit_sr_var_override():
    # A caller holding the full trial-Sharpe population may pass the empirical variance for a
    # stricter, BLdP-faithful gate; a larger sr_var raises the expected-max bar -> lower DSR.
    rng = np.random.default_rng(8)
    r = 0.0012 + rng.normal(0, 0.01, 4000)
    d_default = screen.dsr_for_returns(r, n_trials=200)                 # sr_var = 1/n_obs
    d_inflated = screen.dsr_for_returns(r, n_trials=200, sr_var=1e-2)   # large -> stricter
    assert d_inflated < d_default


def test_bh_fdr_rejects_up_to_largest_passing_rank():
    # 0.01<=0.0167, 0.02<=0.0333, 0.5>0.05 -> reject the first two
    assert screen.fdr_bh([0.01, 0.02, 0.5], q=0.05) == [True, True, False]
    # flags come back in INPUT order, not sorted order
    assert screen.fdr_bh([0.5, 0.01, 0.02], q=0.05) == [False, True, True]


def test_pbo_over_alphas_guards_and_union_aligns():
    # < 2 alphas or empty -> neutral 0.5
    assert screen.pbo_over_alphas({"a": pd.Series([0.1, 0.2])}) == 0.5
    assert screen.pbo_over_alphas({}) == 0.5
    # two alphas on partially-overlapping grids -> union>=16, fillna(0), runs
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(0, 0.01, 20), index=range(20))
    b = pd.Series(rng.normal(0, 0.01, 20), index=range(10, 30))
    val = screen.pbo_over_alphas({"a": a, "b": b}, n_partitions=16)
    assert 0.0 <= val <= 1.0
