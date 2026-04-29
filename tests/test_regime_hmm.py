"""Tests for core/regime_hmm.py — Phase 2.2 HMM regime detector."""
from __future__ import annotations

from itertools import permutations
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("hmmlearn")
pytest.importorskip("joblib")

from core.regime_hmm import RegimeHMM


# --------------------------------------------------------------------- helpers


def _best_label_match(predicted: np.ndarray, truth: np.ndarray, n_states: int) -> float:
    """Return max accuracy across all label permutations.

    HMM state IDs are arbitrary — fitted state 0 may correspond to truth state 2.
    Try every permutation and return the best agreement.
    """
    predicted = np.asarray(predicted, dtype=int)
    truth = np.asarray(truth, dtype=int)
    best = 0.0
    for perm in permutations(range(n_states)):
        relabel = np.array(perm, dtype=int)
        remapped = relabel[predicted]
        acc = float((remapped == truth).mean())
        if acc > best:
            best = acc
    return best


def _generate_3state_series(
    n: int = 1500, seed: int = 7
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Synthesize a 3-state Markov-switching (returns, vol) series with known states.

    States:
      0 = chop      → mean ≈ 0,    vol ≈ 0.005
      1 = trend     → mean ≈ +0.01, vol ≈ 0.010
      2 = expansion → mean ≈ -0.005, vol ≈ 0.040

    Distinct mean+vol per state ⇒ a well-fit Gaussian HMM should recover states
    at well above chance (33%).
    """
    rng = np.random.default_rng(seed)
    # Very sticky transition matrix — each state persists with prob ~0.98.
    # Long dwell times help the HMM lock onto each cluster cleanly.
    trans = np.array(
        [
            [0.98, 0.01, 0.01],
            [0.01, 0.98, 0.01],
            [0.01, 0.01, 0.98],
        ]
    )
    # Well-separated state means/vols (~5+ sigma between cluster centers
    # in feature space) so a Gaussian HMM can reliably recover them.
    # Three well-separated clusters in (return, vol) space:
    #   chop      : ret≈0,    vol≈0.005
    #   trend     : ret≈+0.03 vol≈0.020
    #   expansion : ret≈-0.02 vol≈0.060
    means_ret = np.array([0.000, 0.030, -0.020])
    vols_ret = np.array([0.003, 0.006, 0.020])
    means_vol = np.array([0.005, 0.020, 0.060])
    vols_vol = np.array([0.0005, 0.001, 0.003])

    states = np.empty(n, dtype=int)
    returns = np.empty(n, dtype=float)
    vol = np.empty(n, dtype=float)

    s = 0
    for t in range(n):
        states[t] = s
        returns[t] = rng.normal(means_ret[s], vols_ret[s])
        vol[t] = abs(rng.normal(means_vol[s], vols_vol[s]))
        s = int(rng.choice(3, p=trans[s]))
    return returns, vol, states


# ---------------------------------------------------------------------- tests


def test_recovers_synthetic_3state_at_80pct():
    # n=3000 (≈4 months of hourly bars) gives the EM enough samples per
    # state to reliably converge on well-separated clusters with the
    # default random_state=42. Seeds 3/5/7/11/17/21 all clear ≥ 80%; we
    # pick one in the middle.
    returns, vol, truth = _generate_3state_series(n=3000, seed=7)
    hmm = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    pred = hmm.predict(returns, vol)
    acc = _best_label_match(pred, truth, n_states=3)
    assert acc >= 0.80, f"HMM accuracy {acc:.3f} below 0.80 threshold"


def test_predict_proba_rows_sum_to_one():
    returns, vol, _ = _generate_3state_series(n=600, seed=11)
    hmm = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    proba = hmm.predict_proba(returns, vol)
    assert proba.shape == (600, 3)
    row_sums = proba.sum(axis=1)
    assert np.allclose(row_sums, 1.0, atol=1e-6)
    assert (proba >= 0).all() and (proba <= 1).all()


def test_reproducibility_same_random_state():
    returns, vol, _ = _generate_3state_series(n=500, seed=21)
    a = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    b = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    np.testing.assert_array_equal(a.predict(returns, vol), b.predict(returns, vol))
    np.testing.assert_allclose(
        a.predict_proba(returns, vol), b.predict_proba(returns, vol), atol=1e-10
    )


def test_save_load_roundtrip(tmp_path: Path):
    returns, vol, _ = _generate_3state_series(n=400, seed=33)
    hmm = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    path = tmp_path / "hmm_test.pkl"
    hmm.save(path)
    loaded = RegimeHMM.load(path)

    np.testing.assert_array_equal(
        hmm.predict(returns, vol), loaded.predict(returns, vol)
    )
    np.testing.assert_allclose(
        hmm.predict_proba(returns, vol),
        loaded.predict_proba(returns, vol),
        atol=1e-10,
    )


def test_insufficient_data_raises():
    # n < n_states ⇒ cannot fit
    hmm = RegimeHMM(n_states=3, random_state=42)
    with pytest.raises(ValueError, match="(?i)insufficient|too few|n_samples"):
        hmm.fit(np.array([0.01, -0.02]), np.array([0.01, 0.02]))


def test_constant_vol_does_not_crash():
    """Zero-variance feature column should not blow up.

    Either the fit succeeds (e.g. by adding regularization) and predicts an
    array of correct length, OR a clear ValueError is raised. Silent NaN
    output / segfault would fail this test.
    """
    rng = np.random.default_rng(99)
    n = 400
    returns = rng.normal(0.0, 0.01, size=n)
    vol = np.full(n, 0.005)  # constant vol — degenerate
    hmm = RegimeHMM(n_states=3, random_state=42)
    try:
        hmm.fit(returns, vol)
        pred = hmm.predict(returns, vol)
        proba = hmm.predict_proba(returns, vol)
        assert pred.shape == (n,)
        assert proba.shape == (n, 3)
        assert np.isfinite(proba).all()
    except ValueError as e:
        # Acceptable: explicit, non-cryptic error.
        assert str(e), "ValueError must carry a message"


def test_mismatched_lengths_raise():
    hmm = RegimeHMM(n_states=3, random_state=42)
    with pytest.raises(ValueError, match="(?i)length|shape|same"):
        hmm.fit(np.zeros(100), np.zeros(99))


def test_predict_before_fit_raises():
    hmm = RegimeHMM(n_states=3, random_state=42)
    with pytest.raises(RuntimeError, match="(?i)fit"):
        hmm.predict(np.zeros(10), np.zeros(10))
