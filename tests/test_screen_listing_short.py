"""Unit tests for the post-listing perp-short screen core math.

Pure-function tests on SYNTHETIC inputs only (no market data is synthesized for
the verdict — these exercise the deterministic calculations the screen relies on).
Run: venv/Scripts/python.exe -m pytest tests/test_screen_listing_short.py -v
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_listing_short import (  # noqa: E402
    find_backfill_clusters,
    funding_pnl_short,
    price_at,
    short_net_return,
)


def test_find_backfill_clusters_flags_shared_timestamps():
    firsts = {"A": 100, "B": 100, "C": 100, "D": 200, "E": 300, "F": 200}
    # 100 shared by 3 → cluster; 200 shared by 2 (< 3) → not; 300 unique → not
    clusters = find_backfill_clusters(firsts, min_shared=3)
    assert clusters == {100}


def test_find_backfill_clusters_empty_when_all_unique():
    firsts = {"A": 1, "B": 2, "C": 3}
    assert find_backfill_clusters(firsts, min_shared=3) == set()


def test_short_net_return_profit_on_decline():
    # short 100 -> cover 90; funding 0; fee 5bps + slip 5bps per side, 2 sides
    r = short_net_return(
        entry_price=100.0, exit_price=90.0, funding_sum=0.0,
        fee_per_side=0.0005, slip_per_side=0.0005,
    )
    # gross short = (100-90)/100 = 0.10 ; round-trip cost = 2*(0.0005+0.0005)=0.002
    assert r == pytest.approx(0.10 - 0.002, abs=1e-12)


def test_short_net_return_loss_on_rally():
    r = short_net_return(
        entry_price=100.0, exit_price=110.0, funding_sum=0.0,
        fee_per_side=0.0005, slip_per_side=0.0005,
    )
    # gross short = (100-110)/100 = -0.10 ; minus 0.002 cost
    assert r == pytest.approx(-0.10 - 0.002, abs=1e-12)


def test_negative_funding_is_a_cost_to_the_short():
    # flat price, no fees: a negative funding sum must REDUCE the short return
    # (short pays on negative-funding tokens — the pre-declared killer).
    r = short_net_return(
        entry_price=100.0, exit_price=100.0, funding_sum=-0.02,
        fee_per_side=0.0, slip_per_side=0.0,
    )
    assert r == pytest.approx(-0.02, abs=1e-12)


def test_positive_funding_is_income_to_the_short():
    r = short_net_return(
        entry_price=100.0, exit_price=100.0, funding_sum=+0.015,
        fee_per_side=0.0, slip_per_side=0.0,
    )
    assert r == pytest.approx(+0.015, abs=1e-12)


def test_funding_pnl_short_sums_rates_with_short_sign():
    # short receives +rate each settlement; net of a negative-dominant series is negative
    assert funding_pnl_short([0.0001, -0.0002, 0.0001]) == pytest.approx(0.0, abs=1e-12)
    assert funding_pnl_short([-0.001, -0.001]) == pytest.approx(-0.002, abs=1e-12)
    assert funding_pnl_short([]) == 0.0


def test_price_at_returns_first_bar_at_or_after_target():
    df = pd.DataFrame(
        {"ts": [1000, 2000, 3000, 4000], "close": [10.0, 20.0, 30.0, 40.0]}
    )
    # target between bars -> next bar
    price, actual = price_at(df, 2500)
    assert price == 30.0 and actual == 3000
    # exact match -> that bar
    price, actual = price_at(df, 2000)
    assert price == 20.0 and actual == 2000


def test_price_at_returns_none_past_end():
    df = pd.DataFrame({"ts": [1000, 2000], "close": [10.0, 20.0]})
    assert price_at(df, 5000) == (None, None)
