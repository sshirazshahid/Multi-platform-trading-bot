"""Offline tests for scripts/run_tv_macro_screen.py (tradfi->crypto alignment).

The shared gate/signal harness is already covered by test_tv_regime_screen.py;
these target the NEW component: trading-day -> crypto-calendar alignment with
the pre-registered one-day causality lag.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "tv_macro_screen", ROOT / "scripts" / "run_tv_macro_screen.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MS = _load()


def _crypto_week():
    # Mon 2024-01-01 .. Sun 2024-01-07 (7d/week crypto calendar)
    return pd.date_range("2024-01-01", periods=7, freq="D", tz="UTC")


def test_align_macro_weekend_ffill_and_lag():
    # macro closes Mon-Fri only
    macro = pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        index=pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC"),
    )
    out = MS.align_macro(macro, _crypto_week())
    # one-day lag: crypto Tue gets Mon's close; Mon (no prior macro) is NaN
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == 1.0  # Tue <- Mon
    assert out.iloc[4] == 4.0  # Fri <- Thu
    assert out.iloc[5] == 5.0  # Sat <- Fri (ffill then lag)
    assert out.iloc[6] == 5.0  # Sun <- Fri


def test_align_macro_causality_change_propagates_forward_only():
    idx = _crypto_week()
    base = pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        index=pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC"),
    )
    bumped = base.copy()
    bumped.iloc[2] = 99.0  # change Wednesday's close
    a, b = MS.align_macro(base, idx), MS.align_macro(bumped, idx)
    # crypto days <= Wed must be identical; Thu onward may differ
    assert a.iloc[:3].fillna(-1).tolist() == b.iloc[:3].fillna(-1).tolist()
    assert b.iloc[3] == 99.0  # Thu <- Wed


def test_align_macro_intraday_stamps_normalized():
    # macro bars stamped at a session hour (e.g. 13:30 UTC) still map by date
    macro = pd.Series(
        [10.0, 20.0],
        index=pd.DatetimeIndex(["2024-01-01 13:30", "2024-01-02 13:30"], tz="UTC"),
    )
    out = MS.align_macro(macro, _crypto_week())
    assert out.iloc[1] == 10.0
    assert out.iloc[2] == 20.0


def test_grid_is_preregistered_shape():
    assert len(MS.VARIANTS) == 7
    names = [v["name"] for v in MS.VARIANTS]
    assert len(set(names)) == 7
    assert MS.N_TRIALS == 15  # 7 (this grid) + 8 (dominance regime grid)
    assert len(MS.SERIES) == 5
