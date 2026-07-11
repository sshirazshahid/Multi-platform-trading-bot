"""Offline tests for research/screen_funding_timing.py pure functions."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_funding_timing import (  # noqa: E402
    OFFSETS,
    event_deltas,
    premium_close_at,
    qualifies,
    trailing_mean,
)


def test_premium_close_at_uses_bar_closing_at_t():
    prem = {1000: 0.0001, 1900: 0.0002}
    # bar CLOSING at t has open t-900
    assert premium_close_at(prem, 1900) == 0.0001
    assert premium_close_at(prem, 2800) == 0.0002
    assert premium_close_at(prem, 1000) is None


def test_offsets_frozen_grid():
    assert OFFSETS == {"B": -6300, "V1": -900, "V2": -1800, "V3": -3600, "V4": 900}


def test_trailing_mean_strictly_before_s0():
    ts = np.array([0, 28800, 57600, 86400], dtype="int64")
    rates = np.array([0.01, 0.02, 0.03, 99.0])
    m, cnt = trailing_mean(ts, rates, 3)
    # print at index 3 itself must NOT be included
    assert cnt == 3 and abs(m - 0.02) < 1e-12
    m, cnt = trailing_mean(ts, rates, 0)
    assert m is None and cnt == 0


def test_qualifies_frozen_arithmetic():
    # binance cost 0.0050 -> threshold = max(15, 150) = 150 bps over 21 settlements
    # trailing mean 0.001/settlement -> gross 210bps - 50 = 160bps net >= 150 -> pass
    assert qualifies(0.0001, 0.001, 0.0050)
    # mean 0.0009 -> 189-50 = 139 < 150 -> fail
    assert not qualifies(0.0001, 0.0009, 0.0050)
    # negative current rate always fails
    assert not qualifies(-0.0001, 0.01, 0.0050)
    # negative trailing mean fails
    assert not qualifies(0.0001, -0.001, 0.0050)


def test_event_deltas_frozen_vs_corrected_sign():
    prems = {"B": 0.0010, "V1": 0.0015, "V2": 0.0010, "V3": 0.0008, "V4": 0.0002}
    rate_s0 = 0.0003
    frozen, corrected = event_deltas(rate_s0, prems)
    # V1 entered at RICHER basis (+5bps vs B): corrected credits it, frozen debits it
    assert abs(corrected["V1"] - 0.0005) < 1e-12
    assert abs(frozen["V1"] + 0.0005) < 1e-12
    # V2 identical basis -> 0 both
    assert frozen["V2"] == corrected["V2"] == 0.0
    # V4 misses funding(s0) in BOTH conventions
    assert abs(frozen["V4"] - (-rate_s0 - (0.0002 - 0.0010))) < 1e-12
    assert abs(corrected["V4"] - (-rate_s0 + (0.0002 - 0.0010))) < 1e-12
    # frozen and corrected are mirror images around the funding delta
    for v in ("V1", "V2", "V3"):
        assert abs(frozen[v] + corrected[v]) < 1e-12
