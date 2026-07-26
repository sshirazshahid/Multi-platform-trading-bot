"""Unit tests for C3 quarter-hour imbalance screen helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from research.screen_c3_quarter_hour_imbalance import (
    boundary_ms,
    expanding_residuals,
    side_net_return,
    trade_side,
)


def test_boundary_ms_on_quarter_hours():
    dt = datetime(2026, 4, 1, 14, 17, 5, tzinfo=timezone.utc)
    b = boundary_ms(int(dt.timestamp() * 1000))
    got = datetime.fromtimestamp(b / 1000, tz=timezone.utc)
    assert got.minute == 15 and got.second == 0


def test_trade_side_aligned_and_contrarian():
    assert trade_side("aligned", 0.5) == "long"
    assert trade_side("aligned", -0.1) == "short"
    assert trade_side("contrarian", 0.5) == "short"
    assert trade_side("contrarian", -0.1) == "long"
    assert trade_side("aligned", 0.0) is None


def test_side_net_return_long_with_costs():
    r = side_net_return("long", 100.0, 101.0, 0.0, 0.0005, 0.0005)
    assert abs(r - (0.01 - 0.002)) < 1e-9


def test_expanding_residuals_finite():
    rng = np.random.default_rng(1)
    raw = rng.normal(size=300)
    X = rng.normal(size=(300, 3))
    y = raw + 0.5 * X[:, 0]
    resid = expanding_residuals(y, X, min_history=50)
    assert np.isfinite(resid[250:]).sum() > 40
