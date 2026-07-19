"""T2 — SL_COOLDOWN_ENABLED env flag (max-flow band engine, 2026-07-19).

Phase 29 post-SL cooldown/StoplossGuard stays default-ON. The owner-approved
max-flow profile disables it via SL_COOLDOWN_ENABLED=false: is_sl_cooldown_active
then returns (False, "sl_cooldown_disabled_by_profile") without touching the
ledger, so re-enabling the flag restores full protection from persisted state.
"""
from __future__ import annotations

import time as _t
from pathlib import Path

import config


def _fresh_risk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    from core.risk_manager import RiskManager
    return RiskManager()


def test_config_default_is_enabled():
    import os
    if os.getenv("SL_COOLDOWN_ENABLED") is None:
        assert config.SL_COOLDOWN_ENABLED is True


def test_flag_on_keeps_cooldown_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SL_COOLDOWN_ENABLED", True, raising=False)
    r = _fresh_risk(tmp_path, monkeypatch)
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is True
    assert "post_sl_cooldown" in reason


def test_flag_off_disables_both_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SL_COOLDOWN_ENABLED", False, raising=False)
    r = _fresh_risk(tmp_path, monkeypatch)
    # Layer 1 (fresh SL) AND layer 2 (2+ in lookback) would both fire.
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    r._recent_sl_by_pair_side["BTC/USDT|buy"].append(_t.time() - 3600)
    active, reason = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is False
    assert reason == "sl_cooldown_disabled_by_profile"


def test_flag_off_does_not_erase_ledger(tmp_path, monkeypatch):
    """Disabling must not mutate state: re-enabling restores protection."""
    monkeypatch.setattr(config, "SL_COOLDOWN_ENABLED", False, raising=False)
    r = _fresh_risk(tmp_path, monkeypatch)
    r.note_sl_hit("BTC/USDT:USDT", "buy")
    r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert len(r._recent_sl_by_pair_side.get("BTC/USDT|buy", [])) == 1
    monkeypatch.setattr(config, "SL_COOLDOWN_ENABLED", True, raising=False)
    active, _ = r.is_sl_cooldown_active("BTC/USDT:USDT", "buy")
    assert active is True
