"""Tests for the cross-venue funding-dispersion screen core math.

TDD: these pin the load-bearing cost/carry arithmetic before the screen runs.
Pure functions only — no file or network I/O is exercised here.
"""
import numpy as np
import pytest

import research.screen_funding_dispersion as disp
from research.screen_funding_dispersion import (
    _amortize_holds,
    annualize_apr,
    breakeven_settlements,
    coins_with_cross_venue_coverage,
    load_venue_funding,
    net_return_frac,
    roundtrip_cost_frac,
    sign_persistence,
    signed_carry,
    walk_forward_oos_spread,
    walk_forward_oos_spread_hold,
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


# ── rev2: funding_history reader path (synthetic fixtures in tmp dirs) ────────
def _write_history(d, venue, coin, ts, rates):
    import pandas as pd
    p = d / f"{venue}_{coin}.csv"
    pd.DataFrame({"ts": ts, "funding_rate": rates, "venue": venue,
                  "symbol": f"{coin}/USDT:USDT"}).to_csv(p, index=False)


def _write_carry(d, venue, coin, next_ts, rates):
    import pandas as pd
    p = d / f"{venue}_{coin}.csv"
    pd.DataFrame({"ts": next_ts, "rate": rates, "interval_hours": 8.0,
                  "next_funding_ts": next_ts}).to_csv(p, index=False)


def test_load_venue_funding_prefers_funding_history(tmp_path, monkeypatch):
    hist = tmp_path / "history"; hist.mkdir()
    carry = tmp_path / "carry"; carry.mkdir()
    monkeypatch.setattr(disp, "_HISTORY_DIR", str(hist))
    monkeypatch.setattr(disp, "_CARRY_DIR", str(carry))
    _write_history(hist, "binance", "BTC", [100, 108, 116], [0.0001, 0.0002, -0.0001])
    _write_carry(carry, "binance", "BTC", [100, 108], [9.9, 9.9])  # should be ignored
    s = load_venue_funding("BTC", "binance")
    assert list(s.index) == [100, 108, 116]
    np.testing.assert_allclose(s.values, [0.0001, 0.0002, -0.0001])


def test_load_venue_funding_falls_back_to_carry_when_no_history(tmp_path, monkeypatch):
    hist = tmp_path / "history"; hist.mkdir()  # empty
    carry = tmp_path / "carry"; carry.mkdir()
    monkeypatch.setattr(disp, "_HISTORY_DIR", str(hist))
    monkeypatch.setattr(disp, "_CARRY_DIR", str(carry))
    _write_carry(carry, "bybit", "ETH", [200, 208], [0.00005, 0.00006])
    s = load_venue_funding("ETH", "bybit")
    assert list(s.index) == [200, 208]
    np.testing.assert_allclose(s.values, [0.00005, 0.00006])


def test_coverage_prefers_history_and_lists_cross_venue_coin(tmp_path, monkeypatch):
    hist = tmp_path / "history"; hist.mkdir()
    carry = tmp_path / "carry"; carry.mkdir()
    monkeypatch.setattr(disp, "_HISTORY_DIR", str(hist))
    monkeypatch.setattr(disp, "_CARRY_DIR", str(carry))
    ts = list(range(1000, 1000 + 8 * 10, 8))
    _write_history(hist, "binance", "SOL", ts, [0.0001] * len(ts))
    _write_history(hist, "bybit", "SOL", ts, [0.00005] * len(ts))
    # ADA only on one venue in history -> not cross-venue
    _write_history(hist, "binance", "ADA", ts, [0.0001] * len(ts))
    assert coins_with_cross_venue_coverage() == ["SOL"]


# ── rev2: walk-forward OOS carry (no lookahead + cost charged) ────────────────
def test_walk_forward_oos_direction_from_train_not_test():
    # neg regime then pos regime; train sign is negative through the later folds,
    # so the pos-regime test settlements are shorted the WRONG way -> negative.
    # A lookahead impl (peeking at the test-fold sign) would profit there instead.
    spread = np.concatenate([np.full(60, -0.001), np.full(40, +0.001)])
    oos = walk_forward_oos_spread(spread, rt_cost=0.0, n_splits=4, embargo=0)
    assert oos.size == 80  # test folds cover indices 20..99
    assert int((oos > 0).sum()) == 40  # neg-regime test settlements (correct side)
    assert int((oos < 0).sum()) == 40  # pos-regime test settlements (ate the flip)


def test_walk_forward_oos_persistent_spread_clears_cost_with_large_folds():
    spread = np.full(400, 0.0003)  # persistent +3bps/settlement
    oos = walk_forward_oos_spread(spread, rt_cost=0.0042, n_splits=4)
    # test folds ~80 settlements -> amortized cost ~0.525bps << 3bps -> net positive
    assert float(np.mean(oos)) > 0
    assert float(np.mean(oos > 0)) == pytest.approx(1.0)


def test_walk_forward_oos_cost_dominates_small_folds():
    spread = np.full(40, 0.0001)  # persistent +1bp/settlement, tiny folds
    oos = walk_forward_oos_spread(spread, rt_cost=0.0042, n_splits=4)
    # test folds ~8 settlements -> amortized cost ~5.25bps >> 1bp -> net negative
    assert float(np.mean(oos)) < 0


def test_walk_forward_oos_empty_when_too_short():
    assert walk_forward_oos_spread(np.array([0.0001, 0.0002]), 0.0042).size == 0


# ── rev3: hold-until-sign-flip cost model (fixes audit A1) ────────────────────
def test_amortize_one_roundtrip_per_held_position():
    # constant direction -> ONE position -> total cost == one round-trip
    cost = _amortize_holds(np.array([1.0, 1.0, 1.0, 1.0]), rt_cost=0.004)
    assert cost.sum() == pytest.approx(0.004, abs=1e-12)
    np.testing.assert_allclose(cost, [0.001, 0.001, 0.001, 0.001])


def test_amortize_charges_a_roundtrip_on_each_direction_flip():
    # two contiguous runs (+ then -) -> TWO positions -> total == 2 round-trips,
    # each amortized evenly within its own run.
    cost = _amortize_holds(np.array([1.0, 1.0, -1.0, -1.0, 1.0]), rt_cost=0.004)
    assert cost.sum() == pytest.approx(0.012, abs=1e-12)  # 3 runs -> 3 round-trips
    np.testing.assert_allclose(cost, [0.002, 0.002, 0.002, 0.002, 0.004])


def test_amortize_empty_is_empty():
    assert _amortize_holds(np.array([]), 0.004).size == 0


def test_hold_model_charges_exactly_one_roundtrip_when_direction_constant():
    # persistent +3bps spread -> train dir is +1 in every fold -> ONE held position
    # -> total OOS cost == exactly ONE round-trip regardless of fold count.
    spread = np.full(400, 0.0003)
    for n_splits in (4, 8):
        oos = walk_forward_oos_spread_hold(spread, rt_cost=0.0042, n_splits=n_splits)
        gross = 0.0003 * oos.size  # direction is +1 so carry == +spread each settle
        total_cost = gross - float(oos.sum())
        assert total_cost == pytest.approx(0.0042, abs=1e-9)  # one round-trip, both fold counts
        assert float(oos.mean()) > 0  # amortized cost << carry -> net positive


def test_hold_model_direction_from_train_not_test_no_lookahead():
    # neg regime then pos regime; every train fold picks dir=-1 (anchored, early
    # negatives dominate) and applies it UNSEEN. With zero cost the pos-regime test
    # settlements are shorted the WRONG way -> negative, exactly like the per-fold
    # impl. A lookahead impl would profit there instead.
    spread = np.concatenate([np.full(60, -0.001), np.full(40, +0.001)])
    oos = walk_forward_oos_spread_hold(spread, rt_cost=0.0, n_splits=4, embargo=0)
    assert oos.size == 80
    assert int((oos > 0).sum()) == 40  # neg-regime test settlements (correct side)
    assert int((oos < 0).sum()) == 40  # pos-regime test settlements (ate the flip)


def test_hold_model_empty_when_too_short():
    assert walk_forward_oos_spread_hold(np.array([0.0001, 0.0002]), 0.0042).size == 0
