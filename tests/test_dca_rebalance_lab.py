"""Tests for the offline DCA / rebalancing research lab.

Deterministic — no exchange, network, or bot state. Verifies the arithmetic and
the documented risk-adjusted properties (DCA lowers drawdown & average cost in a
falling market; rebalancing triggers only on drift beyond threshold).
"""
from __future__ import annotations

import pytest

from research import dca_rebalance_lab as lab

# ── lump sum ──────────────────────────────────────────────────────────

def test_lump_sum_exact_no_costs():
    r = lab.simulate_lump_sum([100, 200], capital=1000.0, fee_pct=0.0,
                              slippage_pct=0.0)
    assert r.extra["coins"] == pytest.approx(10.0)
    assert r.final_value == pytest.approx(2000.0)
    assert r.total_return_pct == pytest.approx(100.0)
    assert r.n_trades == 1


def test_lump_sum_costs_reduce_coins():
    no = lab.simulate_lump_sum([100, 100], capital=1000.0, fee_pct=0.0,
                               slippage_pct=0.0)
    fee = lab.simulate_lump_sum([100, 100], capital=1000.0, fee_pct=0.001,
                                slippage_pct=0.0)
    assert fee.extra["coins"] < no.extra["coins"]
    assert fee.fees_paid == pytest.approx(1.0)  # 0.1% of 1000


# ── DCA ───────────────────────────────────────────────────────────────

def test_dca_flat_price_returns_capital():
    r = lab.simulate_dca([100] * 10, capital=1000.0, interval_bars=1,
                         fee_pct=0.0, slippage_pct=0.0)
    assert r.extra["n_buys"] == 10
    assert r.invested == pytest.approx(1000.0)
    assert r.final_value == pytest.approx(1000.0)
    assert r.extra["uninvested_cash"] == pytest.approx(0.0)


def test_dca_lowers_drawdown_vs_lump_in_falling_market():
    prices = [100 - i for i in range(50)] + [50] * 20  # fall to 51 then flat
    cmp = lab.compare_single_asset(prices, capital=1000.0, interval_bars=1)
    assert cmp["dca"].max_drawdown_pct < cmp["lump_sum_hold"].max_drawdown_pct


def test_dca_lowers_average_cost_in_falling_market():
    prices = [100 - i for i in range(40)]  # strictly declining
    dca = lab.simulate_dca(prices, capital=1000.0, interval_bars=1)
    lump = lab.simulate_lump_sum(prices, capital=1000.0)
    assert dca.extra["avg_cost"] < lump.extra["avg_cost"]
    assert dca.extra["avg_cost"] < prices[0]


def test_dca_dip_multiplier_front_loads():
    prices = [100, 100, 70, 100, 100]  # one dip at bar 2
    base = lab.simulate_dca(prices, capital=1000.0, interval_bars=1)
    dip = lab.simulate_dca(prices, capital=1000.0, interval_bars=1,
                           dip_threshold=0.05, dip_multiplier=3.0)
    # dip variant buys more coins on the cheap bar → ends with more coins
    assert dip.extra["coins"] > base.extra["coins"]


def test_dca_never_exceeds_capital():
    prices = [100 - i * 0.5 for i in range(30)]
    r = lab.simulate_dca(prices, capital=500.0, interval_bars=1,
                         dip_threshold=0.01, dip_multiplier=5.0)
    assert r.invested <= 500.0 + 1e-9
    assert r.extra["uninvested_cash"] >= -1e-9


# ── metric helpers ────────────────────────────────────────────────────

def test_max_drawdown():
    assert lab._max_drawdown_pct([100, 50, 75]) == pytest.approx(50.0)
    assert lab._max_drawdown_pct([100, 110, 120]) == pytest.approx(0.0)


def test_sharpe_zero_variance():
    assert lab._sharpe([100, 100, 100, 100], 365) == 0.0


# ── basket rebalance ──────────────────────────────────────────────────

def test_basket_validation():
    with pytest.raises(ValueError):
        lab.simulate_threshold_rebalance({"A": [1, 2]}, {"A": 0.6, "B": 0.4})
    with pytest.raises(ValueError):  # weights don't sum to 1
        lab.simulate_buy_and_hold_basket({"A": [1, 2], "B": [1, 2]},
                                         {"A": 0.5, "B": 0.4})
    with pytest.raises(ValueError):  # unequal lengths
        lab.simulate_buy_and_hold_basket({"A": [1, 2, 3], "B": [1, 2]},
                                         {"A": 0.5, "B": 0.5})


def test_rebalance_noop_when_threshold_never_hit():
    series = {"A": [100, 101, 102], "B": [100, 101, 102]}  # move together
    tgt = {"A": 0.5, "B": 0.5}
    reb = lab.simulate_threshold_rebalance(series, tgt, capital=1000.0,
                                           threshold=0.5)
    hold = lab.simulate_buy_and_hold_basket(series, tgt, capital=1000.0)
    assert reb.extra["rebalances"] == 0
    assert reb.final_value == pytest.approx(hold.final_value)


def test_rebalance_triggers_on_drift():
    # A moons, B crashes -> weights diverge far past threshold -> rebalance fires
    series = {"A": [100, 200, 400, 800], "B": [100, 50, 25, 12]}
    tgt = {"A": 0.5, "B": 0.5}
    reb = lab.simulate_threshold_rebalance(series, tgt, capital=1000.0,
                                           threshold=0.05)
    assert reb.extra["rebalances"] >= 1
    assert reb.n_trades > len(tgt)


# ── input validation ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [[], [100], [100, -5], [0, 100]])
def test_price_validation(bad):
    with pytest.raises(ValueError):
        lab.simulate_lump_sum(bad)
