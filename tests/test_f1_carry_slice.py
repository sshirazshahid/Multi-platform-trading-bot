"""Phase D-F1 — delta-neutral funding/basis carry F1 StrategySpec tests.

LAB-SIM ONLY (no real legs). Covers the F1 net-expected-edge entry gate (with its
depth / liquidation-buffer / funding-freshness / atomic-two-leg-fill sub-checks),
the four exit gates, the leg-aware directional-SL suppression on a hedged leg, the
per-venue/symbol PnL-concentration guard, and the end-to-end register -> accept()
slice (a correct NO_GO is success; live INFEASIBLE-at-$420).
"""
from __future__ import annotations

import pytest

from core.strategy_spec import StrategySpec
from research import funding_carry_lab as fc


# ── F1 net-expected-edge entry gate ───────────────────────────────────
def test_f1_net_expected_edge_bps():
    # +0.01%/8h over 3 settlements = 3 bps gross; minus 1 bp cost = 2 bps net.
    edge = fc.f1_net_expected_edge_bps(
        funding_per_settlement=0.0001, hold_settlements=3, round_trip_cost_frac=0.0001
    )
    assert edge == pytest.approx(3.0 - 1.0)


def test_f1_entry_gate_allows_rich_carry():
    ok, reason, _ = fc.f1_entry_gate(
        funding_per_settlement=0.0004, hold_settlements=9,
        round_trip_cost_frac=0.0002, depth_ratio=30.0, liq_buffer_x=5.0,
        funding_age_sec=30.0, both_legs_fillable=True,
    )
    assert ok is True
    assert reason == "ok"


@pytest.mark.parametrize("kw,bad", [
    # thin edge below max(15bps, 3x cost): 3 settlements * 0.5bp = 1.5bp net.
    (dict(funding_per_settlement=0.00006, hold_settlements=3,
          round_trip_cost_frac=0.0001, depth_ratio=30.0, liq_buffer_x=5.0,
          funding_age_sec=30.0, both_legs_fillable=True), "edge"),
    (dict(funding_per_settlement=0.0004, hold_settlements=9,
          round_trip_cost_frac=0.0002, depth_ratio=5.0, liq_buffer_x=5.0,
          funding_age_sec=30.0, both_legs_fillable=True), "depth"),
    (dict(funding_per_settlement=0.0004, hold_settlements=9,
          round_trip_cost_frac=0.0002, depth_ratio=30.0, liq_buffer_x=2.0,
          funding_age_sec=30.0, both_legs_fillable=True), "liquidation"),
    (dict(funding_per_settlement=0.0004, hold_settlements=9,
          round_trip_cost_frac=0.0002, depth_ratio=30.0, liq_buffer_x=5.0,
          funding_age_sec=600.0, both_legs_fillable=True), "funding_stale"),
    (dict(funding_per_settlement=0.0004, hold_settlements=9,
          round_trip_cost_frac=0.0002, depth_ratio=30.0, liq_buffer_x=5.0,
          funding_age_sec=30.0, both_legs_fillable=False), "atomic"),
])
def test_f1_entry_gate_rejects(kw, bad):
    ok, reason, _ = fc.f1_entry_gate(**kw)
    assert ok is False
    assert bad in reason


def test_f1_entry_gate_edge_floor_is_max_15bps_or_3x_cost():
    # 3x cost dominates when cost is large: cost=10bps -> threshold=30bps.
    ok, reason, det = fc.f1_entry_gate(
        funding_per_settlement=0.0008, hold_settlements=3,  # 24 bps gross
        round_trip_cost_frac=0.0010, depth_ratio=30.0, liq_buffer_x=5.0,
        funding_age_sec=30.0, both_legs_fillable=True,
    )
    # net = 24 - 10 = 14 bps < 30 bps threshold -> reject on edge.
    assert ok is False and "edge" in reason
    assert det["edge_threshold_bps"] == pytest.approx(30.0)


