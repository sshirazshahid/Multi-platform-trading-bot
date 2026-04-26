"""Tests for core.decay_detector — concept drift + performance decay."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.decay_detector import (
    DriftDetector,
    PerformanceDecayDetector,
    is_quarantined,
    quarantine,
)


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------


def test_drift_detector_stationary_no_alarm():
    """Both windows from N(0, 1) — alarm must NEVER fire."""
    rng = np.random.default_rng(42)
    det = DriftDetector(p_threshold=0.01, sustain_seconds=6 * 3600)
    ts = 0.0
    fired = False
    for _ in range(1000):
        ref = rng.standard_normal(500)
        recent = rng.standard_normal(200)
        if det.update(ts=ts, feature_name="f", recent_values=recent, ref_values=ref):
            fired = True
            break
        ts += 60.0  # advance 1 minute per call
    assert not fired, "DriftDetector should not fire on stationary noise"


def test_drift_detector_injected_shift_fires_after_sustain():
    """Inject a +1σ mean shift and keep it; alarm must fire after sustain window."""
    rng = np.random.default_rng(7)
    det = DriftDetector(p_threshold=0.01, sustain_seconds=6 * 3600)
    ref = rng.standard_normal(2000)
    sustain = 6 * 3600
    # Drive enough samples + enough wall-clock past first breach.
    fired_at = None
    ts = 0.0
    # Each step: large shifted recent window. ts advances by 30 min.
    for _ in range(50):
        recent = rng.standard_normal(500) + 1.0  # +1σ shift
        if det.update(ts=ts, feature_name="f", recent_values=recent, ref_values=ref):
            fired_at = ts
            break
        ts += 1800.0  # 30 minutes
    assert fired_at is not None, "Alarm must fire under sustained +1σ shift"
    first = det.first_breach_ts("f")
    assert first is not None
    assert (fired_at - first) >= sustain


def test_drift_detector_transient_shift_no_fire():
    """Shift for less than sustain_seconds, then revert. Must NOT fire."""
    rng = np.random.default_rng(123)
    det = DriftDetector(p_threshold=0.01, sustain_seconds=6 * 3600)
    ref = rng.standard_normal(2000)

    ts = 0.0
    fired = False

    # Phase 1: shifted but for only ~2 hours (well under 6h sustain).
    # 4 calls × 30 min = 2h.
    for _ in range(4):
        recent = rng.standard_normal(500) + 1.0
        if det.update(ts=ts, feature_name="f", recent_values=recent, ref_values=ref):
            fired = True
        ts += 1800.0

    assert not fired, "Should not fire before sustain window elapses"
    # First breach should currently be set.
    assert det.first_breach_ts("f") is not None

    # Phase 2: revert to in-distribution. One healthy update must reset breach.
    for _ in range(3):
        recent = rng.standard_normal(500)
        out = det.update(ts=ts, feature_name="f", recent_values=recent, ref_values=ref)
        assert out is False
        ts += 1800.0

    assert det.first_breach_ts("f") is None, "Return to OK must reset first_breach_ts"

    # Phase 3: brief shift again, but only one update, well under sustain.
    recent = rng.standard_normal(500) + 1.0
    fired = det.update(ts=ts, feature_name="f", recent_values=recent, ref_values=ref)
    assert not fired, "First breach after reset must not immediately fire"


# ---------------------------------------------------------------------------
# PerformanceDecayDetector
# ---------------------------------------------------------------------------


def test_perf_decay_at_expectation_no_alarm():
    """Returns generated to match expected Sharpe -> z near 0, no alarm."""
    rng = np.random.default_rng(11)
    expected_sr = 0.10
    det = PerformanceDecayDetector(
        expected_sharpe=expected_sr,
        expected_sharpe_std=0.05,
        sustain_seconds=24 * 3600,
        z_threshold=-1.5,
    )
    ts = 0.0
    fired = False
    for _ in range(50):
        # Returns with mean ≈ 0.10 * std => Sharpe ≈ 0.10
        rets = rng.standard_normal(30) * 1.0 + expected_sr
        if det.update(ts=ts, recent_returns=rets):
            fired = True
            break
        ts += 3600.0  # 1 hour per update
    assert not fired


def test_perf_decay_sustained_drop_fires():
    """Sustained low Sharpe (z <= -1.5) for > 24h must fire."""
    rng = np.random.default_rng(22)
    expected_sr = 0.20
    det = PerformanceDecayDetector(
        expected_sharpe=expected_sr,
        expected_sharpe_std=0.05,
        sustain_seconds=24 * 3600,
        z_threshold=-1.5,
    )
    sustain = 24 * 3600
    # Generate returns with mean strongly negative so sample Sharpe is well below
    # expected even under noise: rets = -0.5 + N(0,1) -> Sharpe ~ -0.5,
    # z = (-0.5 - 0.2) / 0.05 = -14.
    ts = 0.0
    fired_at = None
    for _ in range(60):
        rets = rng.standard_normal(30) * 1.0 - 0.5
        if det.update(ts=ts, recent_returns=rets):
            fired_at = ts
            break
        ts += 3600.0  # 1h per call

    assert fired_at is not None, "Sustained decay should fire"
    assert det.first_breach_ts is not None
    assert (fired_at - det.first_breach_ts) >= sustain


def test_perf_decay_transient_drop_no_fire():
    """Transient decay then recovery — alarm must NOT fire."""
    rng = np.random.default_rng(33)
    expected_sr = 0.20
    det = PerformanceDecayDetector(
        expected_sharpe=expected_sr,
        expected_sharpe_std=0.05,
        sustain_seconds=24 * 3600,
        z_threshold=-1.5,
    )
    ts = 0.0

    # Phase 1: decay for 6 hours (well under 24h).
    for _ in range(6):
        rets = rng.standard_normal(30) * 1.0 - 0.5  # Sharpe ~ -0.5 -> z=-14
        out = det.update(ts=ts, recent_returns=rets)
        assert out is False
        ts += 3600.0
    assert det.first_breach_ts is not None

    # Phase 2: recovery — Sharpe well above z_threshold cutoff.
    # z_threshold=-1.5, expected_std=0.05 -> need sharpe > 0.125.
    # Bias mean strongly positive so noise can't pull below.
    for _ in range(3):
        rets = rng.standard_normal(30) * 1.0 + 1.0  # Sharpe ~ 1, z = +16
        out = det.update(ts=ts, recent_returns=rets)
        assert out is False
        ts += 3600.0

    assert det.first_breach_ts is None


# ---------------------------------------------------------------------------
# Quarantine helpers
# ---------------------------------------------------------------------------


def test_quarantine_writes_record(tmp_path: Path):
    p = tmp_path / "quarantine.json"
    quarantine("supertrend_futures", "drift:p<0.01", until_ts=10_000.0, path=p)

    assert p.exists()
    with p.open() as f:
        data = json.load(f)
    assert "supertrend_futures" in data
    rec = data["supertrend_futures"]
    assert rec["strategy"] == "supertrend_futures"
    assert rec["reason"] == "drift:p<0.01"
    assert rec["until_ts"] == pytest.approx(10_000.0)


def test_is_quarantined_before_and_after_until_ts(tmp_path: Path):
    p = tmp_path / "quarantine.json"
    quarantine("strat_a", "decay", until_ts=1_000.0, path=p)
    assert is_quarantined("strat_a", now_ts=500.0, path=p) is True
    assert is_quarantined("strat_a", now_ts=1_000.0, path=p) is False
    assert is_quarantined("strat_a", now_ts=2_000.0, path=p) is False
    # Unknown strategy never quarantined.
    assert is_quarantined("never_seen", now_ts=0.0, path=p) is False


def test_quarantine_idempotent_on_strategy(tmp_path: Path):
    p = tmp_path / "quarantine.json"
    quarantine("strat_a", "drift", until_ts=1_000.0, path=p)
    quarantine("strat_a", "drift-renewed", until_ts=2_000.0, path=p)
    quarantine("strat_b", "decay", until_ts=1_500.0, path=p)

    with p.open() as f:
        data = json.load(f)
    assert set(data.keys()) == {"strat_a", "strat_b"}, "No duplicate keys per strategy"
    assert data["strat_a"]["until_ts"] == pytest.approx(2_000.0)
    assert data["strat_a"]["reason"] == "drift-renewed"


def test_is_quarantined_missing_file(tmp_path: Path):
    p = tmp_path / "does_not_exist.json"
    assert is_quarantined("anything", now_ts=0.0, path=p) is False


def test_quarantine_creates_parent_dir(tmp_path: Path):
    p = tmp_path / "nested" / "dir" / "quarantine.json"
    quarantine("strat", "reason", until_ts=42.0, path=p)
    assert p.exists()
