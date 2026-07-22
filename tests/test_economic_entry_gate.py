from __future__ import annotations

import inspect
import math
from pathlib import Path

import pytest

import config as config_module
from core.bot_engine import BotEngine, _is_mcp_directional_paper_futures
from core.economic_entry_gate import evaluate_directional_entry

ROOT = Path(__file__).resolve().parents[1]


def _evaluate(**overrides):
    values = {
        "model_version": "promoted-v1",
        "promoted_model_version": "promoted-v1",
        "p_win": 0.60,
        "stop_frac": 0.01,
        "target_frac": 0.02,
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
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "market_type": "futures",
        "side": "buy",
        "model_version": "promoted-v1",
        "p_win_ensemble": 0.60,
        "expected_entry_fee_usdt": 5.0,
        "expected_entry_slippage_usdt": 2.0,
        "execution_snapshot": {
            "snapshot": {"context": {"book_source": "test"}},
        },
    }
    action.update(overrides)
    return action


def test_positive_stressed_after_cost_entry_passes_with_full_audit():
    decision = _evaluate()

    # stressed entry = 1.5*5bps + 2*2bps = 11.5bps
    # stressed exit floor = 20bps (larger than modeled 17.5bps)
    assert decision.stressed_entry_cost_frac == pytest.approx(0.00115)
    assert decision.stressed_exit_cost_frac == pytest.approx(0.002)
    assert decision.stressed_round_trip_cost_frac == pytest.approx(0.00315)
    assert decision.breakeven_p_win == pytest.approx((0.01 + 0.00315) / 0.03)
    assert decision.required_p_win == pytest.approx(
        decision.breakeven_p_win + 0.03
    )
    assert decision.stressed_expectancy_frac == pytest.approx(0.00485)
    assert decision.allowed is True
    assert decision.reason == "economic_gate_pass"
    assert decision.to_dict()["model_version"] == "promoted-v1"


@pytest.mark.parametrize("model_version", [None, "", "fallback", "rule_only"])
def test_missing_or_fallback_model_fails_closed(model_version):
    decision = _evaluate(model_version=model_version)

    assert decision.allowed is False
    assert decision.reason == "economic_gate_model_missing"


def test_arbitrary_or_stale_model_version_cannot_impersonate_promotion():
    decision = _evaluate(model_version="stale-v0")

    assert decision.allowed is False
    assert decision.reason == "economic_gate_model_not_promoted"
    assert decision.promoted_model_version == "promoted-v1"


@pytest.mark.parametrize("p_win", [None, float("nan"), float("inf"), -0.1, 1.1])
def test_nonfinite_or_out_of_range_probability_fails_closed(p_win):
    decision = _evaluate(p_win=p_win)

    assert decision.allowed is False
    assert decision.reason == "economic_gate_p_win_invalid"


def test_nonpositive_after_cost_expectancy_has_explicit_reason():
    decision = _evaluate(p_win=0.30)

    assert decision.stressed_expectancy_frac < 0.0
    assert decision.allowed is False
    assert decision.reason == "economic_gate_negative_expectancy"


def test_positive_ev_still_needs_predeclared_probability_margin():
    decision = _evaluate(p_win=0.45)

    assert decision.stressed_expectancy_frac > 0.0
    assert decision.p_win < decision.required_p_win
    assert decision.allowed is False
    assert decision.reason == "economic_gate_probability_margin_not_met"


def test_invalid_geometry_or_nonconservative_stress_fails_closed():
    assert _evaluate(target_frac=0).reason == "economic_gate_geometry_invalid"
    assert (
        _evaluate(fee_stress_multiplier=0.99).reason
        == "economic_gate_config_invalid"
    )


@pytest.mark.parametrize("strategy_id", [
    "MCP_DIRECTIONAL_PAPER",
    "mcp_registry",
    "algo_det",
])
def test_catalog_aliases_resolve_to_owned_paper_futures_lane(strategy_id):
    assert _is_mcp_directional_paper_futures(
        strategy_id, "futures", "PAPER"
    )


@pytest.mark.parametrize(
    ("strategy_id", "market_type", "mode", "is_tsmom"),
    [
        ("F1", "futures", "PAPER", False),
        ("mcp_registry", "spot", "PAPER", False),
        ("mcp_registry", "futures", "CONTROLLED_LIVE", False),
        ("mcp_registry", "futures", "PAPER", True),
        ("deep_breakout", "futures", "PAPER", False),
    ],
)
def test_other_strategy_lanes_are_out_of_scope(
    strategy_id, market_type, mode, is_tsmom
):
    assert not _is_mcp_directional_paper_futures(
        strategy_id, market_type, mode, is_tsmom=is_tsmom
    )


