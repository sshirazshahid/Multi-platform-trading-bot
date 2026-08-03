"""T3 — ACCURACY_TARGET_MODE geometry re-enable, profile-gated (2026-07-19).

A parallel session hardcoded ACCURACY_TARGET_MODE["enabled"] = False so the
env toggle could not reactivate the hit-rate geometry. The owner explicitly
approved reversing that disable for the MAX_FLOW_BAND paper profile ONLY
(spec docs/superpowers/specs/2026-07-19-max-flow-band-engine-design.md).

The guard is NOT deleted — it is gated: "enabled" honors requested_enabled
solely when OPERATING_MODE=PAPER and PAPER_TRADING_PROFILE=MAX_FLOW_BAND.
Every other profile/mode keeps the parallel session's disable, so a stale
ACCURACY_TARGET_MODE=true in .env alone can never re-enable geometry, and
CONTROLLED_LIVE can never see it at all (stronger than the old guard).

HONESTY (binding): the WR band is delivered by geometry, not edge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import config
import core.mcp_brain as mb


def test_guard_helper_matrix():
    g = config._accuracy_geometry_enabled
    # ONLY the owner-approved combination reactivates the geometry.
    assert g(True, "PAPER", "MAX_FLOW_BAND") is True
    # The parallel session's disable stays in force everywhere else.
    assert g(True, "PAPER", "STANDARD") is False
    assert g(True, "PAPER", "AGGRESSIVE_RESEARCH") is False
    assert g(True, "CONTROLLED_LIVE", "MAX_FLOW_BAND") is False
    assert g(True, "OBSERVATION", "MAX_FLOW_BAND") is False
    assert g(False, "PAPER", "MAX_FLOW_BAND") is False


def test_config_dict_derivation_is_consistent():
    acc = config.ACCURACY_TARGET_MODE
    assert acc["enabled"] == config._accuracy_geometry_enabled(
        acc["requested_enabled"], config.OPERATING_MODE, config.PAPER_TRADING_PROFILE
    )
    # requested_enabled stays observable for tools/tests.
    assert "requested_enabled" in acc


def test_no_unconditional_hardcoded_disable():
    """Source pin: 'enabled' must be derived through the profile-gated helper,
    not a hardcoded literal (in either direction)."""
    src = Path("config.py").read_text(encoding="utf-8")
    block = src[src.index("ACCURACY_TARGET_MODE = {"):]
    block = block[: block.index("}")]
    assert '"enabled": _accuracy_geometry_enabled(' in block
    assert '"enabled": False' not in block
    assert '"enabled": True' not in block


def test_geometry_compresses_with_spec_fracs(monkeypatch):
    """Env true + fracs buy 0.35 / sell 0.30 (the .env cohort values):
    computed TP compresses vs SL per _apply_accuracy_target."""
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "tp_frac_buy": 0.35, "tp_frac_sell": 0.30},
        raising=False,
    )
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(0.70)
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(0.60)
    # Soft cost clearance: SL 1.0% x sell 0.30 = 0.30; default clearance
    # in fixture is unset → module default 0.35 lifts to 0.35.
    assert mb._apply_accuracy_target(1.0, 2.0, side="sell") == pytest.approx(0.35)


def test_geometry_off_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        config, "ACCURACY_TARGET_MODE",
        {"enabled": False, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
         "tp_frac_buy": 0.35, "tp_frac_sell": 0.30},
        raising=False,
    )
    assert mb._apply_accuracy_target(2.0, 4.0, side="buy") == pytest.approx(4.0)
    assert mb._apply_accuracy_target(2.0, 4.0, side="sell") == pytest.approx(4.0)
