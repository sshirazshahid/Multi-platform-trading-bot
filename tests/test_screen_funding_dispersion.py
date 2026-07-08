"""Tests for the cross-venue funding-dispersion screen core math.

TDD: these pin the load-bearing cost/carry arithmetic before the screen runs.
Pure functions only — no file or network I/O is exercised here.
"""
import numpy as np
import pytest

from research.screen_funding_dispersion import (
    annualize_apr,
    breakeven_settlements,
    net_return_frac,
    roundtrip_cost_frac,
    signed_carry,
    sign_persistence,
)


def test_roundtrip_cost_is_four_legs_plus_slippage_on_all_fills():
    # 2*fee_L + 2*fee_H + 4*slip. Taker binance(5bps)/bybit(6bps), slip 5bps.
    rt = roundtrip_cost_frac(fee_long=0.0005, fee_short=0.0006, slippage=0.0005)
    # 2*0.0005 + 2*0.0006 + 4*0.0005 = 0.0010 + 0.0012 + 0.0020 = 0.0042
    assert rt == pytest.approx(0.0042, abs=1e-12)


def test_roundtrip_cost_maker_is_cheaper_but_still_slippage_dominates():
    rt = roundtrip_cost_frac(fee_long=0.0002, fee_short=0.0002, slippage=0.0005)
    # 0.0004 + 0.0004 + 0.0020 = 0.0028
    assert rt == pytest.approx(0.0028, abs=1e-12)


def test_signed_carry_short_receives_long_pays():
    # short leg on the HIGH-funding venue receives rate_short; long leg pays rate_long.
    rate_short = np.array([0.0001, 0.0002, -0.0001])
    rate_long = np.array([0.00005, 0.00005, 0.00005])
    carry = signed_carry(rate_short, rate_long)
    # element-wise rate_short - rate_long
    np.testing.assert_allclose(carry, [0.00005, 0.00015, -0.00015])


def test_signed_carry_can_go_negative_when_differential_flips():
    # if the venue we shorted funds LOWER than the venue we longed, the pair PAYS.
    carry = signed_carry(np.array([0.0]), np.array([0.0003]))
    assert carry[0] == pytest.approx(-0.0003)


def test_net_return_is_accumulated_carry_minus_one_roundtrip():
    carry = np.array([0.0001, 0.0001, 0.0001])  # 3 settlements, 1bp each = 3bps gross
    net = net_return_frac(carry, roundtrip_cost=0.0028)
    # 0.0003 - 0.0028 = -0.0025
    assert net == pytest.approx(-0.0025, abs=1e-12)


def test_breakeven_settlements_cost_over_carry():
    # 28bps cost / 1bp-per-settlement carry = 28 settlements to break even.
    assert breakeven_settlements(roundtrip_cost=0.0028, mean_carry=0.0001) == pytest.approx(28.0)


def test_breakeven_is_infinite_when_carry_nonpositive():
    assert breakeven_settlements(0.0028, 0.0) == float("inf")
    assert breakeven_settlements(0.0028, -0.0001) == float("inf")


def test_annualize_scales_by_365_over_days():
    # -25bps over 5 days annualizes to -25bps * 73 = -18.25
    apr = annualize_apr(net_return=-0.0025, days=5.0)
    assert apr == pytest.approx(-0.0025 * 365.0 / 5.0, abs=1e-12)


def test_annualize_zero_days_returns_nan_not_crash():
    assert np.isnan(annualize_apr(0.001, 0.0))


def test_sign_persistence_all_same_sign_is_one():
    assert sign_persistence(np.array([0.001, 0.002, 0.003])) == pytest.approx(1.0)


def test_sign_persistence_alternating_is_zero():
    assert sign_persistence(np.array([0.001, -0.001, 0.001, -0.001])) == pytest.approx(0.0)


def test_sign_persistence_half():
    # signs: + + - -> transitions: (+,+)=same, (+,-)=diff -> 1/2
    assert sign_persistence(np.array([0.001, 0.001, -0.001])) == pytest.approx(0.5)
