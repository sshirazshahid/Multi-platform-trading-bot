"""T1 — MCP_ENTRY_MIN_SCORE env knob (max-flow band engine, 2026-07-19).

The mcp_brain algorithmic fallback gate uses hardcoded score floors:
66 (standard path, layers_ok >= 6) and SCALP_MODE.entry_threshold
(default 65, layers_ok >= 4). The owner-approved max-flow profile lowers
the floor to 50 via env. Unset env -> exactly today's 66/65 behavior.

HONESTY: lowering the floor admits lower-conviction entries; expectancy
is expected <= historical (~ -0.24R) and is reported, not hidden. The
layers_ok gates are NOT modified by this knob.
"""
from __future__ import annotations

import os
from pathlib import Path

import config
import core.mcp_brain as mb


def test_config_default_is_none_when_env_unset():
    if os.getenv("MCP_ENTRY_MIN_SCORE") is None:
        assert config.MCP_ENTRY_MIN_SCORE is None


def test_default_floors_unchanged(monkeypatch):
    monkeypatch.setattr(config, "MCP_ENTRY_MIN_SCORE", None, raising=False)
    assert mb._entry_score_floor(False) == 66
    assert mb._entry_score_floor(True, {"entry_threshold": 65}) == 65
    # SCALP_MODE without an explicit threshold falls back to 65
    assert mb._entry_score_floor(True, {}) == 65
    assert mb._entry_score_floor(True, None) == 65


def test_env_50_admits_score_55_that_default_rejects(monkeypatch):
    score, layers_ok = 55, 6
    # Default: 55 < 66 -> rejected
    monkeypatch.setattr(config, "MCP_ENTRY_MIN_SCORE", None, raising=False)
    assert not (score >= mb._entry_score_floor(False) and layers_ok >= 6)
    # env=50: 55 >= 50 -> admitted
    monkeypatch.setattr(config, "MCP_ENTRY_MIN_SCORE", 50.0, raising=False)
    assert score >= mb._entry_score_floor(False) and layers_ok >= 6
    # scalp floor overridden too
    assert mb._entry_score_floor(True, {"entry_threshold": 65}) == 50.0


def test_gate_sites_use_the_floor_helper():
    """Source pin: both rule_gate comparisons must route through the helper
    so the env knob cannot silently miss one path (repo scan-test style)."""
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert 'result["score"] >= 66' not in src, "standard gate must use _entry_score_floor"
    assert '_SM_gate.get("entry_threshold", 65) and result["layers_ok"]' not in src, (
        "scalp gate must use _entry_score_floor"
    )
    assert src.count("_entry_score_floor(") >= 3, "helper def + both gate sites"
