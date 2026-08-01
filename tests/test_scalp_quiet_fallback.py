"""Regression: quiet/ranging scalp veto must not starve AccBand PAPER.

Defect (2026-08-01): with SCALP_MODE enabled, ATR&lt;min_atr produced
scalp_veto:quiet on essentially every crypto candidate → 0 ALLOW / 0 OPEN
while the bot looked healthy. Ops workaround: SCALP_MODE_ENABLED=false.
Code fix: under PAPER+MAX_FLOW_BAND, fall through to standard _score_coin
when scalp returns quiet/ranging veto (regime switch, not ATR loosen).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.mcp_brain import MCPBrain


def _make_brain() -> MCPBrain:
    brain = MCPBrain.__new__(MCPBrain)
    brain._data_coordinator = None
    brain.score_via_model = MagicMock(
        return_value={
            "p_win_lr": 0.5,
            "p_win_gbm": 0.5,
            "p_win_ensemble": 0.55,
            "model_version": None,
        }
    )
    return brain


def test_quiet_scalp_falls_through_to_standard_under_max_flow_band():
    brain = _make_brain()
    quiet = {
        "score": 0,
        "layers_ok": 0,
        "side": "buy",
        "sl_pct": 0.8,
        "tp_pct": 1.3,
        "confidence": 0.0,
        "reason": "scalp_veto:quiet(atr=0.45%)",
        "_scalp": True,
    }
    standard = {
        "score": 68,
        "layers_ok": 6,
        "side": "buy",
        "sl_pct": 1.5,
        "tp_pct": 1.0,
        "confidence": 0.7,
        "reason": "4h_ema_gap | 1h_ema | rsi=52 | adx=24",
        "_scalp": False,
    }
    brain._score_coin_scalp = MagicMock(return_value=quiet)
    brain._score_coin = MagicMock(return_value=standard)

    with patch("config.SCALP_MODE", {"enabled": True, "min_atr_pct": 0.8}):
        with patch(
            "core.mcp_brain._max_flow_scalp_fallback_enabled",
            return_value=True,
        ):
            out = brain._route_score_coin("ADA", {}, {"1h": {}, "4h": {}})

    brain._score_coin_scalp.assert_called_once()
    brain._score_coin.assert_called_once()
    assert out["score"] == 68
    assert out.get("_scalp") is False
    assert "scalp_fallback" in str(out.get("reason") or "")
    assert "scalp_veto:quiet" in str(out.get("_scalp_fallback") or out.get("reason") or "")


def test_quiet_scalp_stays_veto_when_fallback_disabled():
    brain = _make_brain()
    quiet = {
        "score": 0,
        "layers_ok": 0,
        "side": "buy",
        "sl_pct": 0.8,
        "tp_pct": 1.3,
        "confidence": 0.0,
        "reason": "scalp_veto:quiet(atr=0.45%)",
        "_scalp": True,
    }
    brain._score_coin_scalp = MagicMock(return_value=quiet)
    brain._score_coin = MagicMock(return_value={"score": 70, "_scalp": False})

    with patch("config.SCALP_MODE", {"enabled": True}):
        with patch(
            "core.mcp_brain._max_flow_scalp_fallback_enabled",
            return_value=False,
        ):
            out = brain._route_score_coin("ADA", {}, {"1h": {}, "4h": {}})

    brain._score_coin.assert_not_called()
    assert out["score"] == 0
    assert "scalp_veto:quiet" in out["reason"]


def test_healthy_scalp_does_not_fall_through():
    brain = _make_brain()
    scalp_ok = {
        "score": 65,
        "layers_ok": 4,
        "side": "buy",
        "sl_pct": 0.8,
        "tp_pct": 1.3,
        "confidence": 0.6,
        "reason": "vwap_near=0.10% | rsi=50 | adx=25 | atr=1.20%",
        "_scalp": True,
    }
    brain._score_coin_scalp = MagicMock(return_value=scalp_ok)
    brain._score_coin = MagicMock()

    with patch("config.SCALP_MODE", {"enabled": True}):
        with patch(
            "core.mcp_brain._max_flow_scalp_fallback_enabled",
            return_value=True,
        ):
            out = brain._route_score_coin("ETH", {}, {"1h": {}, "4h": {}})

    brain._score_coin.assert_not_called()
    assert out is scalp_ok


def test_max_flow_scalp_fallback_gate_matches_profile():
    from core import mcp_brain as mb

    assert mb._max_flow_scalp_fallback_enabled(
        operating_mode="PAPER", paper_profile="MAX_FLOW_BAND"
    ) is True
    assert mb._max_flow_scalp_fallback_enabled(
        operating_mode="PAPER", paper_profile="STANDARD"
    ) is False
    assert mb._max_flow_scalp_fallback_enabled(
        operating_mode="OBSERVATION", paper_profile="MAX_FLOW_BAND"
    ) is False
