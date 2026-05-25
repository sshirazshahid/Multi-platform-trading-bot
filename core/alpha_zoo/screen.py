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

from core.stat_tests import deflated_sharpe, pbo, sharpe


def cross_sectional_ic(signal: pd.DataFrame, fwd_ret: pd.DataFrame,
                       *, min_width: int = 10) -> pd.Series:
    """Per-bar Spearman rank-correlation of `signal` vs `fwd_ret` across symbols.

    A bar with fewer than `min_width` symbols valid in BOTH frames yields NaN.

    Vectorized: Spearman == Pearson of cross-sectional ranks. Both frames are
    masked to the per-bar intersection of finite values BEFORE ranking, so the
    ranks (and thus the correlation) are computed over exactly that
    intersection — identical to a per-bar spearmanr on the masked subset, but
    without the 2.6M-call Python loop on a full-history panel. A zero-variance
    bar yields a 0/0 -> NaN (matching the old explicit guard).
    """
    s = signal.to_numpy(dtype=float)
    f = fwd_ret.to_numpy(dtype=float)
    m = np.isfinite(s) & np.isfinite(f)
    n = m.sum(axis=1).astype(float)
    rank_s = pd.DataFrame(np.where(m, s, np.nan)).rank(axis=1).to_numpy()
    rank_f = pd.DataFrame(np.where(m, f, np.nan)).rank(axis=1).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        cnt = np.where(n > 0, n, np.nan)
        mean_s = (np.where(m, rank_s, 0.0).sum(axis=1) / cnt)[:, None]
        mean_f = (np.where(m, rank_f, 0.0).sum(axis=1) / cnt)[:, None]
        rc = np.where(m, rank_s - mean_s, 0.0)
        fc = np.where(m, rank_f - mean_f, 0.0)
        cov = (rc * fc).sum(axis=1)
        denom = np.sqrt((rc * rc).sum(axis=1) * (fc * fc).sum(axis=1))
        ic = cov / denom
    ic = np.where(n >= int(min_width), ic, np.nan)
    return pd.Series(ic, index=signal.index)


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
