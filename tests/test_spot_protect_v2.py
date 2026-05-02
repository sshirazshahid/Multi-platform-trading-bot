"""SPOT-PROTECT-V2: model-driven early SCALE_OUT in core.spot_manager."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core.spot_manager import SpotPortfolioManager, HoldingInfo


class _FakeBrainPwin:
    """Minimal fake — score_via_model returns the pre-set p_win."""
    def __init__(self, p_win: float, model_version: str = "spot_test_v1"):
        self._p = p_win
        self._v = model_version

    def score_via_model(self, *, market_type, feats, rule_score):
        return {
            "p_win_lr":       self._p,
            "p_win_gbm":      self._p,
            "p_win_ensemble": self._p,
            "model_version":  self._v,
        }


def _make_holding(*, balance=10.0, current_price=80.0, peak_price=100.0,
                  avg_cost=90.0):
    """Holding 20% drawn down from peak (peak_price=100, current=80)."""
    return HoldingInfo(
        coin="BTC",
        exchange="binance",
        balance=balance,
        avg_cost=avg_cost,
        current_price=current_price,
        unrealized_pnl=balance * (current_price - avg_cost),
        unrealized_pnl_pct=(current_price - avg_cost) / avg_cost,
        peak_price=peak_price,
        last_updated=time.time(),
    )


def _make_mgr(brain, holding):
    mgr = SpotPortfolioManager(exchanges={}, risk_manager=None, mcp_brain=brain)
    mgr._holdings = {"binance": {"BTC": holding}}
    return mgr


def test_v2_scale_out_when_model_below_floor(monkeypatch):
    """20% drawdown + p_win=0.30 (below floor) → SCALE_OUT."""
    import config
    monkeypatch.setattr(config, "SPOT_STRATEGY",
                        {"enabled": True, "min_position_usd": 1.0,
                         "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40},
                        raising=False)
    monkeypatch.setattr(config, "SPOT_PROTECT_V2",
                        {"enabled": True, "drawdown_pct": 0.15, "p_win_floor": 0.40},
                        raising=False)
    mgr = _make_mgr(_FakeBrainPwin(p_win=0.30), _make_holding())
    out = mgr.evaluate_holding("BTC", "binance")
    assert out["action"] == "SCALE_OUT"
    assert "spot_v2_model" in out["reason"]


def test_v2_holds_when_model_above_floor(monkeypatch):
    """20% drawdown but p_win=0.55 (above floor) → HOLD."""
    import config
    monkeypatch.setattr(config, "SPOT_STRATEGY",
                        {"enabled": True, "min_position_usd": 1.0,
                         "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40},
                        raising=False)
    monkeypatch.setattr(config, "SPOT_PROTECT_V2",
                        {"enabled": True, "drawdown_pct": 0.15, "p_win_floor": 0.40},
                        raising=False)
    mgr = _make_mgr(_FakeBrainPwin(p_win=0.55), _make_holding())
    out = mgr.evaluate_holding("BTC", "binance")
    assert out["action"] == "HOLD"


def test_v2_no_op_below_drawdown_threshold(monkeypatch):
    """5% drawdown — too shallow for V2 even when model is bearish."""
    import config
    monkeypatch.setattr(config, "SPOT_STRATEGY",
                        {"enabled": True, "min_position_usd": 1.0,
                         "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40},
                        raising=False)
    monkeypatch.setattr(config, "SPOT_PROTECT_V2",
                        {"enabled": True, "drawdown_pct": 0.15, "p_win_floor": 0.40},
                        raising=False)
    mgr = _make_mgr(_FakeBrainPwin(p_win=0.20),
                    _make_holding(current_price=95.0, peak_price=100.0))
    out = mgr.evaluate_holding("BTC", "binance")
    assert out["action"] == "HOLD"


def test_v2_no_op_when_no_model_loaded(monkeypatch):
    """Spot ensemble not promoted (model_version=None) → HOLD."""
    import config
    monkeypatch.setattr(config, "SPOT_STRATEGY",
                        {"enabled": True, "min_position_usd": 1.0,
                         "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40},
                        raising=False)
    monkeypatch.setattr(config, "SPOT_PROTECT_V2",
                        {"enabled": True, "drawdown_pct": 0.15, "p_win_floor": 0.40},
                        raising=False)
    mgr = _make_mgr(_FakeBrainPwin(p_win=0.20, model_version=None),
                    _make_holding())
    out = mgr.evaluate_holding("BTC", "binance")
    assert out["action"] == "HOLD"


def test_v2_disabled_via_config(monkeypatch):
    """SPOT_PROTECT_V2.enabled=False bypasses the gate entirely."""
    import config
    monkeypatch.setattr(config, "SPOT_STRATEGY",
                        {"enabled": True, "min_position_usd": 1.0,
                         "drawdown_half_pct": 0.25, "drawdown_full_pct": 0.40},
                        raising=False)
    monkeypatch.setattr(config, "SPOT_PROTECT_V2",
                        {"enabled": False, "drawdown_pct": 0.15, "p_win_floor": 0.40},
                        raising=False)
    mgr = _make_mgr(_FakeBrainPwin(p_win=0.20), _make_holding())
    out = mgr.evaluate_holding("BTC", "binance")
    assert out["action"] == "HOLD"
