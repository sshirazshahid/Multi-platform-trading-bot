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
import pandas as pd


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


def shuffle_control_significance(signal, fwd, *, n_shuffles=200, seed=0,
                                 min_abs_t=3.5, min_width=10):
    """Same-universe shuffle control + IC t-stat (Harvey-Liu-Zhu).

    Computes the real cross-sectional IC IR and its t-stat (|IR|*sqrt(n_bars)), then
    builds a null by permuting ``fwd`` returns ACROSS SYMBOLS within each bar — this
    breaks the signal->return link while preserving each bar's return distribution, so
    a signal that merely rides shared cross-sectional structure/beta scores no better
    than the null. A real edge must clear BOTH gates:
      * IC t-stat >= min_abs_t (default 3.5, the post-multiple-testing bar, not 2.0), and
      * the real |IR| exceeds the shuffle null by >= min_abs_t sigmas.

    Deterministic given ``seed``. Returns a dict (real_ir, ic_t, n_bars, n_shuffles,
    null_abs_ir_mean, null_abs_ir_std, z_vs_null, passes).
    """
    from core.alpha_zoo.screen import cross_sectional_ic, ir

    real_ic = cross_sectional_ic(signal, fwd, min_width=min_width)
    real_ir = ir(real_ic)
    n_bars = int(real_ic.notna().sum())
    ic_t = abs(real_ir) * np.sqrt(max(n_bars, 1))

    rng = np.random.default_rng(seed)
    f = fwd.to_numpy(dtype=float)
    idx, cols = fwd.index, fwd.columns
    null = np.empty(int(n_shuffles), dtype=float)
    for k in range(int(n_shuffles)):
        sh = f.copy()
        for r in range(sh.shape[0]):
            row = sh[r]
            fin = np.where(np.isfinite(row))[0]
            if fin.size > 1:
                row[fin] = row[rng.permutation(fin)]
        null[k] = ir(cross_sectional_ic(
            signal, pd.DataFrame(sh, index=idx, columns=cols), min_width=min_width))

    abs_null = np.abs(null)
    null_mu = float(np.nanmean(abs_null)) if abs_null.size else 0.0
    null_sd = float(np.nanstd(abs_null)) if abs_null.size else 0.0
    z_vs_null = float((abs(real_ir) - null_mu) / (null_sd + 1e-12))
    passes = bool(ic_t >= min_abs_t and z_vs_null >= min_abs_t)
    return {
        "real_ir": float(real_ir),
        "ic_t": float(ic_t),
        "n_bars": n_bars,
        "n_shuffles": int(n_shuffles),
        "null_abs_ir_mean": null_mu,
        "null_abs_ir_std": null_sd,
        "z_vs_null": z_vs_null,
        "passes": passes,
    }
