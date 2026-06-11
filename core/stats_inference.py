"""core/stats_inference.py — confidence-aware inference for trade statistics.

2026-06-11. Companion to core/stat_tests.py (per-bar-return / Sharpe,
scipy-backed). This module is binomial-WR + trade-PnL oriented: pure
numpy + stdlib math, deterministic seeds, safe for the bot hot path
(closed forms only; bootstrap is opt-in for offline scripts).

Purpose: the bot's gates and reports previously compared point
estimates (mean PnL, raw WR) with no notion of sampling noise — e.g.
an "hour with +$8 over 36 trades" reads as profitable when its 95% CI
straddles zero. These helpers make noise visible WITHOUT changing any
gate rule (owner directives stand; CI fields are informational).
"""
from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

Z_95 = 1.959963984540054


# ── binomial proportion ────────────────────────────────────────────────


def wilson_interval(wins: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion, in [0, 1].

    n <= 0 -> (0.0, 1.0) (total ignorance). Never raises."""
    try:
        wins = int(wins)
        n = int(n)
        z = float(z)
    except (TypeError, ValueError):
        return (0.0, 1.0)
    if n <= 0:
        return (0.0, 1.0)
    wins = max(0, min(wins, n))
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ── Student-t distribution (pure stdlib — no scipy) ───────────────────


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta
    (Numerical Recipes betacf)."""
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Student-t CDF. df <= 0 -> 0.5 (degenerate); +/-inf -> 1.0/0.0."""
    try:
        t = float(t)
        df = float(df)
    except (TypeError, ValueError):
        return 0.5
    if df <= 0 or t != t:  # bad df or NaN t
        return 0.5
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    x = df / (df + t * t)
    p = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - p if t >= 0 else p


def t_ppf(p: float, df: float) -> float:
    """Inverse t CDF by bisection on t_cdf (tol 1e-9, deterministic)."""
    p = float(p)
    df = float(df)
    if not (0.0 < p < 1.0) or df <= 0:
        return float("nan")
    lo, hi = -1000.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    return (lo + hi) / 2.0


# ── mean confidence intervals ──────────────────────────────────────────


def mean_ci(values: Sequence[float], ci: float = 0.95) -> Tuple[float, float, float]:
    """(mean, lo, hi) — Student-t CI on the mean. n<2 -> (mean, nan, nan).
    Closed form O(n): the only variant allowed in the bot hot path."""
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    m = sum(vals) / n
    if n < 2:
        return (m, float("nan"), float("nan"))
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    se = math.sqrt(var / n)
    tcrit = t_ppf(0.5 + ci / 2.0, n - 1)
    return (m, m - tcrit * se, m + tcrit * se)


def mean_ci_bootstrap(values: Sequence[float], n_resamples: int = 4000,
                      ci: float = 0.95, seed: int = 42) -> Tuple[float, float, float]:
    """(mean, lo, hi) percentile bootstrap. Deterministic per seed."""
    vals = np.asarray([float(v) for v in values if v is not None], dtype=float)
    n = len(vals)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    m = float(vals.mean())
    if n < 2:
        return (m, float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(int(n_resamples), n))
    means = vals[idx].mean(axis=1)
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    return (m, float(lo), float(hi))


# ── two-sample comparison ─────────────────────────────────────────────


def welch_t_test(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    """(t_stat, df_welch, p_two_sided) — Welch-Satterthwaite; p via t_cdf.
    Degenerate input (n<2 either arm, or zero variance in both) ->
    (0.0, 1.0, 1.0)."""
    xa = [float(v) for v in a if v is not None]
    xb = [float(v) for v in b if v is not None]
    na, nb = len(xa), len(xb)
    if na < 2 or nb < 2:
        return (0.0, 1.0, 1.0)
    ma, mb = sum(xa) / na, sum(xb) / nb
    va = sum((v - ma) ** 2 for v in xa) / (na - 1)
    vb = sum((v - mb) ** 2 for v in xb) / (nb - 1)
    if va <= 0 and vb <= 0:
        return (0.0, 1.0, 1.0)
    se2 = va / na + vb / nb
    t = (mb - ma) / math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = 2.0 * (1.0 - t_cdf(abs(t), df))
    return (t, df, min(max(p, 0.0), 1.0))


def bootstrap_delta_ci(a: Sequence[float], b: Sequence[float],
                       n_resamples: int = 4000, ci: float = 0.95,
                       seed: int = 42) -> Tuple[float, float, float]:
    """(mean(b)-mean(a), lo, hi); independent resampling of each arm."""
    xa = np.asarray([float(v) for v in a if v is not None], dtype=float)
    xb = np.asarray([float(v) for v in b if v is not None], dtype=float)
    if len(xa) == 0 or len(xb) == 0:
        return (float("nan"), float("nan"), float("nan"))
    delta = float(xb.mean() - xa.mean())
    if len(xa) < 2 or len(xb) < 2:
        return (delta, float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(xa), size=(int(n_resamples), len(xa)))
    ib = rng.integers(0, len(xb), size=(int(n_resamples), len(xb)))
    deltas = xb[ib].mean(axis=1) - xa[ia].mean(axis=1)
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(deltas, [alpha, 1.0 - alpha])
    return (delta, float(lo), float(hi))


def two_sample_verdict(pre: Sequence[float], post: Sequence[float],
                       alpha: float = 0.05, min_n: int = 20,
                       n_resamples: int = 4000, seed: int = 42) -> dict:
    """Honest A/B verdict on per-trade PnL arrays.

    Conservative — BOTH the bootstrap delta CI and Welch p must agree:
      min(n_pre, n_post) < min_n             -> INCONCLUSIVE (insufficient_n)
      delta>0 AND ci_lo>0 AND welch_p<alpha  -> IMPROVED
      delta<0 AND ci_hi<0 AND welch_p<alpha  -> WORSE
      else                                   -> INCONCLUSIVE (ci_straddles_zero)
    """
    xa = [float(v) for v in pre if v is not None]
    xb = [float(v) for v in post if v is not None]
    out = {"n_pre": len(xa), "n_post": len(xb)}
    if min(len(xa), len(xb)) < int(min_n):
        out.update({"verdict": "INCONCLUSIVE", "reason": "insufficient_n",
                    "delta": float("nan"), "delta_ci": [float("nan")] * 2,
                    "welch_t": 0.0, "welch_df": 1.0, "welch_p": 1.0})
        return out
    t, df, p = welch_t_test(xa, xb)
    delta, lo, hi = bootstrap_delta_ci(xa, xb, n_resamples=n_resamples, seed=seed)
    out.update({"delta": delta, "delta_ci": [lo, hi],
                "welch_t": t, "welch_df": df, "welch_p": p})
    if delta > 0 and lo > 0 and p < alpha:
        out.update({"verdict": "IMPROVED", "reason": "ci_and_welch_agree"})
    elif delta < 0 and hi < 0 and p < alpha:
        out.update({"verdict": "WORSE", "reason": "ci_and_welch_agree"})
    else:
        out.update({"verdict": "INCONCLUSIVE", "reason": "ci_straddles_zero"})
    return out
