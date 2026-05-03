"""Walk-forward + purged cross-validation.

Wraps `sklearn.model_selection.TimeSeriesSplit` with two additions critical
for time-series ML on financial data:

  1. EMBARGO — a gap of `embargo_bars` between the last training row and
     the first test row, preventing label leakage from labels whose
     forward-looking horizon would otherwise reach into the test window.

  2. PURGE — a static method that drops training rows whose label window
     [t, t + label_horizon_bars] overlaps the test window. Use this when
     labels (e.g. triple-barrier) require a forward look.

Modes:
  * anchored=True  — every fold's train starts at 0 and grows. Default.
                     Best when more data → strictly better (regime-stable).
  * anchored=False — rolling fixed-size train window. Use when older data
                     is stale (regime drift).

Reference: López de Prado, "Advances in Financial Machine Learning" Ch.7.
"""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from sklearn.model_selection import TimeSeriesSplit


class WalkForward:
    """Walk-forward CV with embargo + purge support."""

    def __init__(
        self,
        n_splits: int = 4,
        embargo_bars: int = 0,
        anchored: bool = True,
    ) -> None:
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if embargo_bars < 0:
            raise ValueError("embargo_bars must be >= 0")
        self.n_splits = n_splits
        self.embargo_bars = embargo_bars
        self.anchored = anchored

    def split(self, X) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield (train_idx, test_idx) pairs.

        X: either an int (length) or an array-like whose length is used.
        """
        n = X if isinstance(X, (int, np.integer)) else len(X)
        tss = TimeSeriesSplit(n_splits=self.n_splits)
        # Pre-compute fold geometry by feeding an arange of length n.
        dummy = np.arange(int(n))
        for train_idx, test_idx in tss.split(dummy):
            test_start = int(test_idx[0])
            # Embargo: drop trailing train rows within `embargo_bars` of test.
            if self.embargo_bars > 0:
                cutoff = test_start - self.embargo_bars
                train_idx = train_idx[train_idx < cutoff]
            # Rolling mode: bound train window to size of test set.
            if not self.anchored:
                window_size = len(test_idx)
                if len(train_idx) > window_size:
                    train_idx = train_idx[-window_size:]
            yield train_idx, test_idx

    @staticmethod
    def purge(
        train_idx: np.ndarray,
        test_idx: np.ndarray,
        label_horizon_bars: int,
    ) -> np.ndarray:
        """Drop training rows whose label window overlaps the test window.

        A training row at index `t` has a label that depends on bars
        [t, t + label_horizon_bars]. If any of those bars are in the test
        set (i.e. t + label_horizon_bars >= test_start), the label has
        seen future test information — drop the row.

        Returns a filtered copy of train_idx; original is not modified.
        """
        train_idx = np.asarray(train_idx)
        test_idx = np.asarray(test_idx)
        if len(test_idx) == 0:
            return train_idx
        if label_horizon_bars <= 0:
            return train_idx
        test_start = int(test_idx.min())
        keep = (train_idx + label_horizon_bars) < test_start
        return train_idx[keep]
