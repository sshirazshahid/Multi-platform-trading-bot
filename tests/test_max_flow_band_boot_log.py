"""T5 — in-process boot log lines for the max-flow band profile (2026-07-19).

Restart verification rule: the NEW boot log must print the entry-score
threshold, SL-cooldown state, accuracy-band geometry state, and the paper
profile IN-PROCESS (no subprocess re-parse). BotEngine.__init__ emits one
line per knob via _boot_profile_log_lines().
"""
from __future__ import annotations

from pathlib import Path

import config


def _lines(monkeypatch, **overrides):
    defaults = {
        "PAPER_TRADING_PROFILE": "STANDARD",
        "PAPER_PROFILE_STARTED_AT": "",
        "MCP_ENTRY_MIN_SCORE": None,
        "SL_COOLDOWN_ENABLED": True,
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
        PAPER_TRADING_PROFILE="MAX_FLOW_BAND",
        PAPER_PROFILE_STARTED_AT="1789000000.0",
        MCP_ENTRY_MIN_SCORE=50.0,
        SL_COOLDOWN_ENABLED=False,
        ACCURACY_TARGET_MODE={
            "enabled": True, "tp_frac_of_sl": 0.5, "min_tp_pct": 0.5,
            "tp_frac_buy": 0.35, "tp_frac_sell": 0.30,
        },
    )
    assert "Profile   : MAX_FLOW_BAND" in text
    assert "epoch=1789000000.0" in text
    assert "EntryFloor: MCP_ENTRY_MIN_SCORE=50" in text
    assert "SLCooldown: DISABLED" in text
    assert "AccBand   : ON" in text
    assert "buy=0.35" in text and "sell=0.3" in text


def test_boot_lines_defaults_describe_todays_behavior(monkeypatch):
    text = _lines(monkeypatch)
    assert "Profile   : STANDARD" in text
    assert "EntryFloor: MCP_ENTRY_MIN_SCORE=default(66/65)" in text
    assert "SLCooldown: enabled" in text
    assert "AccBand   : OFF" in text


def test_engine_init_emits_boot_lines():
    """Source pin: __init__ must log the lines so every boot is verifiable
    from the log alone (in-process verification rule)."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("class BotEngine")
    init_block = src[src.index("def __init__", i):]
    init_block = init_block[: init_block.index("def ", 10)]
    assert "_boot_profile_log_lines()" in init_block
