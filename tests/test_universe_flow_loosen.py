"""Universe Flow Loosen V1 — thresholds flip when the config flag is on."""
from __future__ import annotations

import config
from core.pair_discovery import UniverseFilter


def test_universe_flow_loosen_defaults_off_use_baseline(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_FLOW_LOOSEN", {"enabled": False}, raising=False)
    monkeypatch.setattr(config, "MIN_TREND_EFFICIENCY", 0.20, raising=False)
    uf = UniverseFilter()
    assert uf.flow_loosen_v1 is False
    assert uf.MAX_SPREAD_PCT == 0.005
    assert uf.MIN_DEPTH_USD == 2000
    assert uf.MIN_RANGE_OF_CHANGE == 0.02
    assert uf.MIN_TREND_EFFICIENCY == 0.20


def test_universe_flow_loosen_applies_candidate_knobs(monkeypatch):
    monkeypatch.setattr(
        config,
        "UNIVERSE_FLOW_LOOSEN",
        {
            "enabled": True,
            "max_spread_pct": 0.0075,
            "min_depth_usd": 1200.0,
            "min_range_of_change": 0.015,
            "min_trend_efficiency": 0.12,
        },
        raising=False,
    )
    uf = UniverseFilter()
    assert uf.flow_loosen_v1 is True
    assert uf.MAX_SPREAD_PCT == 0.0075
    assert uf.MIN_DEPTH_USD == 1200.0
    assert uf.MIN_RANGE_OF_CHANGE == 0.015
    assert uf.MIN_TREND_EFFICIENCY == 0.12
