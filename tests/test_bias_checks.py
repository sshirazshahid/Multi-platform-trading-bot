"""Bias-detector tests — look-ahead detection for the alpha pre-registration gate.

A feature whose value at bar i changes when bars AFTER i are removed is peeking at the
future (shift(-n), centered windows, unbounded max/mean over the whole series). Catching
this before a candidate is screened attacks the documented PBO=1.0 / dies-OOS failure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo.bias_checks import (
    is_causal,
    lookahead_violations,
    shuffle_control_significance,
)

PRICES = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], dtype=float)


def test_causal_rolling_mean_has_no_violations():
    f = lambda s: pd.Series(s).rolling(3, min_periods=1).mean().to_numpy()  # noqa: E731
    assert lookahead_violations(f, PRICES) == []
    assert is_causal(f, PRICES)


def test_identity_is_causal():
    assert is_causal(lambda s: np.asarray(s, dtype=float), PRICES)


def test_shift_negative_is_caught():
    """Using the NEXT bar's value is look-ahead."""
    f = lambda s: pd.Series(s).shift(-1).fillna(0.0).to_numpy()  # noqa: E731
    assert lookahead_violations(f, PRICES)        # non-empty
    assert not is_causal(f, PRICES)


def test_centered_window_is_caught():
    f = lambda s: pd.Series(s).rolling(3, center=True, min_periods=1).mean().to_numpy()  # noqa: E731
    assert lookahead_violations(f, PRICES)


def test_global_max_is_caught():
    """Value at i depends on the whole series (unbounded forward look)."""
    f = lambda s: np.full(len(s), float(np.max(s)))  # noqa: E731
    assert lookahead_violations(f, PRICES)


def test_check_indices_subset():
    """Only the requested indices are inspected."""
    f = lambda s: pd.Series(s).shift(-1).fillna(0.0).to_numpy()  # noqa: E731
    # index 7 (last) is causal under shift(-1) (full[-1]==0==trunc[-1]); restrict to it
    assert lookahead_violations(f, PRICES, check_indices=[7]) == []


# ── Same-universe shuffle-control significance (Harvey-Liu-Zhu, |t|>=3.5) ──

def _panels(seed, kind, n_bars=40, n_sym=12):
    rng = np.random.default_rng(seed)
    fwd = rng.normal(size=(n_bars, n_sym))
    if kind == "strong":
        sig = fwd + 0.3 * rng.normal(size=(n_bars, n_sym))  # genuinely predictive
    else:
        sig = rng.normal(size=(n_bars, n_sym))              # independent noise
    cols = [f"S{i}" for i in range(n_sym)]
    idx = pd.RangeIndex(n_bars)
    return pd.DataFrame(sig, index=idx, columns=cols), pd.DataFrame(fwd, index=idx, columns=cols)


def test_shuffle_control_passes_real_signal():
    sig, fwd = _panels(0, "strong")
    r = shuffle_control_significance(sig, fwd, n_shuffles=100, seed=1)
    assert r["ic_t"] >= 3.5
    assert r["z_vs_null"] >= 3.5
    assert r["passes"] is True


def test_shuffle_control_rejects_noise():
    sig, fwd = _panels(0, "noise")
    r = shuffle_control_significance(sig, fwd, n_shuffles=100, seed=1)
    assert r["passes"] is False
    assert r["z_vs_null"] < 3.5


def test_shuffle_control_is_deterministic():
    sig, fwd = _panels(0, "strong")
    a = shuffle_control_significance(sig, fwd, n_shuffles=50, seed=7)
    b = shuffle_control_significance(sig, fwd, n_shuffles=50, seed=7)
    assert a["z_vs_null"] == b["z_vs_null"]
    assert a["null_abs_ir_mean"] == b["null_abs_ir_mean"]
