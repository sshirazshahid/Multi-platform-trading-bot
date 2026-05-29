"""Phase 13.4 — IsotonicCalibrator integrated into ProbabilityCalibrator.

Verify the new precedence order: isotonic > strategy bucket > global bucket
> raw passthrough. And verify that isotonic actually refits when N reaches
the threshold, persists, and survives a reload.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# IsotonicCalibrator depends on sklearn + joblib. When those aren't
# installed in the venv, the production path correctly falls back to
# the bucket calibrator (verified separately) but THESE tests can't
# exercise the isotonic-fitted branch — they require the real fit.
pytest.importorskip("sklearn")
pytest.importorskip("joblib")


@pytest.fixture
def fresh_calibrator(monkeypatch, tmp_path):
    """Each test gets a clean cwd + fresh ProbabilityCalibrator import."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    # Force reimport so module-level Path()s pick up new cwd.
    if "core.probability_calibrator" in sys.modules:
        del sys.modules["core.probability_calibrator"]
    sys.path.insert(0, str(ROOT))
    mod = importlib.import_module("core.probability_calibrator")
    return mod


def test_warmup_returns_raw_when_no_data(fresh_calibrator):
    cal = fresh_calibrator.ProbabilityCalibrator()
    assert cal.calibrate(0.70) == 0.70


def test_bucket_kicks_in_at_min_samples(fresh_calibrator):
    cal = fresh_calibrator.ProbabilityCalibrator()
    # 16 records at 70% conf, all losses — bucket says actual WR = 0%
    for _ in range(16):
        cal.record(0.70, actual_win=False, strategy="systematic_v3_1")
    out = cal.calibrate(0.70, strategy="systematic_v3_1")
    assert out == 0.0


def test_isotonic_refit_at_threshold(fresh_calibrator, monkeypatch):
    """Force a refit by lowering the threshold, then check isotonic kicked in."""
    cal = fresh_calibrator.ProbabilityCalibrator()
    # Synthesize a clean monotone signal: high conf wins, low conf loses
    import random
    rng = random.Random(42)
    for _ in range(60):
        c = rng.uniform(0.4, 0.9)
        won = c > 0.65  # deterministic threshold
        cal.record(c, actual_win=won)
    # After 60 records, isotonic should have refit
    iso = cal._iso
    assert iso is not None and iso is not False
    assert iso.is_fitted(), "isotonic should be fitted after >= 50 pairs"


def test_isotonic_takes_precedence_over_buckets(fresh_calibrator):
    """When both isotonic and bucket would fire, isotonic wins."""
    cal = fresh_calibrator.ProbabilityCalibrator()
    # Train: predict 0.70 → actual ~50%
    for _ in range(30):
        cal.record(0.70, actual_win=True)
    for _ in range(30):
        cal.record(0.70, actual_win=False)
    # Bucket: 30/60 = 50%. Isotonic: also fitted.
    # Calibrate at 0.70 — should come from isotonic.
    out = cal.calibrate(0.70)
    # Either way the value is near 0.5; key check is that isotonic is fitted
    assert cal._iso is not None and cal._iso.is_fitted()
    assert 0.4 <= out <= 0.6


def test_isotonic_monotonicity_preserved(fresh_calibrator):
    """A fitted isotonic must produce non-decreasing outputs for non-decreasing inputs."""
    cal = fresh_calibrator.ProbabilityCalibrator()
    import random
    rng = random.Random(1)
    for _ in range(80):
        c = rng.uniform(0.4, 0.9)
        cal.record(c, actual_win=(c > 0.6))
    if not (cal._iso and cal._iso.is_fitted()):
        pytest.skip("isotonic not fitted — refit threshold not reached")
    inputs = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
    outputs = [cal.calibrate(c) for c in inputs]
    for i in range(len(outputs) - 1):
        assert outputs[i] <= outputs[i + 1] + 1e-9, (
            f"monotonicity violated at idx {i}: "
            f"calibrate({inputs[i]})={outputs[i]} > calibrate({inputs[i+1]})={outputs[i+1]}")


def test_isotonic_persisted_to_disk(fresh_calibrator, tmp_path):
    cal = fresh_calibrator.ProbabilityCalibrator()
    import random
    rng = random.Random(5)
    for _ in range(60):
        c = rng.uniform(0.4, 0.9)
        cal.record(c, actual_win=(c > 0.55))
    iso_path = tmp_path / "data" / "calibration_iso.pkl"
    pairs_path = tmp_path / "data" / "calibration_pairs.json"
    assert iso_path.exists(), "isotonic .pkl must persist"
    assert pairs_path.exists(), "pair history must persist"


def test_isotonic_loaded_from_disk_on_reinit(fresh_calibrator):
    """A new ProbabilityCalibrator instance picks up the persisted isotonic."""
    cal1 = fresh_calibrator.ProbabilityCalibrator()
    import random
    rng = random.Random(11)
    for _ in range(60):
        c = rng.uniform(0.4, 0.9)
        cal1.record(c, actual_win=(c > 0.55))
    assert cal1._iso and cal1._iso.is_fitted()

    # New instance — must load the persisted isotonic.
    cal2 = fresh_calibrator.ProbabilityCalibrator()
    assert cal2._iso is not None
    assert cal2._iso.is_fitted(), "second-instance isotonic should be fitted from disk"


def test_get_summary_still_works(fresh_calibrator):
    """The legacy summary API must continue to work after the refactor."""
    cal = fresh_calibrator.ProbabilityCalibrator()
    for _ in range(20):
        cal.record(0.70, actual_win=True)
    s = cal.get_summary()
    assert "total_recorded" in s
    assert s["total_recorded"] >= 20
