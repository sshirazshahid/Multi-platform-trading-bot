"""Phase 4 / S1 — delta-neutral funding/basis carry LAB-SIM tests.

LAB-SIM ONLY (no real legs). Covers the four exit gates, the funding-persistence
pre-validation (GO/NO_GO + CI), spot borrow/asymmetric exit costs, and the
leg-aware risk model that suppresses the directional ATR-SL/8%-guardian on a
tagged hedged leg (mirror of is_tsmom_position).
"""
from __future__ import annotations

import pytest

from research import funding_carry_lab as fc


# ── Entry gate ────────────────────────────────────────────────────────
def test_entry_gate_allows_tight_book():
    ok, reason = fc.carry_entry_gate(basis_bps=10.0, depth_ratio=30.0, spread_bps=3.0)
    assert ok is True
    assert reason == "ok"


@pytest.mark.parametrize("kw,bad", [
    (dict(basis_bps=30.0, depth_ratio=30.0, spread_bps=3.0), "basis"),
    (dict(basis_bps=10.0, depth_ratio=10.0, spread_bps=3.0), "depth"),
    (dict(basis_bps=10.0, depth_ratio=30.0, spread_bps=8.0), "spread"),
])
def test_entry_gate_rejects(kw, bad):
    ok, reason = fc.carry_entry_gate(**kw)
    assert ok is False
    assert bad in reason


# ── Exit gates (all four) ─────────────────────────────────────────────
def test_exit_two_consecutive_negative_settlements():
    st = fc.CarryPositionState(consec_negative_settlements=2)
    should, reason = fc.carry_exit_signal(st)
    assert should is True
    assert "negative" in reason
    # one negative settlement is not enough
    st1 = fc.CarryPositionState(consec_negative_settlements=1)
    assert fc.carry_exit_signal(st1)[0] is False


def test_exit_basis_loss_over_50bps():
    st = fc.CarryPositionState(adverse_basis_move_bps=55.0)
    should, reason = fc.carry_exit_signal(st)
    assert should is True
    assert "basis" in reason


def test_exit_margin_buffer_below_3x_maintenance():
    st = fc.CarryPositionState(margin_buffer_x=2.5)
    should, reason = fc.carry_exit_signal(st)
    assert should is True
    assert "margin" in reason


def test_exit_stale_hedge_leg():
    st = fc.CarryPositionState(hedge_leg_age_sec=600.0)
    should, reason = fc.carry_exit_signal(st, max_hedge_staleness_sec=300.0)
    assert should is True
    assert "stale" in reason


def test_exit_healthy_state_holds():
    st = fc.CarryPositionState()
    should, reason = fc.carry_exit_signal(st)
    assert should is False
    assert reason == "hold"


# ── Spot borrow / asymmetric exit costs ───────────────────────────────
def test_spot_financing_cost_accrues_per_settlement():
    c = fc.spot_financing_cost(notional=10_000.0, borrow_rate_per_settlement=0.00002,
                               n_settlements=1095)
    assert c == pytest.approx(10_000.0 * 0.00002 * 1095)


def test_carry_with_financing_reduces_net():
    base = fc.simulate_cash_and_carry([0.0001] * 500, notional=10_000.0)
    fin = fc.simulate_cash_and_carry(
        [0.0001] * 500, notional=10_000.0,
        borrow_rate_per_settlement=0.00003, spot_exit_extra_taker_pct=0.0004)
    assert fin.net_pnl < base.net_pnl
    assert fin.extra["spot_financing_cost"] > 0
    assert fin.extra["spot_exit_extra_cost"] > 0


# ── Funding-persistence pre-validation ────────────────────────────────
def test_funding_persistence_go_on_strong_persistent_carry():
    # Strongly positive, persistent funding well above costs -> GO.
    rates = [0.0003 + (i % 11 - 5) * 1e-6 for i in range(1200)]
    res = fc.funding_persistence_test(rates, trailing_window=21, forward_horizon=21, seed=0)
    assert res["verdict"] == "GO"
    assert res["ci_lb_net_pct"] > res["cost_threshold_pct"]


def test_funding_persistence_no_go_on_thin_noisy():
    import random as _r
    rng = _r.Random(7)
    rates = [rng.gauss(0.0, 0.0002) for _ in range(1200)]
    res = fc.funding_persistence_test(rates, trailing_window=21, forward_horizon=21, seed=0)
    assert res["verdict"] == "NO_GO"


def test_funding_persistence_deterministic():
    rates = [0.0002 + (i % 7 - 3) * 1e-6 for i in range(800)]
    a = fc.funding_persistence_test(rates, seed=1)
    b = fc.funding_persistence_test(rates, seed=1)
    assert a == b


# ── Leg-aware risk model (directional SL suppressed on hedged leg) ─────
def test_is_hedged_leg_tag():
    p = fc.TwoLegCarryPosition.open(symbol="BTC/USDT", notional=1000.0, spot_px=100.0,
                                    perp_px=100.0)
    assert fc.is_hedged_leg(p.spot_leg) is True
    assert fc.is_hedged_leg(p.perp_leg) is True
    directional = type("P", (), {"strategy": "mcp"})()
    assert fc.is_hedged_leg(directional) is False


def test_directional_stop_suppressed_on_hedged_leg():
    p = fc.TwoLegCarryPosition.open(symbol="BTC/USDT", notional=1000.0, spot_px=100.0,
                                    perp_px=100.0)
    # A -12% adverse move + ATR-SL hit would fire on a directional position...
    assert fc.directional_stop_should_fire(p.perp_leg, pnl_pct=-12.0, atr_sl_hit=True) is False
    directional = type("P", (), {"strategy": "mcp"})()
    assert fc.directional_stop_should_fire(directional, pnl_pct=-12.0, atr_sl_hit=True) is True
    assert fc.directional_stop_should_fire(directional, pnl_pct=-9.0, atr_sl_hit=False) is True
    assert fc.directional_stop_should_fire(directional, pnl_pct=-2.0, atr_sl_hit=False) is False


def test_per_leg_liquidation_short_perp():
    # short perp at 100, 5x leverage, 0.5% maintenance -> liquidation ABOVE entry
    liq = fc.per_leg_liquidation_price(entry_px=100.0, side="short", leverage=5.0,
                                       maintenance_margin_pct=0.005)
    assert liq > 100.0
    # spot long leg is unleveraged -> no liquidation
    assert fc.per_leg_liquidation_price(entry_px=100.0, side="long", leverage=1.0,
                                        maintenance_margin_pct=0.005) is None


def test_two_leg_notional_matched():
    p = fc.TwoLegCarryPosition.open(symbol="BTC/USDT", notional=1000.0, spot_px=200.0,
                                    perp_px=200.0)
    assert p.spot_leg.notional == pytest.approx(1000.0)
    assert p.perp_leg.notional == pytest.approx(1000.0)
    assert p.spot_leg.qty == pytest.approx(p.perp_leg.qty)  # notional-matched


def test_s1_verdict_constant():
    assert "lab-GO" in fc.S1_VERDICT
    assert "INFEASIBLE-AT-$420" in fc.S1_VERDICT
