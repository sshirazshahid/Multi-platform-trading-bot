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
