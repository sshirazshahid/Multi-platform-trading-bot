"""Bias detectors for the alpha pre-registration gate.

Attacks the documented PBO=1.0 / "survives in-sample, dies OOS" failure mode by catching
self-deception before a candidate alpha is ever screened.

LOOK-AHEAD: a feature whose value at bar i changes when bars after i are removed is
peeking at the future (shift(-n), centered windows, unbounded max/mean over the whole
series). `lookahead_violations` recomputes the feature on each truncated prefix and flags
any bar whose value moves.

(A same-universe shuffle-control significance check is a planned follow-up increment.)
"""
from __future__ import annotations

import numpy as np


def lookahead_violations(compute_fn, series, *, check_indices=None, atol=1e-9):
    """Return the indices where ``compute_fn`` peeks ahead.

    compute_fn: callable(sequence) -> sequence of the SAME length (a feature/indicator).
    series:     the full input sequence (list / np.ndarray / pd.Series).

    A causal feature satisfies ``compute_fn(series)[i] == compute_fn(series[:i+1])[i]``
    for every i — its value at bar i depends only on data up to i. Any i where that fails
    is a look-ahead violation. Indices where the truncated recompute can't yield position
    i (shorter output) are skipped rather than flagged.
    """
    full = np.asarray(compute_fn(series), dtype=float)
    n = len(full)
    idxs = range(n) if check_indices is None else check_indices
    bad: list[int] = []
    for i in idxs:
        if i < 0 or i >= n:
            continue
        trunc = np.asarray(compute_fn(series[: i + 1]), dtype=float)
        if len(trunc) <= i:
            continue
        if not np.isclose(full[i], trunc[i], atol=atol, equal_nan=True):
            bad.append(int(i))
    return bad


def is_causal(compute_fn, series, **kwargs) -> bool:
    """True iff ``compute_fn`` shows no look-ahead violations on ``series``."""
    return len(lookahead_violations(compute_fn, series, **kwargs)) == 0
