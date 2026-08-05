"""Tests for max_hold_force_flat helper (blueprint Phase 1)."""
from __future__ import annotations

from types import SimpleNamespace

from core.order_mgmt.helpers import max_hold_force_flat_hours


def test_band_position_uses_accuracy_max_hold(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "ACCURACY_TARGET_MODE",
        {"enabled": True, "max_hold_hours": 72},
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "TIER_GEOMETRY_TIME_EXIT_HOLD",
        {"enabled": False, "max_hold_hours": 48, "min_planned_rr": 1.0},
        raising=False,
    )
    pos = SimpleNamespace(
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=100.4,  # TP frac < SL frac → band
        side="buy",
        _scalp=False,
        strategy="mcp",
    )
    assert max_hold_force_flat_hours(pos, standard_max_age_h=4.0) == 72.0


def test_standard_position_uses_risk_max_age(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "ACCURACY_TARGET_MODE",
        {"enabled": False, "max_hold_hours": 72},
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "TIER_GEOMETRY_TIME_EXIT_HOLD",
        {"enabled": False, "max_hold_hours": 72, "min_planned_rr": 1.0},
        raising=False,
    )
    pos = SimpleNamespace(
        entry_price=100.0,
        stop_loss=97.0,
        take_profit=106.0,  # wide TP → not band
        side="buy",
        _scalp=False,
        strategy="mcp",
    )
    assert max_hold_force_flat_hours(pos, standard_max_age_h=4.0) == 4.0
