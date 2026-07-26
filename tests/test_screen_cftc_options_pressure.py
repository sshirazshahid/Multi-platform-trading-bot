"""Unit tests for C1 CFTC options-pressure screen pure helpers."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from research.screen_cftc_options_pressure import (
    asset_mgr_net,
    options_only_net,
    release_ts_utc,
    residualize_against_lags,
    side_net_return,
)


def test_asset_mgr_and_options_only_net():
    assert asset_mgr_net(100, 40) == 60.0
    assert options_only_net(80.0, 60.0) == 20.0


def test_residualize_removes_linear_lag_content():
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 2.0 * x + rng.normal(scale=0.01, size=200)
    resid = residualize_against_lags(y, x)
    # residuals should be near zero mean and uncorrelated with x
    mask = np.isfinite(resid)
    assert abs(float(np.corrcoef(resid[mask], x[mask])[0, 1])) < 0.05


def test_release_ts_friday_after_tuesday():
    # 2024-01-02 was a Tuesday; Friday 2024-01-05 15:30 NY
    rd = datetime(2024, 1, 2, 12, 0, tzinfo=ZoneInfo("UTC"))
    ts = release_ts_utc(rd)
    got = datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York"))
    assert got.weekday() == 4  # Friday
    assert got.hour == 15 and got.minute == 30


def test_side_net_return_charges_roundtrip_and_funding():
    # long: price +1%, funding sum +0.001 → long pays 10bps; fee+slip 5+5 bps/side = 20bps RT
    r = side_net_return("long", 100.0, 101.0, 0.001, 0.0005, 0.0005)
    assert abs(r - (0.01 - 0.001 - 0.002)) < 1e-9
    # short receives funding
    r2 = side_net_return("short", 100.0, 99.0, 0.001, 0.0005, 0.0005)
    assert abs(r2 - (0.01 + 0.001 - 0.002)) < 1e-9
