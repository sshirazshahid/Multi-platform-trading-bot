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
from scipy.stats import norm, spearmanr
from scipy.stats import skew as _skew

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


# ---------------------------------------------------------------------------
# Stage 2 — out-of-sample evaluation
# ---------------------------------------------------------------------------


def long_short_returns(signal: pd.DataFrame, fwd_ret: pd.DataFrame, *,
                       sign: float, q: float = 0.2,
                       min_width: int = 10) -> pd.Series:
    """Per-bar long-short portfolio return.

    At each bar rank symbols by `sign * signal`, go long the top `q` fraction
    and short the bottom `q` fraction, and take mean(fwd_ret[long]) -
    mean(fwd_ret[short]). Bars with < min_width valid symbols -> NaN.
    """
    s_vals = (sign * signal).to_numpy(dtype=float)
    f_vals = fwd_ret.to_numpy(dtype=float)
    out = np.full(s_vals.shape[0], np.nan)
    for t in range(s_vals.shape[0]):
        s_row, f_row = s_vals[t], f_vals[t]
        mask = np.isfinite(s_row) & np.isfinite(f_row)
        n = int(mask.sum())
        if n < int(min_width):
            continue
        idx = np.where(mask)[0]
        order = idx[np.argsort(s_row[idx])]
        k = max(1, int(round(n * float(q))))
        shorts, longs = order[:k], order[-k:]
        out[t] = float(f_row[longs].mean() - f_row[shorts].mean())
    return pd.Series(out, index=signal.index)


def sharpe_pvalue(returns) -> float:
    """One-sided p-value for SR > 0 (normal approx): 1 - Phi(SR * sqrt(n))."""
    r = pd.Series(returns).dropna().to_numpy()
    if r.size < 2:
        return 1.0
    sr = sharpe(r)
    z = sr * np.sqrt(r.size)
    return float(1.0 - norm.cdf(z))


def dsr_for_returns(returns, *, n_trials: int) -> float:
    """Trials-deflated Pr[true SR > 0] for a return series."""
    r = pd.Series(returns).dropna().to_numpy()
    if r.size < 2:
        return 0.5
    return float(deflated_sharpe(
        sr_observed=sharpe(r),
        n_trials=int(n_trials),
        n_obs=int(r.size),
        skew=float(_skew(r)),
        kurt=float(_kurtosis(r, fisher=False)),  # Pearson: normal = 3
    ))


def fdr_bh(pvals: list[float], q: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg: return per-input boolean reject flags at level `q`."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    thresh_rank = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            thresh_rank = rank
    flags = [False] * m
    if thresh_rank >= 0:
        for rank, i in enumerate(order, start=1):
            if rank <= thresh_rank:
                flags[i] = True
    return flags


def pbo_over_alphas(returns_by_alpha: dict[str, pd.Series], *,
                    n_partitions: int = 16) -> float:
    """PBO over the T×K matrix of all alphas' OOS portfolio returns.

    Each series is reindexed to the union bar grid; missing bars (no
    position) become 0.0 return. Returns 0.5 (neutral) if T < n_partitions.
    """
    if not returns_by_alpha:
        return 0.5
    mat = pd.DataFrame(returns_by_alpha).sort_index().fillna(0.0)
    if mat.shape[0] < n_partitions or mat.shape[1] < 2:
        return 0.5
    return float(pbo(mat.to_numpy(), n_partitions=int(n_partitions)))
