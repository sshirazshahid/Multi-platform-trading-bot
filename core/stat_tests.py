"""Statistical tests for backtest validity.

Three primitives:

  * `sharpe(returns)` — sample Sharpe ratio (mean / std(ddof=1)).
  * `bootstrap_ci(returns, statistic, ...)` — confidence interval via
    `scipy.stats.bootstrap` (percentile method, configurable n_resamples
    and seed for reproducibility).
  * `deflated_sharpe(sr, n_trials, skew, kurt, n_obs)` — Bailey & López
    de Prado 2014 closed-form DSR. Returns the probability that the
    observed Sharpe exceeds the expected-max-Sharpe under the null
    (no-skill) given `n_trials` strategy attempts.
  * `pbo(returns_matrix, n_partitions)` — Probability of Backtest
    Overfitting via Combinatorially Symmetric Cross-Validation
    (Bailey et al. 2017).

References:
  - Bailey & López de Prado (2014) "The Deflated Sharpe Ratio".
  - Bailey, Borwein, López de Prado, Zhu (2017) "The Probability of
    Backtest Overfitting".
"""
from __future__ import annotations

from itertools import combinations
from math import exp, sqrt

import numpy as np
from scipy.stats import bootstrap as _scipy_bootstrap
from scipy.stats import norm

# Euler-Mascheroni constant — used in expected-max-of-N normals approximation.
_GAMMA = 0.5772156649015328606


def sharpe(returns: np.ndarray) -> float:
    """Sample Sharpe ratio (no annualization). Returns 0 for degenerate input."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0
    s = r.std(ddof=1)
    if s <= 0:
        return 0.0
    return float(r.mean() / s)


def bootstrap_ci(
    returns,
    statistic=None,
    n_resamples: int = 10000,
    ci: float = 0.95,
    random_state: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap CI for `statistic` on `returns`.

    Default statistic = sample Sharpe.
    """
    if statistic is None:
        statistic = sharpe
    res = _scipy_bootstrap(
        (np.asarray(returns, dtype=float),),
        statistic,
        n_resamples=int(n_resamples),
        confidence_level=float(ci),
        random_state=int(random_state),
        method="percentile",
        vectorized=False,
    )
    lo = float(res.confidence_interval.low)
    hi = float(res.confidence_interval.high)
    return lo, hi


def _expected_max_sr_under_null(n_trials: int, sr_var: float = 1.0) -> float:
    """Bailey & López de Prado approximation for E[max SR_n] under null.

    E[max] ≈ sqrt(V[SR]) × ((1-γ) Z^{-1}(1 - 1/N) + γ Z^{-1}(1 - 1/(N·e)))
    where γ is Euler-Mascheroni, Z^{-1} is inverse normal CDF, N = n_trials.
    """
    n = max(2, int(n_trials))  # need >= 2 for ppf to be defined
    z1 = norm.ppf(1.0 - 1.0 / n)
    z2 = norm.ppf(1.0 - 1.0 / (n * exp(1.0)))
    return sqrt(max(0.0, sr_var)) * ((1.0 - _GAMMA) * z1 + _GAMMA * z2)


def deflated_sharpe(
    sr_observed: float,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_var: float = 1.0,
) -> float:
    """Deflated Sharpe Ratio probability.

    Returns Pr[true SR > 0 | observed SR, n_trials], adjusted for:
      - selection bias from `n_trials` strategy attempts
      - return non-normality via `skew` and `kurt` (kurt=3 = normal)
      - sample size `n_obs`

    Higher = more skill-like. Reject "no skill" null at p > 1 − α.

    For `n_trials = 1` this reduces to the Probabilistic Sharpe Ratio
    (Bailey & López de Prado 2012).
    """
    if n_obs < 2:
        return 0.5
    expected_max = _expected_max_sr_under_null(n_trials, sr_var)
    excess_kurt = kurt - 3.0
    denom_sq = 1.0 - skew * sr_observed + (excess_kurt / 4.0) * sr_observed * sr_observed
    denom = sqrt(max(1e-12, denom_sq))
    z = (sr_observed - expected_max) * sqrt(n_obs - 1) / denom
    return float(norm.cdf(z))


