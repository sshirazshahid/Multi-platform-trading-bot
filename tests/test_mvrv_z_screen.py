"""Causality + logic tests for the MVRV-Z honest-gate screen (2026-06-28).

The deep-research pass flagged on-chain backtests as especially prone to
look-ahead. These tests pin the decision rule's causality: position[t] depends
only on z[<=t], so a change to a FUTURE z can never alter an earlier position.
"""
import numpy as np

from scripts.run_mvrv_z_screen import episodes_to_trades, strat_daily


def test_enters_on_buy_exits_on_sell():
    z = np.array([1.0, -0.5, 0.0, 5.0, 1.0])  # buy at idx1 (<=0), sell at idx3 (>=4)
    ret = np.zeros(5)
    _, pos = strat_daily(z, ret, z_buy=0.0, z_sell=4.0, cost_frac=0.0)
    assert list(pos) == [0, 1, 1, 0, 0]


def test_no_lookahead_future_z_cannot_change_past_position():
    z1 = np.array([1.0, -0.5, 0.0, 1.0, 1.0])
    z2 = z1.copy()
    z2[4] = 5.0  # change ONLY the last day
    ret = np.zeros(5)
    _, p1 = strat_daily(z1, ret, 0.0, 4.0, 0.0)
    _, p2 = strat_daily(z2, ret, 0.0, 4.0, 0.0)
    assert list(p1[:4]) == list(p2[:4])  # earlier positions identical


def test_position_earns_same_day_aligned_next_day_return_with_cost():
    z = np.array([-1.0, -1.0])  # long both days
    ret = np.array([0.10, 0.0])
    sret, pos = strat_daily(z, ret, z_buy=0.0, z_sell=9.0, cost_frac=0.001)
    assert list(pos) == [1, 1]
    assert sret[0] == 0.10 - 0.001  # one entry cost charged on the 0->1 turn
    assert sret[1] == 0.0


def test_held_episode_is_one_trade():
    z = np.array([1.0, -0.5, 0.0, 0.0])  # buy idx1, never sells
    ret = np.array([0.0, 0.1, 0.1, 0.0])
    _, pos = strat_daily(z, ret, z_buy=0.0, z_sell=9.0, cost_frac=0.0)
    trades = episodes_to_trades(pos, ret, ["2022-01-01", "2022-01-02", "2022-01-03", "2022-01-04"])
    assert len(trades) == 1
    assert trades[0]["pnl"] > 0
