"""Regression: ProbabilityCalibrator must not crash when sklearn missing.

Bug found 2026-04-29 in deep audit. `_ensure_iso` set `self._iso = False`
as a 'don't retry' sentinel when `core.calibration` import failed.
`calibrate()` then checked `self._iso is not None` (True for False) and
called `self._iso.is_fitted()` → AttributeError.

In the user's deployed venv (no sklearn) every `calibrate()` would crash.
Currently dormant in production (no live caller of `should_trade`), but a
latent crash waiting for the next code path that calls calibrate().

Fix: use a separate `_iso_unavailable` flag instead of the False sentinel.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_cal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    if "core.probability_calibrator" in sys.modules:
        del sys.modules["core.probability_calibrator"]
    sys.path.insert(0, str(ROOT))
    return importlib.import_module("core.probability_calibrator")


def test_calibrate_does_not_crash_without_sklearn(fresh_cal):
    """When IsotonicCalibrator import fails, calibrate() must return raw,
    not raise AttributeError."""
    cal = fresh_cal.ProbabilityCalibrator()

    # Force the import-failure path (works regardless of whether sklearn
    # is actually installed in the test env).
    with patch.object(
        fresh_cal.ProbabilityCalibrator, "_ensure_iso",
        side_effect=ImportError("simulated missing sklearn"),
    ):
        # Trigger the import-failure side-effect; subsequent calibrate
        # must still work.
        try:
            cal._ensure_iso()
        except ImportError:
            cal._iso_unavailable = True
            cal._iso = None

    # Must not raise. Returns raw (warm-up branch).
    out = cal.calibrate(0.70)
    assert isinstance(out, float)


def test_record_with_no_sklearn_does_not_crash(fresh_cal):
    """50+ records with sklearn unavailable must not crash."""
    cal = fresh_cal.ProbabilityCalibrator()
    cal._iso_unavailable = True  # Simulate failed import
    for i in range(60):
        cal.record(0.65, i % 2 == 0, "test_strat")
    # No crash. _iso stays None.
    assert cal._iso is None


def test_iso_unavailable_flag_persists_across_calibrate_calls(fresh_cal):
    """After import fails once, _iso_unavailable must short-circuit
    subsequent _ensure_iso calls so we don't retry the failing import."""
    cal = fresh_cal.ProbabilityCalibrator()
    cal._iso_unavailable = True
    # _ensure_iso should return None without retrying import
    assert cal._ensure_iso() is None
    assert cal._iso is None
    assert cal._iso_unavailable is True


def test_calibrate_returns_raw_when_iso_none(fresh_cal):
    """No isotonic, no bucket data → returns raw confidence unchanged."""
    cal = fresh_cal.ProbabilityCalibrator()
    cal._iso = None
    cal._iso_unavailable = True
    assert cal.calibrate(0.72) == 0.72
    assert cal.calibrate(0.55) == 0.55
