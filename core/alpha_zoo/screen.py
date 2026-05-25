# core/alpha_zoo/screen.py
"""Two-stage alpha screen. Pure functions, no I/O.

Stage 1 (in-sample): per-bar cross-sectional IC (Spearman) between an alpha
signal and the forward return; IR = mean(IC)/std(IC); sign fixed in-sample;
Alive/Reversed/Dead categorization at |IR| >= threshold.

Stage 2 (out-of-sample): long-short top/bottom-quantile portfolio return per
bar; Sharpe + trials-deflated DSR + one-sided Sharpe p-value; PBO over the
T×K matrix of all computable alphas; Benjamini-Hochberg FDR across survivors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import kurtosis as _kurtosis
from scipy.stats import norm
from scipy.stats import skew as _skew
from scipy.stats import spearmanr

from core.stat_tests import deflated_sharpe, pbo, sharpe


def cross_sectional_ic(signal: pd.DataFrame, fwd_ret: pd.DataFrame,
                       *, min_width: int = 10) -> pd.Series:
    """Per-bar Spearman rank-correlation of `signal` vs `fwd_ret` across symbols.

    A bar with fewer than `min_width` symbols valid in BOTH frames yields NaN.
    """
    out = np.full(len(signal), np.nan)
    s_vals = signal.to_numpy(dtype=float)
    f_vals = fwd_ret.to_numpy(dtype=float)
    for t in range(s_vals.shape[0]):
        s_row, f_row = s_vals[t], f_vals[t]
        mask = np.isfinite(s_row) & np.isfinite(f_row)
        if int(mask.sum()) < int(min_width):
            continue
        if np.all(s_row[mask] == s_row[mask][0]):
            continue  # zero-variance signal -> undefined corr
        rho, _ = spearmanr(s_row[mask], f_row[mask])
        out[t] = rho
    return pd.Series(out, index=signal.index)


def ir(ic: pd.Series) -> float:
    """Information Ratio = mean(IC) / std(IC). 0.0 on degenerate input."""
    x = ic.dropna().to_numpy()
    if x.size < 2:
        return 0.0
    s = x.std(ddof=1)
    if s <= 0:
        return 0.0
    return float(x.mean() / s)


def categorize(ir_value: float, threshold: float = 0.5) -> str:
    if ir_value >= threshold:
        return "alive"
    if ir_value <= -threshold:
        return "reversed"
    return "dead"
