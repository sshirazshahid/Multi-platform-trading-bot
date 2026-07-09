"""Item 3 (data-integrity backlog 2026-07-07): spot_rotation_slice NaN factor.

A single leading-NaN / insufficient-history symbol (e.g. a late-listed coin whose
full-history EMA50 window still has leading NaNs) made `_zscore` return all-zeros,
zeroing the documented EMA50-slope rotation factor for EVERY symbol. The fix
neutralizes only the NaN symbol (per-symbol) and preserves the finite ones.
"""
from __future__ import annotations

import numpy as np

from scripts.spot_rotation_slice import _zscore, rank_scores


def test_zscore_neutralizes_nan_without_zeroing_others():
    z = _zscore(np.array([1.0, 2.0, 3.0, np.nan]))
    # NaN entry -> neutral 0.0 (the standardized mean); the finite ones keep
    # their real, non-degenerate z-scores (NOT all zeroed).
    assert z[3] == 0.0
    assert not np.allclose(z[:3], 0.0)
    finite = np.array([1.0, 2.0, 3.0])
    expected = (finite - finite.mean()) / finite.std(ddof=0)
    assert np.allclose(z[:3], expected)


def test_zscore_all_finite_path_unchanged():
    vals = np.array([1.0, 2.0, 3.0, 4.0])
    expected = (vals - vals.mean()) / vals.std(ddof=0)
    assert np.allclose(_zscore(vals), expected)


def test_zscore_all_nan_returns_zeros():
    z = _zscore(np.array([np.nan, np.nan]))
    assert np.allclose(z, 0.0)


def test_ema_slope_factor_survives_late_listed_symbol():
    """Reproduce the documented death: a late-listed symbol whose EMA50-history
    window has leading NaNs must not zero the slope factor for the others.

    The slope factor is `_zscore` of per-symbol EMA50 slopes. Build slopes where
    one is NaN; the OLD helper returned all-zeros (dead factor), the fixed one
    keeps the finite majors' spread while neutralizing the NaN symbol."""
    slopes = np.array([0.02, -0.01, 0.03, np.nan])
    z = _zscore(slopes)
    assert z[3] == 0.0                      # late-listed symbol -> neutral
    assert len({round(float(v), 9) for v in z[:3]}) == 3  # majors still ranked


def test_rank_scores_finite_and_ranked_with_leading_nan_symbol():
    """End-to-end: a symbol with leading NaNs in its EMA50 window yields finite
    scores for all and a strict strongest-first ordering (no NaN contamination)."""
    n = 120
    idx = np.arange(n)
    import pandas as pd
    closes = {}
    volumes = {}
    for k, s in enumerate(("A", "B", "C", "D")):
        base = 100.0 + 10 * k
        path = base + 0.4 * (k - 1) * idx + np.sin(idx / 7.0)
        closes[s] = pd.Series(np.abs(path) + 1.0)
        volumes[s] = pd.Series(np.full(n, 1_000_000.0 + 1e5 * k))
    closes = pd.DataFrame(closes)
    volumes = pd.DataFrame(volumes)
    # D lists late: leading NaNs only inside the EMA50-history window (< index 60)
    closes.loc[:59, "D"] = np.nan
    volumes.loc[:59, "D"] = np.nan

    scores = rank_scores(closes, volumes, 100, list(closes.columns))
    assert all(np.isfinite(v) for v in scores.values())
    vals = list(scores.values())
    assert vals == sorted(vals, reverse=True)
