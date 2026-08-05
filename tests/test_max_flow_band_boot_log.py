"""T5 — in-process boot log lines for the max-flow band profile (2026-07-19).

Restart verification rule: the NEW boot log must print the entry-score
threshold, SL-cooldown state, accuracy-band geometry state, and the paper
profile IN-PROCESS (no subprocess re-parse). BotEngine.__init__ emits one
line per knob via _boot_profile_log_lines().
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path

import config


def _lines(monkeypatch, **overrides):
    defaults = {
        "SIGNAL_SOURCE": "mcp",
        "PAPER_TRADING_PROFILE": "STANDARD",
        "PAPER_PROFILE_STARTED_AT": "",
        "MCP_ENTRY_MIN_SCORE": None,
        "SL_COOLDOWN_ENABLED": True,
        "BAND_REGIME_FILTER_ENABLED": False,
        "SMART_MONEY_ENTRY_GATE": {"enabled": False, "fail_open_stale": True},
        "ACCURACY_TARGET_MODE": {
            "enabled": False, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
            "tp_frac_buy": None, "tp_frac_sell": None,
        },
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(config, key, value, raising=False)
    from core.bot_engine import _boot_profile_log_lines
    return "\n".join(_boot_profile_log_lines())


def test_boot_lines_reflect_max_flow_band_profile(monkeypatch):
    text = _lines(
        monkeypatch,
        SIGNAL_SOURCE="mcp",
        PAPER_TRADING_PROFILE="MAX_FLOW_BAND",
        PAPER_PROFILE_STARTED_AT="1789000000.0",
        MCP_ENTRY_MIN_SCORE=50.0,
        SL_COOLDOWN_ENABLED=False,
        BAND_REGIME_FILTER_ENABLED=True,
        SMART_MONEY_ENTRY_GATE={"enabled": True, "fail_open_stale": True},
        ACCURACY_TARGET_MODE={
            "enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
            "tp_frac_buy": 0.35, "tp_frac_sell": 0.30,
        },
    )
    assert "SignalSrc : mcp" in text
    assert "Profile   : MAX_FLOW_BAND" in text
    assert "epoch=1789000000.0" in text
    assert "EntryFloor: MCP_ENTRY_MIN_SCORE=50" in text
    assert "SLCooldown: DISABLED" in text
    assert "AccBand   : ON" in text
    assert "buy=0.35" in text and "sell=0.3" in text
    assert "BandRegime: ON" in text
    assert "SmartMoney: ON" in text
    assert "AccBandNote:" in text
    assert "CONFIRMED_NO_GO" in text
    assert "not edge" in text


def test_boot_lines_defaults_describe_todays_behavior(monkeypatch):
    text = _lines(monkeypatch)
    assert "SignalSrc : mcp" in text
    assert "Profile   : STANDARD" in text
    assert "EntryFloor: MCP_ENTRY_MIN_SCORE=default(66/65)" in text
    assert "SLCooldown: enabled" in text
    assert "AccBand   : OFF" in text
    assert "BandRegime: OFF" in text
    assert "SmartMoney: OFF" in text
    assert "AccBandNote:" not in text


def test_boot_lines_signal_source_tsmom_visible(monkeypatch):
    text = _lines(monkeypatch, SIGNAL_SOURCE="tsmom")
    assert "SignalSrc : tsmom" in text


def test_engine_init_emits_boot_lines():
    """Source pin: __init__ must log the lines so every boot is verifiable
    from the log alone (in-process verification rule)."""
    src = bot_engine_source_for_grep()
    assert "_boot_profile_log_lines()" in src
