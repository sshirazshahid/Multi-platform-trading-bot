"""Embargo must be >= label horizon or the walk-forward leaks future labels.

The 2026-05-25 no-edge-forensics finding: the live ensemble's 0.80 OOS-WR
coexisted with PBO=1.0 (max overfit). Root-cause hypothesis: the CV ran with
embargo_bars=24 while labels use a 96-bar forward horizon, so training rows
within 96 bars of a test fold carry labels that peek into the test window.
"""
from __future__ import annotations

import numpy as np

from core.walk_forward import WalkForward


def _series(n=400):
    return np.random.default_rng(0).normal(size=(n, 1))


def test_embargo_below_horizon_overlaps_test():
    """embargo=24 < horizon=96: at least one fold has < horizon bars between
    the last train row and the first test row (label window overlaps test)."""
    x = _series()
    wf = WalkForward(n_splits=5, embargo_bars=24)
    horizon = 96
    overlaps = []
    for tr, te in wf.split(x):
        if len(tr) and len(te):
            overlaps.append((te[0] - tr[-1]) <= horizon)
    assert any(overlaps), "embargo=24 must leave at least one overlapping fold"


def test_embargo_at_horizon_purges_overlap():
    """embargo=96 >= horizon=96: no fold's last train row label window
    reaches the test fold."""
    x = _series()
    wf = WalkForward(n_splits=5, embargo_bars=96)
    horizon = 96
    for tr, te in wf.split(x):
        if len(tr) and len(te):
            gap = te[0] - tr[-1]
            assert gap >= horizon, (
                f"embargo>=horizon must purge; gap={gap} < horizon={horizon}")
