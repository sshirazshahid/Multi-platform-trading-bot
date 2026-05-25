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
    r = np.full(300, 0.005) + np.random.default_rng(2).normal(0, 0.01, 300)
    d_low = screen.dsr_for_returns(r, n_trials=1)
    d_high = screen.dsr_for_returns(r, n_trials=500)
    assert d_low >= d_high  # more trials deflates the probability
