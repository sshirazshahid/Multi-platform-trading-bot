"""Phase 0.3: walk-forward + purged-CV invariants.

Tests:
- chronological: for every fold, max(train) < min(test)
- embargo: gap between train end and test start >= embargo_bars
- purge: no training row whose label window overlaps the test window
- anchored mode keeps train growing; rolling mode keeps train size bounded
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "walk_forward_test_copy", ROOT / "core" / "walk_forward.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["walk_forward_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wf_mod():
    return _load()


def test_split_is_chronological(wf_mod):
    wf = wf_mod.WalkForward(n_splits=4, embargo_bars=0)
    n = 1000
    for train_idx, test_idx in wf.split(n):
        assert len(train_idx) > 0
        assert len(test_idx) > 0
        assert int(train_idx.max()) < int(test_idx.min()), (
            "train must be entirely before test")


def test_embargo_gap_respected(wf_mod):
    embargo = 24
    wf = wf_mod.WalkForward(n_splits=4, embargo_bars=embargo)
    n = 1000
    for train_idx, test_idx in wf.split(n):
        gap = int(test_idx.min()) - int(train_idx.max())
        assert gap > embargo, (
            f"embargo violation: gap={gap}, expected > {embargo}")


def test_anchored_mode_train_grows(wf_mod):
    """Anchored = each fold's train starts at 0 and grows."""
    wf = wf_mod.WalkForward(n_splits=4, embargo_bars=0, anchored=True)
    n = 1000
    sizes = [len(train_idx) for train_idx, _ in wf.split(n)]
    assert sizes == sorted(sizes), f"anchored should grow; got {sizes}"
    # First train must start at 0 (anchored)
    first_train = next(iter(wf.split(n)))[0]
    assert int(first_train.min()) == 0


def test_rolling_mode_train_bounded(wf_mod):
    """Rolling = train window size bounded across folds."""
    wf = wf_mod.WalkForward(n_splits=4, embargo_bars=0, anchored=False)
    n = 1000
    sizes = [len(train_idx) for train_idx, _ in wf.split(n)]
    # Last fold's train shouldn't be much larger than first (rolling, not growing)
    assert max(sizes) <= 2 * min(sizes), (
        f"rolling mode should bound size; got {sizes}")


def test_purge_drops_contaminated_train_rows(wf_mod):
    """A training row at t whose label uses bars [t, t+horizon] must be
    dropped if any of those bars lands inside the test window."""
    train_idx = np.arange(0, 100)
    test_idx = np.arange(110, 130)  # test starts at 110
    horizon = 15
    purged = wf_mod.WalkForward.purge(train_idx, test_idx, horizon)
    # Any train row t where t + horizon >= 110 must be removed
    # i.e. t >= 95. So purged train should be < 95.
    assert int(purged.max()) < 95
    assert int(purged.max()) == 94


def test_purge_with_zero_horizon_is_identity(wf_mod):
    train_idx = np.arange(0, 100)
    test_idx = np.arange(110, 130)
    purged = wf_mod.WalkForward.purge(train_idx, test_idx, 0)
    np.testing.assert_array_equal(purged, train_idx)


def test_purge_with_empty_test_returns_train_unchanged(wf_mod):
    train_idx = np.arange(0, 100)
    test_idx = np.array([], dtype=int)
    purged = wf_mod.WalkForward.purge(train_idx, test_idx, 10)
    np.testing.assert_array_equal(purged, train_idx)


def test_split_yields_n_splits_folds(wf_mod):
    for n_splits in (2, 3, 4, 6):
        wf = wf_mod.WalkForward(n_splits=n_splits, embargo_bars=5)
        folds = list(wf.split(500))
        assert len(folds) == n_splits


def test_split_disjoint_test_indices(wf_mod):
    """Test windows across folds must not overlap (proper TimeSeriesSplit
    semantics)."""
    wf = wf_mod.WalkForward(n_splits=4, embargo_bars=0)
    test_sets = [set(test_idx.tolist()) for _, test_idx in wf.split(1000)]
    for i in range(len(test_sets)):
        for j in range(i + 1, len(test_sets)):
            assert not (test_sets[i] & test_sets[j]), (
                f"fold {i} and {j} have overlapping test indices")


def test_split_accepts_array_input(wf_mod):
    """split() should accept either an int n or an array-like X."""
    wf = wf_mod.WalkForward(n_splits=2, embargo_bars=0)
    # int form
    folds_int = list(wf.split(100))
    # array form (length 100)
    folds_arr = list(wf.split(np.zeros(100)))
    assert len(folds_int) == len(folds_arr)
    for (t1, te1), (t2, te2) in zip(folds_int, folds_arr):
        np.testing.assert_array_equal(t1, t2)
        np.testing.assert_array_equal(te1, te2)
