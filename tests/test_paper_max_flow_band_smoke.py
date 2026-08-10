"""Smoke: PAPER + MAX_FLOW_BAND banner knobs resolve without live orders."""

from __future__ import annotations

import importlib

import pytest


def test_paper_max_flow_band_knobs_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATING_MODE", "PAPER")
    monkeypatch.setenv("PAPER_TRADING_PROFILE", "MAX_FLOW_BAND")
    monkeypatch.setenv("SCALP_TIER_ENABLED", "false")
    monkeypatch.setenv("MCP_DIRECTIONAL_ECONOMIC_GATE_MODE", "paper_fallback")
    monkeypatch.setenv("CONTROLLED_LIVE_ENABLED", "false")

    import config

    importlib.reload(config)

    assert config.OPERATING_MODE == "PAPER"
    assert str(getattr(config, "PAPER_TRADING_PROFILE", "")).upper() == "MAX_FLOW_BAND"
    assert config.SCALP_TIER_ENABLED is False
    assert config.DRY_RUN is True
    gate = getattr(config, "MCP_DIRECTIONAL_ECONOMIC_GATE", {})
    assert gate.get("mode") == "paper_fallback"

    from core.engine.helpers import _boot_profile_log_lines

    lines = _boot_profile_log_lines()
    assert isinstance(lines, list) and lines
    text = "\n".join(str(x) for x in lines)
    assert "Profile" in text or "MAX_FLOW" in text or "EconGate" in text or "EntryFloor" in text