def test_bot_gate_uses_book_costs_and_persists_audit_in_snapshot(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: "promoted-v1"),
    )
    engine = object.__new__(BotEngine)
    action = _action()

    allowed = engine._apply_mcp_directional_economic_gate(
        action,
        strategy_id="mcp_registry",
        operating_mode="PAPER",
        market_type="futures",
        exchange_name="binance",
        sl_pct=1.0,
        tp_pct=2.0,
        entry_quote_usdt=10_000.0,
    )

    assert allowed is True
    audit = action["economic_entry_gate"]
    assert audit["entry_fee_frac"] == pytest.approx(0.0005)
    assert audit["entry_slippage_frac"] == pytest.approx(0.0002)
    assert audit["stressed_expectancy_frac"] > 0.0
    assert action["economic_gate_reason"] == "economic_gate_pass"
    assert (
        action["execution_snapshot"]["snapshot"]["context"][
            "economic_entry_gate"
        ]
        == audit
    )


def test_bot_gate_fails_closed_when_promoted_pointer_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: None),
    )
    # Strict-mode semantics under test; pin the mode so the live .env
    # (paper_fallback under MAX_FLOW_BAND) cannot invert the assertion.
    monkeypatch.setitem(
        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "strict"
    )
    engine = object.__new__(BotEngine)
    action = _action()

    allowed = engine._apply_mcp_directional_economic_gate(
        action,
        strategy_id="MCP_DIRECTIONAL_PAPER",
        operating_mode="PAPER",
        market_type="futures",
        exchange_name="binance",
        sl_pct=1.0,
        tp_pct=2.0,
        entry_quote_usdt=10_000.0,
    )

    assert allowed is False
    assert action["economic_gate_reason"] == (
        "economic_gate_promoted_model_unavailable"
    )


def test_bot_gate_is_a_noop_for_tsmom_and_carry(monkeypatch):
    engine = object.__new__(BotEngine)
    monkeypatch.setattr(
        BotEngine,
        "_validated_promoted_futures_model_version",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("must not load"))),
    )
    for strategy_id, is_tsmom in (("mcp_registry", True), ("F1", False)):
        action = _action(model_version=None, p_win_ensemble=math.nan)
        assert engine._apply_mcp_directional_economic_gate(
            action,
            strategy_id=strategy_id,
            operating_mode="PAPER",
            market_type="futures",
            exchange_name="binance",
            sl_pct=1.0,
            tp_pct=2.0,
            entry_quote_usdt=10_000.0,
            is_tsmom=is_tsmom,
        )
        assert "economic_entry_gate" not in action


def test_execute_open_gate_is_after_final_geometry_and_book_before_order():
    source = inspect.getsource(BotEngine._execute_open)

    gate = source.index("self._apply_mcp_directional_economic_gate")
    assert source.index("tp_pct = _apply_accuracy_target") < gate
    assert source.index("fetch_and_validate_execution_book") < gate
    assert gate < source.index("self.order_mgr.open_position")
    assert "entry_quote_usdt=float(_book_decision.quote_cost)" in source


def test_config_and_template_predeclare_non_geometry_stress_controls():
    from config import MCP_DIRECTIONAL_ECONOMIC_GATE

    gate = dict(MCP_DIRECTIONAL_ECONOMIC_GATE)
    # 2026-07-21: env- and profile-resolved; semantics covered in
    # tests/test_economic_gate_paper_fallback.py.
    assert gate.pop("mode") in {"strict", "paper_fallback"}
    assert gate == {
        "probability_margin": 0.03,
        "fee_stress_multiplier": 1.5,
        "slippage_stress_multiplier": 2.0,
    }
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MCP_DIRECTIONAL_ECONOMIC_PROBABILITY_MARGIN=0.03" in template
    assert "MCP_DIRECTIONAL_ECONOMIC_FEE_STRESS_MULT=1.5" in template
    assert "MCP_DIRECTIONAL_ECONOMIC_SLIPPAGE_STRESS_MULT=2.0" in template
    assert "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict" in template
