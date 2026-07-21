"""MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback (2026-07-21).

No futures model has ever legitimately passed promotion, so the strict gate's
demand for a promoted model probability blocks 100% of MCP_DIRECTIONAL_PAPER
entries (reason=economic_gate_model_missing). The profile-gated fallback lets
the PAPER MAX_FLOW_BAND cohort trade on the stressed GEOMETRIC breakeven check
alone: the bracket's TP must still clear stressed round-trip costs
(breakeven WR < 1.0). Strict remains the default everywhere; the model path
returns automatically the moment a model is legitimately promoted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config as config_module
from core.bot_engine import BotEngine
from core.economic_entry_gate import evaluate_directional_entry

ROOT = Path(__file__).resolve().parents[1]


def _evaluate(**overrides):
    values = {
        "model_version": None,
        "promoted_model_version": None,
        "p_win": None,
        "stop_frac": 0.02,
        "target_frac": 0.01,
        "entry_fee_frac": 0.0005,
        "entry_slippage_frac": 0.0002,
        "exit_fee_frac": 0.0005,
        "exit_slippage_frac": 0.0005,
        "stressed_exit_cost_floor_frac": 0.002,
        "fee_stress_multiplier": 1.5,
        "slippage_stress_multiplier": 2.0,
        "probability_margin": 0.03,
    }
    values.update(overrides)
    return evaluate_directional_entry(**values)


def _action(**overrides):
    action = {
        "type": "OPEN",
        "symbol": "LTC/USDT",
        "exchange": "binance",
        "market_type": "futures",
        "side": "buy",
        "model_version": None,
        "p_win_ensemble": None,
        "expected_entry_fee_usdt": 5.0,
        "expected_entry_slippage_usdt": 2.0,
        "execution_snapshot": {
            "snapshot": {"context": {"book_source": "test"}},
        },
    }
    action.update(overrides)
    return action


def _gate(engine, action, *, operating_mode="PAPER", sl_pct=2.0, tp_pct=1.0):
    return engine._apply_mcp_directional_economic_gate(
        action,
        strategy_id="MCP_DIRECTIONAL_PAPER",
        operating_mode=operating_mode,
        market_type="futures",
        exchange_name="binance",
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        entry_quote_usdt=10_000.0,
    )


# ── config-level mode resolution (profile gate) ──────────────────────────


def test_mode_defaults_to_strict():
    fn = config_module._profile_gated_economic_gate_mode
    assert fn("", "PAPER", "MAX_FLOW_BAND") == "strict"
    assert fn("strict", "PAPER", "MAX_FLOW_BAND") == "strict"


def test_mode_paper_fallback_requires_paper_plus_max_flow_band():
    fn = config_module._profile_gated_economic_gate_mode
    assert fn("paper_fallback", "PAPER", "MAX_FLOW_BAND") == "paper_fallback"
    assert fn("paper_fallback", "PAPER", "STANDARD") == "strict"
    assert fn("paper_fallback", "CONTROLLED_LIVE", "STANDARD") == "strict"
    assert fn("paper_fallback", "OBSERVATION", "STANDARD") == "strict"


def test_mode_invalid_value_raises():
    with pytest.raises(ValueError):
        config_module._profile_gated_economic_gate_mode(
            "yolo", "PAPER", "MAX_FLOW_BAND"
        )


def test_env_template_documents_the_mode_knob():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict" in template


# ── pure-function semantics ──────────────────────────────────────────────


def test_strict_default_preserved_model_missing_blocks():
    decision = _evaluate()  # no paper_fallback flag -> today's behavior
    assert decision.allowed is False
    assert decision.reason == "economic_gate_model_missing"


def test_fallback_admits_bracket_that_clears_stressed_costs():
    decision = _evaluate(paper_fallback=True)
    assert decision.allowed is True
    assert decision.reason == "economic_gate_paper_fallback_pass"
    assert decision.breakeven_p_win is not None
    assert decision.breakeven_p_win < 1.0
    # No model probability term was consulted.
    assert decision.model_version is None
    assert decision.p_win is None
    # Stressed costs identical to the strict path's arithmetic:
    # entry 1.5*5bps + 2*2bps = 11.5bps; exit floor 20bps -> RT 31.5bps.
    assert decision.stressed_round_trip_cost_frac == pytest.approx(0.00315)
    assert decision.breakeven_p_win == pytest.approx((0.02 + 0.00315) / 0.03)


def test_fallback_blocks_bracket_that_cannot_clear_stressed_costs():
    # TP 2bps < stressed round-trip 31.5bps: even a 100% hit rate loses.
    decision = _evaluate(paper_fallback=True, target_frac=0.0002)
    assert decision.allowed is False
    assert decision.reason == "economic_gate_stressed_breakeven"
    assert decision.breakeven_p_win >= 1.0


def test_fallback_still_fails_closed_on_invalid_inputs():
    assert (
        _evaluate(paper_fallback=True, target_frac=0).reason
        == "economic_gate_geometry_invalid"
    )
    assert (
        _evaluate(paper_fallback=True, exit_fee_frac=None).reason
        == "economic_gate_cost_invalid"
    )
    assert (
        _evaluate(paper_fallback=True, fee_stress_multiplier=0.99).reason
        == "economic_gate_config_invalid"
    )


def test_model_present_uses_original_model_path_in_both_modes():
    for paper_fallback in (False, True):
        decision = _evaluate(
            paper_fallback=paper_fallback,
            model_version="promoted-v1",
            promoted_model_version="promoted-v1",
            p_win=0.90,
        )
        assert decision.reason == "economic_gate_pass"
        weak = _evaluate(
            paper_fallback=paper_fallback,
            model_version="promoted-v1",
            promoted_model_version="promoted-v1",
            p_win=0.45,
        )
        assert weak.allowed is False
        assert weak.reason in {
            "economic_gate_negative_expectancy",
            "economic_gate_probability_margin_not_met",
        }


def test_fallback_does_not_bypass_promotion_mismatch():
    decision = _evaluate(
        paper_fallback=True,
        model_version="stale-v0",
        promoted_model_version="promoted-v1",
        p_win=0.90,
    )
    assert decision.allowed is False
    assert decision.reason == "economic_gate_model_not_promoted"


# ── bot_engine wiring ────────────────────────────────────────────────────


def test_bot_gate_strict_mode_blocks_when_no_model_exists(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: None),
    )
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "strict"
    )
    engine = object.__new__(BotEngine)
    action = _action()

    assert _gate(engine, action) is False
    assert action["economic_gate_reason"] == "economic_gate_model_missing"


def test_bot_gate_paper_fallback_admits_clearing_geometry(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: None),
    )
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
    )
    engine = object.__new__(BotEngine)
    action = _action()

    assert _gate(engine, action, sl_pct=2.0, tp_pct=1.0) is True
    audit = action["economic_entry_gate"]
    assert action["economic_gate_reason"] == "economic_gate_paper_fallback_pass"
    assert audit["breakeven_p_win"] < 1.0
    assert (
        action["execution_snapshot"]["snapshot"]["context"][
            "economic_entry_gate"
        ]
        == audit
    )


def test_bot_gate_paper_fallback_blocks_non_clearing_geometry(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: None),
    )
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
    )
    engine = object.__new__(BotEngine)
    action = _action()

    # TP 0.02% cannot clear the stressed round-trip cost.
    assert _gate(engine, action, sl_pct=2.0, tp_pct=0.02) is False
    assert action["economic_gate_reason"] == "economic_gate_stressed_breakeven"


def test_bot_gate_paper_fallback_inert_outside_paper_mode(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: None),
    )
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
    )
    engine = object.__new__(BotEngine)
    action = _action()

    # CONTROLLED_LIVE never reaches this PAPER-lane gate at all, and no
    # fallback admission audit may be produced for it.
    assert _gate(engine, action, operating_mode="CONTROLLED_LIVE") is True
    assert "economic_entry_gate" not in action


def test_bot_gate_model_present_uses_model_path_in_fallback_mode(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: "promoted-v1"),
    )
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
    )
    engine = object.__new__(BotEngine)
    action = _action(model_version="promoted-v1", p_win_ensemble=0.90)

    assert _gate(engine, action, sl_pct=1.0, tp_pct=2.0) is True
    assert action["economic_gate_reason"] == "economic_gate_pass"
