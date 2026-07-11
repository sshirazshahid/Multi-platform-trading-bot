"""Offline tests for research/screen_basis_swap_eth.py pure functions."""
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_basis_swap_eth import (  # noqa: E402
    apr_basis,
    apr_funding,
    decision_points,
    expiry_schedule,
    last_friday_expiry,
    next_expiry,
    trailing_mean_before,
)


def _ts(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp())


def test_last_friday_expiry_known_dates():
    # Binance quarterly expiries (last Friday, 08:00 UTC)
    assert last_friday_expiry(2024, 3) == _ts(2024, 3, 29)
    assert last_friday_expiry(2024, 6) == _ts(2024, 6, 28)
    assert last_friday_expiry(2021, 6) == _ts(2021, 6, 25)
    assert last_friday_expiry(2025, 12) == _ts(2025, 12, 26)
    assert last_friday_expiry(2026, 6) == _ts(2026, 6, 26)


def test_expiry_schedule_and_next_expiry():
    sched = expiry_schedule()
    assert sched == sorted(sched)
    assert len(sched) == 24  # 2021..2026 x 4
    e = next_expiry(_ts(2024, 4, 1, 0), sched)
    assert e == _ts(2024, 6, 28)
    assert next_expiry(sched[-1] + 1, sched) is None


def test_decision_points_span():
    dps = decision_points()
    assert dps[0] == int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp())
    assert dps[-1] == int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    assert len(dps) == 64  # 2021-03 .. 2026-06 inclusive


def test_aprs():
    # 1% basis with 91.25 days to expiry -> 4% APR
    assert abs(apr_basis(0.01, 91.25) - 0.04) < 1e-12
    # 0.01% per settlement -> 3 x 365 x 0.0001 = 10.95% APR
    assert abs(apr_funding(0.0001) - 0.1095) < 1e-12


def test_trailing_mean_before_strict():
    ts = np.array([100, 200, 300], dtype="int64")
    rates = np.array([0.01, 0.02, 0.09])
    # entry exactly at a print ts excludes that print
    assert abs(trailing_mean_before(ts, rates, 300, k=21) - 0.015) < 1e-12
    assert trailing_mean_before(ts, rates, 100, k=21) is None
    # k window limits lookback
    assert abs(trailing_mean_before(ts, rates, 301, k=1) - 0.09) < 1e-12
