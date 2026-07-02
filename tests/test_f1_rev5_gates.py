"""Phase 2 — F1 gate completion to the Rev 5 carry-first spec (LAB-SIM only).

One test per NEW entry gate / exit gate, the sizing helper (5%/20%/leverage/no
averaging-down), the concentration bump to 60%, and the build_f1_spec universe
latch (BTC+ETH binance-only by default, SOL/BNB + other venues opt-in).
"""
from __future__ import annotations

import pytest

from research import funding_carry_lab as fc

# Kwargs that pass every pre-existing F1 entry check (rich carry, clean book).
_GOOD = dict(
    funding_per_settlement=0.0004, hold_settlements=9,
    round_trip_cost_frac=0.0002, depth_ratio=30.0, liq_buffer_x=5.0,
    funding_age_sec=30.0, both_legs_fillable=True,
)


# ── NEW entry gates ────────────────────────────────────────────────────
def test_entry_rejects_nonpositive_funding_rate():
    kw = dict(_GOOD, funding_per_settlement=-0.0004)
    ok, reason, _ = fc.f1_entry_gate(**kw)
    assert ok is False and "funding_rate" in reason


def test_entry_rejects_negative_7d_average_funding():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, avg_funding_7d=-0.0001)
    assert ok is False and "avg_funding_7d" in reason
    assert fc.f1_entry_gate(**_GOOD, avg_funding_7d=0.0002)[0] is True


def test_entry_rejects_perp_mark_below_spot_mid():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, perp_mark=99.9, spot_mid=100.0)
    assert ok is False and "perp_mark" in reason
    assert fc.f1_entry_gate(**_GOOD, perp_mark=100.1, spot_mid=100.0)[0] is True


def test_entry_rejects_wide_spot_spread():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, spot_spread_bps=6.0)
    assert ok is False and "spot_spread" in reason
    assert fc.f1_entry_gate(**_GOOD, spot_spread_bps=4.9)[0] is True


def test_entry_rejects_wide_perp_spread():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, perp_spread_bps=6.0)
    assert ok is False and "perp_spread" in reason
    assert fc.f1_entry_gate(**_GOOD, perp_spread_bps=4.9)[0] is True


@pytest.mark.parametrize("minutes", [10.0, 200.0])
def test_entry_rejects_time_to_next_funding_out_of_window(minutes):
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, time_to_next_funding_min=minutes)
    assert ok is False and "time_to_next_funding" in reason


def test_entry_accepts_time_to_next_funding_in_window():
    assert fc.f1_entry_gate(**_GOOD, time_to_next_funding_min=60.0)[0] is True


def test_entry_rejects_stale_feeds_flag():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD, feeds_fresh=False)
    assert ok is False and "feeds" in reason


def test_entry_backward_compatible_defaults_still_pass():
    ok, reason, _ = fc.f1_entry_gate(**_GOOD)
    assert ok is True and reason == "ok"


# ── NEW exit gates ─────────────────────────────────────────────────────
def test_exit_on_notional_mismatch_over_1pct():
    st = fc.CarryPositionState(notional_mismatch_pct=1.5)
    should, reason = fc.carry_exit_signal(st)
    assert should is True and "notional_mismatch" in reason


def test_exit_on_spread_widening_beyond_15bps():
    st = fc.CarryPositionState(spread_bps=16.0)
    should, reason = fc.carry_exit_signal(st)
    assert should is True and "spread" in reason


def test_exit_on_negative_net_edge_recheck():
    st = fc.CarryPositionState(net_edge_bps=-0.5)
    should, reason = fc.carry_exit_signal(st)
    assert should is True and "net_edge" in reason


def test_exit_on_max_holding_window():
    st = fc.CarryPositionState(settlements_held=fc.F1_MAX_HOLD_SETTLEMENTS)
    should, reason = fc.carry_exit_signal(st)
    assert should is True and "max_hold" in reason


def test_exit_holds_when_new_gates_satisfied():
    st = fc.CarryPositionState(
        notional_mismatch_pct=0.5, spread_bps=10.0, net_edge_bps=2.0,
        settlements_held=fc.F1_MAX_HOLD_SETTLEMENTS - 1,
    )
    assert fc.carry_exit_signal(st) == (False, "hold")


# ── Sizing helper ──────────────────────────────────────────────────────
def test_sizing_rejects_over_5pct_per_symbol():
    ok, reason = fc.f1_sizing_gate(
        paper_equity=10_000.0, symbol_notional=600.0, total_carry_notional=600.0,
        leverage=1.0)
    assert ok is False and "symbol" in reason


def test_sizing_rejects_over_20pct_total_carry():
    ok, reason = fc.f1_sizing_gate(
        paper_equity=10_000.0, symbol_notional=400.0, total_carry_notional=2_100.0,
        leverage=1.0)
    assert ok is False and "total_carry" in reason


def test_sizing_leverage_1x_initial_2x_max():
    base = dict(paper_equity=10_000.0, symbol_notional=400.0,
                total_carry_notional=1_000.0)
    ok, reason = fc.f1_sizing_gate(**base, leverage=1.5, is_initial_entry=True)
    assert ok is False and "leverage" in reason
    assert fc.f1_sizing_gate(**base, leverage=2.0, is_initial_entry=False)[0] is True
    ok2, reason2 = fc.f1_sizing_gate(**base, leverage=2.5, is_initial_entry=False)
    assert ok2 is False and "leverage" in reason2


def test_sizing_rejects_averaging_down():
    ok, reason = fc.f1_sizing_gate(
        paper_equity=10_000.0, symbol_notional=400.0, total_carry_notional=1_000.0,
        leverage=1.0, adds_to_losing_position=True)
    assert ok is False and "averaging_down" in reason


def test_sizing_passes_within_all_caps():
    assert fc.f1_sizing_gate(
        paper_equity=10_000.0, symbol_notional=500.0, total_carry_notional=2_000.0,
        leverage=1.0)[0] is True


# ── Concentration bump 40 -> 60 (per symbol AND per venue) ────────────
def test_concentration_default_is_60_pct():
    assert fc.F1_MAX_PNL_CONCENTRATION_PCT == 60.0
    # 55% share passes at the new 60% default, would have failed at 40%.
    ok, share, _ = fc.pnl_concentration_ok({"a": 5.5, "b": 4.5})
    assert ok is True and share == pytest.approx(55.0)
    assert fc.pnl_concentration_ok({"a": 6.5, "b": 3.5})[0] is False


# ── build_f1_spec universe latch ───────────────────────────────────────
def test_build_f1_spec_defaults_btc_eth_binance_only():
    spec = fc.build_f1_spec()
    assert spec.symbols == ["BTC/USDT", "ETH/USDT"]
    assert spec.venues == ["binance"]


def test_build_f1_spec_rejects_extended_universe_without_latch():
    with pytest.raises(ValueError):
        fc.build_f1_spec(symbols=("SOL/USDT",))
    with pytest.raises(ValueError):
        fc.build_f1_spec(venues=("bybit",))


def test_build_f1_spec_extended_universe_opt_in():
    spec = fc.build_f1_spec(
        symbols=("BTC/USDT", "SOL/USDT", "BNB/USDT"),
        venues=("binance", "bybit"),
        allow_extended_universe=True,
    )
    assert "SOL/USDT" in spec.symbols and "bybit" in spec.venues