def pbo(returns: np.ndarray, n_partitions: int = 16) -> float:
    """Probability of Backtest Overfitting via Combinatorially Symmetric CV.

    Args:
        returns: shape (T, N). T = time periods, N = strategies.
        n_partitions: number of equal-size chunks to split T into. Must be
            even (we choose half for in-sample, half for out-of-sample).

    Algorithm (Bailey et al. 2017):
      1. Split T into S = n_partitions equal-size chunks.
      2. For each combination C(S, S/2): pick S/2 chunks as IS, rest OOS.
      3. Find best strategy by IS Sharpe; compute its OOS rank percentile.
      4. logit(rank) < 0 (i.e. below-median OOS) ⇒ "overfit".
      5. PBO = fraction of combinations where best-IS is below-median OOS.

    Returns PBO ∈ [0, 1]. ~0.5 means picking the in-sample winner conveys
    no information about out-of-sample performance (pure overfit). Closer
    to 0 = the in-sample winner reliably wins out-of-sample.
    """
    if n_partitions % 2 != 0:
        raise ValueError("n_partitions must be even")
    R = np.asarray(returns, dtype=float)
    if R.ndim != 2:
        raise ValueError(f"returns must be 2-D (T, N); got shape {R.shape}")
    T, N = R.shape
    if T < n_partitions:
        raise ValueError(f"need T >= n_partitions; got T={T}, n_partitions={n_partitions}")

    chunk = T // n_partitions
    # Drop any straggler tail rows so chunks divide evenly.
    R = R[: chunk * n_partitions]
    chunks = [R[i * chunk : (i + 1) * chunk] for i in range(n_partitions)]
    half = n_partitions // 2
    all_idx = list(range(n_partitions))

    pbo_count = 0
    total = 0
    for is_combo in combinations(all_idx, half):
        is_set = set(is_combo)
        oos_combo = [i for i in all_idx if i not in is_set]
        is_data = np.vstack([chunks[i] for i in is_combo])
        oos_data = np.vstack([chunks[i] for i in oos_combo])
        is_sr = is_data.mean(axis=0) / (is_data.std(axis=0, ddof=1) + 1e-12)
        oos_sr = oos_data.mean(axis=0) / (oos_data.std(axis=0, ddof=1) + 1e-12)
        best = int(np.argmax(is_sr))
        # OOS rank percentile of `best` (0 = worst, 1 = best).
        rank_pct = float((oos_sr <= oos_sr[best]).mean())
        # Avoid log(0) / log(inf).
        rank_pct = float(np.clip(rank_pct, 1e-3, 1 - 1e-3))
        w = np.log(rank_pct / (1 - rank_pct))
        if w < 0:
            pbo_count += 1
        total += 1
    return pbo_count / total if total else 0.5


def trial_pnl_matrix(
    y: np.ndarray,
    trials: dict,
    *,
    threshold: float = 0.5,
) -> tuple:
    """Build the (T, N) CSCV trial matrix from a full hyperparameter sweep.

    Edge-queue row 8 (2026-08-03 advisor-conditioned): ``pbo()`` only measures
    selection overfitting when its COLUMNS are the strategies that competed in
    selection — the full (model, hyperparameter) grid, winners AND abandoned
    cells. Feeding folds of the single winner (the trainer's previous
    construction) measures nothing; core/promotion_gate.py:291 documented that
    PBO from this pipeline was therefore untrustworthy.

    Args:
        y: shape (n_rows,) binary trade outcomes for the dataset rows.
        trials: mapping ``trial_name -> {row_index: p_win}`` — the
            out-of-sample predictions of EVERY trial evaluated in the sweep.
        threshold: decision threshold; a trial "takes" a row iff p >= threshold.

    Rows are the union of all trials' OOS indices in index order (walk-forward
    indices are chronological, so this is time order). Cell values: +1 win /
    -1 loss when the trial took the trade, 0.0 (flat) when it did not — a
    trial that made no trade that period has zero PnL that period, which keeps
    all columns on the same clock as CSCV requires.

    Returns:
        (matrix, trial_names) with ``matrix.shape == (T, len(trials))`` and
        ``trial_names`` sorted for determinism.

    Raises:
        ValueError: fewer than two trials — selection overfitting is
        undefined for a single candidate.
    """
    if len(trials) < 2:
        raise ValueError(
            f"trial_pnl_matrix needs >= 2 trials (got {len(trials)}): "
            "PBO measures selection among competitors"
        )
    y_arr = np.asarray(y, dtype=float)
    names = sorted(trials)
    rows = sorted({int(i) for t in trials.values() for i in t})
    mat = np.zeros((len(rows), len(names)), dtype=float)
    row_pos = {idx: r for r, idx in enumerate(rows)}
    for c, name in enumerate(names):
        for idx, p in trials[name].items():
            if float(p) >= threshold:
                mat[row_pos[int(idx)], c] = 1.0 if y_arr[int(idx)] == 1 else -1.0
    return mat, names