# ── Four exit gates (F1 reuses the S1 carry exit signal) ──────────────
def test_f1_exit_two_consecutive_negative_settlements():
    st = fc.CarryPositionState(consec_negative_settlements=2)
    assert fc.carry_exit_signal(st)[0] is True


def test_f1_exit_adverse_basis():
    st = fc.CarryPositionState(adverse_basis_move_bps=55.0)
    assert fc.carry_exit_signal(st)[0] is True


def test_f1_exit_margin_buffer():
    st = fc.CarryPositionState(margin_buffer_x=2.5)
    assert fc.carry_exit_signal(st)[0] is True


def test_f1_exit_stale_hedge_leg():
    st = fc.CarryPositionState(hedge_leg_age_sec=600.0)
    assert fc.carry_exit_signal(st)[0] is True


# ── Leg-aware risk: directional SL suppressed on a hedged leg ──────────
def test_f1_directional_stop_suppressed_on_hedged_leg():
    p = fc.TwoLegCarryPosition.open(symbol="BTC/USDT", notional=1000.0,
                                    spot_px=100.0, perp_px=100.0)
    assert fc.directional_stop_should_fire(p.perp_leg, pnl_pct=-12.0, atr_sl_hit=True) is False
    assert fc.directional_stop_should_fire(p.spot_leg, pnl_pct=-12.0, atr_sl_hit=True) is False
    directional = type("P", (), {"strategy": "mcp"})()
    assert fc.directional_stop_should_fire(directional, pnl_pct=-12.0, atr_sl_hit=True) is True


# ── PnL concentration guard ───────────────────────────────────────────
def test_pnl_concentration_ok_and_breach():
    ok, share, _ = fc.pnl_concentration_ok({"a": 1.0, "b": 1.0, "c": 1.0}, max_pct=40.0)
    assert ok is True and share == pytest.approx(1 / 3 * 100)
    bad, bshare, worst = fc.pnl_concentration_ok({"a": 9.0, "b": 1.0}, max_pct=40.0)
    assert bad is False and bshare == pytest.approx(90.0) and worst == "a"


# ── F1 StrategySpec ───────────────────────────────────────────────────
def test_build_f1_spec_is_strategyspec():
    spec = fc.build_f1_spec()
    assert isinstance(spec, StrategySpec)
    assert spec.id == "F1"
    assert spec.market_type == "spot_perp_hedge"
    assert spec.risk_limits.get("leg_aware_hedged") is True
    assert spec.risk_limits.get("live_feasibility") == "INFEASIBLE-AT-$420"


def test_f1_verdict_constant_marks_infeasible():
    assert "INFEASIBLE-AT-$420" in fc.F1_LIVE_FEASIBILITY


# ── End-to-end slice: register -> accept() -> evidence (NO_GO ok) ──────
def test_run_f1_slice_registers_and_records_verdict(tmp_path):
    from scripts.f1_carry_slice import run_f1_slice

    reg = tmp_path / "active_strategies.json"
    rep = run_f1_slice(registry_path=reg, n_cycles=64, seed=1)
    assert rep["n_cycles"] >= 60
    # accept() ran and produced a boolean verdict; NO_GO is an acceptable outcome.
    assert isinstance(rep["verdict"]["accept"], bool)
    assert rep["evidence"]["promotion_status"] in ("PROMOTED", "NO_EDGE")
    # F1 registered as a StrategySpec through the EvidenceRegistry ledger.
    import json
    ledger = json.loads(reg.read_text(encoding="utf-8"))
    assert "F1" in ledger["strategies"]
    # zero unresolved one-leg events; concentration + cycle-count gate recorded.
    assert rep["f1_gate"]["unresolved_one_leg_events"] == 0
    assert rep["oos_metrics"]["live_feasibility"] == "INFEASIBLE-AT-$420"
    assert "concentration_ok" in rep["f1_gate"]
    assert "net_carry_positive_2x_stress" in rep["f1_gate"]
