"""F3 (2026-07-20 deep audit) — MCP_ENTRY_MIN_SCORE + SL_COOLDOWN_ENABLED
must be profile-gated like their sibling geometry knob (T3).

Verified finding: both were plain env reads with NO mode/profile gate, so a
stale env knob could lower the entry floor or disable the post-SL cooldown in
ANY mode/profile, while the sibling ACCURACY_TARGET_MODE geometry IS gated
PAPER + MAX_FLOW_BAND (_accuracy_geometry_enabled). Fix under test: both
knobs deviate from the legacy defaults (floor 66/65, cooldown enabled) ONLY
when OPERATING_MODE=PAPER AND PAPER_TRADING_PROFILE=MAX_FLOW_BAND.
"""
from __future__ import annotations

from pathlib import Path

import config


def test_gate_helper_requires_paper_and_max_flow_band():
    g = config._max_flow_research_knob_enabled
    assert g("PAPER", "MAX_FLOW_BAND") is True
    assert g("PAPER", "STANDARD") is False
    assert g("PAPER", "AGGRESSIVE_RESEARCH") is False
    # config raises earlier for non-PAPER + non-STANDARD profiles, but the
    # gate must hold on its own regardless (defense in depth).
    assert g("CONTROLLED_LIVE", "MAX_FLOW_BAND") is False
    assert g("OBSERVATION", "MAX_FLOW_BAND") is False


def test_entry_floor_only_deviates_under_gated_profile():
    f = config._profile_gated_entry_floor
    assert f("50", "PAPER", "MAX_FLOW_BAND") == 50.0
    # legacy 66/65 behavior (None) everywhere else, even with the env set
    assert f("50", "PAPER", "STANDARD") is None
    assert f("50", "CONTROLLED_LIVE", "MAX_FLOW_BAND") is None
    assert f("50", "OBSERVATION", "STANDARD") is None
    # unset/blank env stays None under the gated profile too
    assert f("", "PAPER", "MAX_FLOW_BAND") is None
    assert f("  ", "PAPER", "MAX_FLOW_BAND") is None


def test_sl_cooldown_only_disables_under_gated_profile():
    g = config._profile_gated_sl_cooldown
    assert g("false", "PAPER", "MAX_FLOW_BAND") is False
    # legacy default (enabled) everywhere else, even with the env set
    assert g("false", "PAPER", "STANDARD") is True
    assert g("false", "CONTROLLED_LIVE", "MAX_FLOW_BAND") is True
    assert g("false", "OBSERVATION", "STANDARD") is True
    assert g("true", "PAPER", "MAX_FLOW_BAND") is True
    assert g("", "PAPER", "MAX_FLOW_BAND") is True  # blank -> legacy enabled


def test_module_values_obey_the_gate_in_this_environment():
    """Whatever env this suite runs under, the module-level values must obey
    the gate: outside PAPER+MAX_FLOW_BAND the legacy defaults are mandatory."""
    gated = config._max_flow_research_knob_enabled(
        config.OPERATING_MODE, config.PAPER_TRADING_PROFILE
    )
    if not gated:
        assert config.MCP_ENTRY_MIN_SCORE is None
        assert config.SL_COOLDOWN_ENABLED is True


def test_source_pin_knobs_route_through_gated_helpers():
    """Both knob assignments must call the gated helpers so a future edit
    cannot silently revert to plain env reads."""
    src = Path("config/gates.py").read_text(encoding="utf-8")
    assert src.count("_profile_gated_entry_floor(") >= 2, "def + assignment"
    assert src.count("_profile_gated_sl_cooldown(") >= 2, "def + assignment"
    assert 'MCP_ENTRY_MIN_SCORE = float(_MCP_ENTRY_MIN_SCORE_RAW)' not in src
    assert ('SL_COOLDOWN_ENABLED = os.getenv("SL_COOLDOWN_ENABLED", "true")'
            '.lower() == "true"') not in src
