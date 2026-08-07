"""Trial-matrix construction for CSCV/PBO (edge-queue row 8).

The trainer's PBO was documented untrustworthy because it fed *folds of the
single winning hyperparameter* to ``core.stat_tests.pbo`` as if folds were
strategies (see the note at core/promotion_gate.py:291 — that file is
SHA-256-frozen by the D0 harness, so its stale comment is corrected here and
in the trainer, not in place).

Correct construction per Bailey et al.: columns are the FULL trial grid —
every (model, hyperparameter) combination evaluated, winners AND abandoned
cells — and rows are time-aligned per-period PnL. Only then does "the
in-sample winner's out-of-sample rank" measure selection overfitting.

Scope guard: this is measurement repair. Gate thresholds are untouched
(promotion_gate.py is hash-frozen), so a corrected PBO can only inform or
reject — it can never admit a model the frozen gate would have refused.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.stat_tests import pbo, trial_pnl_matrix


# ── construction ───────────────────────────────────────────────────────────

def test_columns_are_all_trials_and_rows_are_the_index_union():
    y = np.array([1, 0, 1, 1, 0, 1])
    trials = {
        "lr_C0.1": {0: 0.9, 2: 0.9, 4: 0.9},        # decides rows 0,2,4
        "gbm_lr0.05": {1: 0.9, 2: 0.2, 5: 0.9},      # decides 1,5; flat on 2
    }
    mat, names = trial_pnl_matrix(y, trials, threshold=0.5)
    assert names == ["gbm_lr0.05", "lr_C0.1"]  # deterministic (sorted) order
    assert mat.shape == (5, 2)                  # union of indices {0,1,2,4,5}
    col = {n: i for i, n in enumerate(names)}
    # lr trial: row0 win +1, row2 win +1, row4 loss -1; flat elsewhere
    assert mat[:, col["lr_C0.1"]].tolist() == [1.0, 0.0, 1.0, -1.0, 0.0]
    # gbm trial: row1 loss -1, row2 below threshold -> flat 0, row5 win +1
    assert mat[:, col["gbm_lr0.05"]].tolist() == [0.0, -1.0, 0.0, 0.0, 1.0]


def test_rows_are_time_ordered_by_dataset_index():
    y = np.ones(10)
    trials = {"a": {7: 0.9, 1: 0.9, 4: 0.9}, "b": {1: 0.9}}
    mat, names = trial_pnl_matrix(y, trials)
    # decided rows in index order 1,4,7
    assert mat.shape == (3, 2)
    col = {n: i for i, n in enumerate(names)}
    assert mat[:, col["a"]].tolist() == [1.0, 1.0, 1.0]
    assert mat[:, col["b"]].tolist() == [1.0, 0.0, 0.0]


def test_below_threshold_and_missing_rows_are_flat_zero():
    y = np.array([1, 1])
    trials = {"a": {0: 0.4}, "b": {0: 0.9, 1: 0.9}}
    mat, names = trial_pnl_matrix(y, trials, threshold=0.5)
    col = {n: i for i, n in enumerate(names)}
    assert mat[:, col["a"]].tolist() == [0.0, 0.0]   # no-decision = flat
    assert mat[:, col["b"]].tolist() == [1.0, 1.0]


def test_empty_or_single_trial_raises():
    with pytest.raises(ValueError):
        trial_pnl_matrix(np.ones(4), {})
    with pytest.raises(ValueError):
        trial_pnl_matrix(np.ones(4), {"only": {0: 0.9}})


# ── the measurement actually discriminates ─────────────────────────────────

def _noise_trials(rng, n_trials, n_rows):
    return {
        f"noise{k}": {i: rng.random() for i in range(n_rows)}
        for k in range(n_trials)
    }


def test_pure_noise_grid_yields_uninformative_pbo():
    """All trials random => picking the IS winner conveys nothing; PBO ~ 0.5.
    The broken folds-as-strategies feed could not produce this property."""
    rng = np.random.default_rng(7)
    n = 256
    y = (rng.random(n) < 0.5).astype(float)
    mat, _ = trial_pnl_matrix(y, _noise_trials(rng, 8, n))
    score = pbo(mat, n_partitions=8)
    assert 0.25 <= score <= 0.75


def test_genuinely_dominant_trial_yields_low_pbo():
    rng = np.random.default_rng(11)
    n = 256
    y = (rng.random(n) < 0.5).astype(float)
    trials = _noise_trials(rng, 7, n)
    # one trial that "knows" the outcome 80% of the time, uniformly over time
    trials["skilled"] = {
        i: (0.9 if (y[i] == 1) == (rng.random() < 0.8) else 0.1)
        for i in range(n)
    }
    mat, _ = trial_pnl_matrix(y, trials)
    score = pbo(mat, n_partitions=8)
    assert score <= 0.30, f"dominant trial should generalize; PBO={score}"
