"""Time-series regime gate tests — a genuinely predictive signal passes, noise is rejected."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo.ts_regime import timeseries_regime_gate


def _series(seed, kind, n=120):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    y = (0.8 * x + 0.6 * rng.normal(size=n)) if kind == "strong" else rng.normal(size=n)
    idx = pd.RangeIndex(n)
    return pd.Series(x, idx), pd.Series(y, idx)


def test_passes_real_relationship():
    s, f = _series(0, "strong")
    r = timeseries_regime_gate(s, f, n_shuffles=200, seed=1)
    assert r["t"] >= 3.5
    assert r["z_vs_null"] >= 3.5
    assert r["both_halves"]
    assert r["passes"] is True


def test_rejects_noise():
    s, f = _series(0, "noise")
    r = timeseries_regime_gate(s, f, n_shuffles=200, seed=1)
    assert r["passes"] is False


def test_insufficient_obs():
    s = pd.Series([1.0, 2.0, 3.0])
    f = pd.Series([1.0, 2.0, 3.0])
    r = timeseries_regime_gate(s, f, min_obs=30)
    assert r["passes"] is False
    assert r["reason"] == "insufficient_obs"


def test_deterministic():
    s, f = _series(0, "strong")
    a = timeseries_regime_gate(s, f, n_shuffles=100, seed=5)
    b = timeseries_regime_gate(s, f, n_shuffles=100, seed=5)
    assert a["z_vs_null"] == b["z_vs_null"]
    assert a["null_mean_abs_rho"] == b["null_mean_abs_rho"]


def test_step_subsamples_for_overlap():
    """step>1 subsamples to non-overlapping observations (overlap-aware significance)."""
    s, f = _series(0, "strong", n=120)
    full = timeseries_regime_gate(s, f, n_shuffles=50, seed=2, step=1)
    sub = timeseries_regime_gate(s, f, n_shuffles=50, seed=2, step=4)
    assert sub["n"] == len(range(0, 120, 4))  # 30 non-overlapping rows
    assert sub["n"] < full["n"]
