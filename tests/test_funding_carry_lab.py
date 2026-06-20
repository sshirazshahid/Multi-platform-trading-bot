"""Tests for the delta-neutral cash-and-carry analytics lab.

Deterministic, offline. Verifies the carry arithmetic, break-even logic, and the
honest risk readouts (% positive settlements, worst settlement, break-even).
"""
from __future__ import annotations

import math

import pytest

from research import funding_carry_lab as fc


def test_annualize_funding():
    # 0.01%/8h * 1095 settlements = 10.95%/yr
    assert fc.annualize_funding(0.0001, 1095) == pytest.approx(10.95)


def test_round_trip_cost():
    # 4 crossings * (fee+slip) = 4 * (0.0006+0.0005) = 0.0044
    assert fc.round_trip_cost_pct(0.0006, 0.0005) == pytest.approx(0.0044)


def test_break_even_funding_per_settlement():
    be = fc.break_even_funding_per_settlement(1095, 0.0006, 0.0005)
    assert be == pytest.approx(0.0044 / 1095)
    with pytest.raises(ValueError):
        fc.break_even_funding_per_settlement(0)


def test_cash_and_carry_constant_positive_exact():
    rates = [0.0001] * 1095
    r = fc.simulate_cash_and_carry(rates, notional=10_000.0)
    assert r.gross_funding_pnl == pytest.approx(1095.0)   # 0.0001*10000*1095
    assert r.total_costs == pytest.approx(44.0)           # 2 legs *2 events *0.0011*10000
    assert r.net_pnl == pytest.approx(1051.0)
    assert r.net_yield_pct == pytest.approx(10.51)
    assert r.annualized_net_yield_pct == pytest.approx(10.51, abs=1e-6)
    assert r.pct_settlements_positive == pytest.approx(100.0)
    assert r.extra["avg_funding_clears_breakeven"] is True


def test_cash_and_carry_negative_funding_loses():
    r = fc.simulate_cash_and_carry([-0.0001] * 500, notional=10_000.0)
    assert r.net_pnl < 0
    assert r.pct_settlements_positive == 0.0
    assert r.extra["avg_funding_clears_breakeven"] is False


def test_cash_and_carry_zero_funding_is_minus_costs():
    r = fc.simulate_cash_and_carry([0.0] * 100, notional=10_000.0)
    assert r.gross_funding_pnl == pytest.approx(0.0)
    assert r.net_pnl == pytest.approx(-r.total_costs)
    assert math.isinf(r.break_even_settlements)


def test_worst_and_pct_positive():
    rates = [0.0002, -0.0003, 0.0001, 0.0]
    r = fc.simulate_cash_and_carry(rates, notional=1000.0)
    assert r.worst_settlement_rate == pytest.approx(-0.0003)
    assert r.pct_settlements_positive == pytest.approx(50.0)  # 2 of 4 > 0


def test_thin_funding_may_not_clear_costs():
    # avg funding below break-even over the horizon -> net negative
    n = 200
    be = fc.break_even_funding_per_settlement(n)
    rates = [be * 0.5] * n  # half of break-even
    r = fc.simulate_cash_and_carry(rates)
    assert r.net_pnl < 0
    assert r.extra["avg_funding_clears_breakeven"] is False


def test_summarize_funding():
    s = fc.summarize_funding([0.0002, -0.0002, 0.0002, 0.0002])
    assert s["n"] == 4
    assert s["pct_positive"] == pytest.approx(75.0)
    assert s["max"] == pytest.approx(0.0002)
    assert s["min"] == pytest.approx(-0.0002)


@pytest.mark.parametrize("bad_call", [
    lambda: fc.simulate_cash_and_carry([]),
    lambda: fc.simulate_cash_and_carry([0.0001], notional=0),
    lambda: fc.summarize_funding([]),
])
def test_validation(bad_call):
    with pytest.raises(ValueError):
        bad_call()
