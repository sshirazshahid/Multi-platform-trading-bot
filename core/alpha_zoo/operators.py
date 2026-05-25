# core/alpha_zoo/operators.py
"""Backward-only vectorized operators for formulaic alphas (Alpha101 / GTJA
semantics). Operate on wide (T bars × N symbols) DataFrames.

INVARIANT (enforced by tests/test_alpha_lookahead_sentinel.py): every
time-series operator uses ONLY the current and prior rows. `shift(d>0)`
pulls the past forward; `rolling(d)` spans [t-d+1, t]. No operator may
reference a future row. Cross-sectional operators (`rank`, `scale`) act
across columns within a single row and are inherently lookahead-safe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Cross-sectional (across symbols, per bar) ────────────────────────────
def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in [0, 1], per row."""
    return df.rank(axis=1, pct=True)


def scale(df: pd.DataFrame, k: float = 1.0) -> pd.DataFrame:
    """Rescale each row so sum(|x|) == k."""
    denom = df.abs().sum(axis=1).replace(0.0, np.nan)
    return df.mul(k / denom, axis=0)


# ── Time-series (down the rows, per symbol; backward-only) ────────────────
def delay(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.shift(int(d))


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df - df.shift(int(d))


def ts_sum(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).sum()


def sma(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).mean()


def stddev(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).std(ddof=1)


def ts_min(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).min()


def ts_max(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).max()


def ts_argmax(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Index (0..d-1) of the max within the trailing window; d-1 = most recent."""
    return df.rolling(int(d), min_periods=int(d)).apply(np.argmax, raw=True)


def ts_argmin(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).apply(np.argmin, raw=True)


def ts_rank(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Percentile rank of the current value within its trailing `d`-window."""
    def _last_rank(a: np.ndarray) -> float:
        return (a <= a[-1]).mean()
    return df.rolling(int(d), min_periods=int(d)).apply(_last_rank, raw=True)


def decay_linear(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Linearly-weighted moving average; most recent gets the highest weight."""
    d = int(d)
    w = np.arange(1, d + 1, dtype=float)
    w /= w.sum()

    def _wavg(a: np.ndarray) -> float:
        return float(np.dot(a, w))
    return df.rolling(d, min_periods=d).apply(_wavg, raw=True)


def correlation(a: pd.DataFrame, b: pd.DataFrame, d: int) -> pd.DataFrame:
    """Rolling Pearson correlation between aligned columns of `a` and `b`."""
    d = int(d)
    return a.rolling(d, min_periods=d).corr(b)


def covariance(a: pd.DataFrame, b: pd.DataFrame, d: int) -> pd.DataFrame:
    d = int(d)
    return a.rolling(d, min_periods=d).cov(b)


# ── Elementwise ──────────────────────────────────────────────────────────
def signed_power(df: pd.DataFrame, a: float) -> pd.DataFrame:
    return np.sign(df) * (df.abs() ** float(a))


def log(df: pd.DataFrame) -> pd.DataFrame:
    return np.log(df.where(df > 0))


def sign(df: pd.DataFrame) -> pd.DataFrame:
    return np.sign(df)


def abs_(df: pd.DataFrame) -> pd.DataFrame:
    return df.abs()


def elem_min(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a.where(a < b, b)


def elem_max(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a.where(a > b, b)
