"""Offline tests for scripts/tv_crosscheck_ohlcv.py (TV vs exchange OHLCV diff)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "tv_crosscheck", ROOT / "scripts" / "tv_crosscheck_ohlcv.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CC = _load()


def _frame(ts0=1781000000, n=100, close0=100.0, step=3600):
    ts = np.arange(ts0, ts0 + n * step, step)
    close = close0 + np.arange(n) * 0.1
    return pd.DataFrame(
        {
            "ts": ts.astype("int64"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.ones(n),
        }
    )


def test_identical_frames_zero_diff():
    a = _frame()
    out = CC.compare_frames(a, a.copy())
    assert out["n_overlap"] == 100
    assert out["median_rel_diff_bps"] == 0.0
    assert out["max_rel_diff_bps"] == 0.0
    assert out["missing_in_tv"] == 0 and out["missing_in_ex"] == 0
    assert out["aligned"]


def test_constant_offset_measured_in_bps():
    a = _frame()
    b = a.copy()
    b["close"] = b["close"] * 1.0005  # +5 bps
    out = CC.compare_frames(b, a)
    assert abs(out["median_rel_diff_bps"] - 5.0) < 0.1


def test_missing_bars_counted():
    a = _frame(n=100)
    b = _frame(n=100).drop(index=[10, 11, 12]).reset_index(drop=True)
    out = CC.compare_frames(b, a)  # tv missing 3 bars that exchange has
    assert out["missing_in_tv"] == 3
    assert out["missing_in_ex"] == 0
    assert out["n_overlap"] == 97


def test_misaligned_boundaries_flagged():
    a = _frame()
    b = _frame(ts0=1781000000 + 1800)  # 30-min offset: zero common timestamps
    out = CC.compare_frames(b, a)
    assert out["n_overlap"] == 0
    assert not out["aligned"]
