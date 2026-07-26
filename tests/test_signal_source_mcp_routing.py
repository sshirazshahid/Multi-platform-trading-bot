"""SIGNAL_SOURCE=mcp must route to MCPStrategyScorer path only (harden 2026-07-24)."""
from __future__ import annotations

from pathlib import Path


def test_portfolio_cycle_routes_mcp_to_mcp_det_signal():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    # Binding seam: mcp and mcp_det share _mcp_det_signal; others do not.
    assert 'elif SIGNAL_SOURCE in ("mcp", "mcp_det"):' in src
    assert "_signal = self._mcp_det_signal()" in src
    # Ensure tsmom/machine/s3 are separate branches (no silent fallback into mcp).
    assert 'if SIGNAL_SOURCE == "tsmom":' in src
    assert 'elif SIGNAL_SOURCE == "machine":' in src
    assert 'elif SIGNAL_SOURCE == "s3":' in src


def test_env_example_defaults_to_mcp():
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "SIGNAL_SOURCE=mcp" in text


def test_config_accepts_mcp_signal_source():
    import config

    assert config.SIGNAL_SOURCE in (
        "mcp",
        "mcp_det",
        "tsmom",
        "machine",
        "s3",
        "none",
    )
