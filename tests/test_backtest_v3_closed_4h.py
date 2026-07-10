"""Audit 2026-07-10 finding A: backtest_v3's 4h join read a FORMING 4h bar.

`df_4h.index <= ts` selects the 4h bar whose label precedes the 1h decision
bar — but a 12:00-labeled 4h bar spans 12:00-16:00 and, after resampling the
full frame, already contains up to 3h of FUTURE 1h data at 13:00/14:00/15:00
decisions (3 of every 4 bars). That flatters every backtest_v3 replay.

closed_4h_bar pins the honest rule: a 4h bar starting at S is usable at the
decision time of 1h bar ts (its close, ts+1h) iff S+4h <= ts+1h, i.e.
S <= ts-3h.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_v3 import closed_4h_bar, resample_to_4h


def _frame(hours: int = 48) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=hours, freq="h")
    rng = np.random.RandomState(7)
    close = 100 + np.cumsum(rng.uniform(-0.5, 0.5, hours))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(hours, 1000.0),
        },
        index=idx,
    )


def test_forming_4h_bar_never_selected():
    df_4h = resample_to_4h(_frame())
    # 1h bars 13:00/14:00 sit INSIDE the 12:00-16:00 4h bar, which is still
    # forming at their decision times (14:00/15:00) -> must use 08:00.
    for hour, want in [(13, "08:00"), (14, "08:00"), (15, "12:00")]:
        ts = pd.Timestamp(f"2026-01-01 {hour}:00")
        row = closed_4h_bar(df_4h, ts)
        assert row is not None
        assert row.name == pd.Timestamp(f"2026-01-01 {want}"), (
            f"1h bar {hour}:00 must use the closed 4h bar {want}, got {row.name}"
        )


def test_boundary_bar_usable_exactly_at_close():
    df_4h = resample_to_4h(_frame())
    # 1h bar 15:00 closes at 16:00 == the 12:00 4h bar's close -> usable.
    row = closed_4h_bar(df_4h, pd.Timestamp("2026-01-01 15:00"))
    assert row is not None and row.name == pd.Timestamp("2026-01-01 12:00")


def test_no_closed_bar_returns_none():
    df_4h = resample_to_4h(_frame(8))
    assert closed_4h_bar(df_4h, pd.Timestamp("2026-01-01 01:00")) is None


def test_run_backtest_uses_closed_join():
    import inspect

    import backtest_v3 as bt

    src = inspect.getsource(bt.run_backtest)
    assert "closed_4h_bar(" in src, (
        "run_backtest must join 4h data via closed_4h_bar (no forming-bar reads)"
    )
    assert "df_4h.index <= ts" not in src, "the look-ahead join must not return"


def test_scan_replay_mirrors_closed_join():
    from pathlib import Path

    src = Path("research/scan_rsi_vol_replay.py").read_text(encoding="utf-8")
    assert 'np.timedelta64(3, "h")' in src, (
        "the scan's searchsorted 4h join must look up ts-3h (closed bars only)"
    )
