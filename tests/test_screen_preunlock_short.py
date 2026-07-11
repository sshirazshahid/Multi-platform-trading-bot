"""Offline tests for research/screen_preunlock_short.py pure functions."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_preunlock_short import (  # noqa: E402
    auc_score,
    build_mtm_curve,
    first_bar_at_or_after,
    floor_day,
    funding_print_nearest,
    max_drawdown,
    nearest_bar,
    rank_auc,
    window_bounds,
)

DAY = 24 * 3600
H = 3600


def _df(ts_prices):
    return pd.DataFrame({"ts": [t for t, _ in ts_prices],
                         "close": [p for _, p in ts_prices]})


def test_floor_day():
    # 2024-03-16 13:37 UTC -> 2024-03-16 00:00 UTC
    assert floor_day(1710596220) == 1710547200


def test_window_bounds_frozen():
    T = 1710547200 + 13 * H  # unlock at 13:00 UTC
    assert window_bounds(T, "W1") == (1710547200 - 28 * DAY, T)
    assert window_bounds(T, "W2") == (1710547200 - 14 * DAY, T)
    assert window_bounds(T, "W3") == (T, T + 3 * DAY)


def test_nearest_bar_picks_closest_open():
    df = _df([(1000 * H, 10.0), (1001 * H, 11.0), (1002 * H, 12.0)])
    # target 20 min past 1001h -> bar 1001h is nearest (not 1002h)
    px, ts = nearest_bar(df, 1001 * H + 1200)
    assert (px, ts) == (11.0, 1001 * H)
    # target 40 min past -> 1002h nearer
    px, ts = nearest_bar(df, 1001 * H + 2400)
    assert (px, ts) == (12.0, 1002 * H)
    # outside tolerance -> None
    assert nearest_bar(df, 1002 * H + 13 * H) == (None, None)
    assert nearest_bar(_df([]), 0) == (None, None)


def test_first_bar_at_or_after():
    df = _df([(1000 * H, 10.0), (1001 * H, 11.0)])
    assert first_bar_at_or_after(df, 1000 * H + 1) == (11.0, 1001 * H)
    assert first_bar_at_or_after(df, 1001 * H) == (11.0, 1001 * H)
    assert first_bar_at_or_after(df, 1002 * H) == (None, None)  # past end


def test_rank_auc_known_values():
    # perfectly discriminating
    assert rank_auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    # inverted
    assert rank_auc([4, 3, 2, 1], [False, False, True, True]) == 0.0
    # non-discriminating (all tied) -> 0.5 via average ranks
    assert rank_auc([1, 1, 1, 1], [False, True, False, True]) == 0.5
    # one class -> NaN
    assert np.isnan(rank_auc([1, 2], [True, True]))


def test_auc_score_monotone_in_ratio_and_funding():
    assert auc_score(0.5, 0.0) > auc_score(0.1, 0.0)
    assert auc_score(0.1, 0.001) > auc_score(0.1, -0.001)


def test_max_drawdown():
    assert max_drawdown(np.array([1.0, 1.1, 0.99, 1.2])) == (1.1 - 0.99) / 1.1
    assert max_drawdown(np.array([1.0, 1.1, 1.2])) == 0.0
    assert max_drawdown(np.array([])) == 0.0


def test_funding_print_nearest():
    s = pd.Series([0.01, -0.02], index=[100, 200])
    assert funding_print_nearest(s, 120) == 0.01
    assert funding_print_nearest(s, 180) == -0.02
    assert funding_print_nearest(pd.Series(dtype=float), 100) == 0.0


def test_build_mtm_curve_two_events_hand_check():
    """One short that wins (price halves), marked daily; MTM dips when price
    spikes mid-hold must register in the curve."""
    e0 = 1000 * DAY
    # daily bars at 00:00 (ts aligned to days)
    df = _df([(e0, 100.0), (e0 + DAY, 150.0), (e0 + 2 * DAY, 50.0)])
    pos = [{"sym": "X", "entry_ts": e0, "exit_ts": e0 + 2 * DAY, "entry_px": 100.0,
            "net_ret": 0.5 - 0.002, "fee": 0.0005, "slip": 0.0005,
            "fser": pd.Series(dtype=float)}]
    curve = build_mtm_curve(pos, {"X": df})
    # day at e0+DAY: short 100 -> 150 = -50% - 0.2% cost, scaled by 3% stake
    dd_day_val = 1.0 + 0.03 * (-0.5 - 0.002)
    assert any(abs(v - dd_day_val) < 1e-9 for v in curve)
    # final: realized +0.498 * 3%
    assert abs(curve[-1] - (1.0 + 0.03 * 0.498)) < 1e-9
    # drawdown reflects the adverse mark
    assert max_drawdown(curve) > 0.014
    assert build_mtm_curve([], {}).tolist() == [1.0]
