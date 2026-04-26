"""Phase 2.1: FeatureStore unit tests.

Covers:
- FEATURE_SCHEMA stability
- compute_features_from_ohlcv pure-function correctness on synthetic data
- All features finite floats
- Graceful degradation on short/missing OHLCV
- Warehouse round-trip (write -> read returns identical dict)
- INSERT OR REPLACE on duplicate keys (re-write updates, not duplicates)
- Deterministic feature_hash
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fs_mod():
    return _load("feature_store_t", "core/feature_store.py")


@pytest.fixture
def wh(tmp_path):
    return _load("warehouse_fs_t", "core/warehouse.py").Warehouse(
        path=tmp_path / "fs.sqlite")


def _synth_ohlcv(n=120, seed=42, drift=0.0):
    """Generate synthetic OHLCV with optional drift; index is integer steps."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(drift, 1.0, n))
    highs = closes + rng.uniform(0, 0.8, n)
    lows = closes - rng.uniform(0, 0.8, n)
    opens = closes + rng.normal(0, 0.3, n)
    vols = rng.uniform(1e3, 5e3, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def test_schema_is_a_list_of_strings(fs_mod):
    s = fs_mod.FEATURE_SCHEMA
    assert isinstance(s, list)
    assert all(isinstance(name, str) for name in s)
    assert len(s) >= 8, "MVP schema should include 8 technical features"


def test_compute_returns_full_schema_keys(fs_mod):
    ohlcv = {
        "4h": _synth_ohlcv(120, seed=1),
        "1h": _synth_ohlcv(120, seed=2),
        "15m": _synth_ohlcv(120, seed=3),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    for name in fs_mod.FEATURE_SCHEMA:
        assert name in feats, f"missing {name}"
        v = feats[name]
        assert isinstance(v, float), f"{name} must be float, got {type(v)}"
        assert np.isfinite(v), f"{name} must be finite, got {v}"


def test_compute_uptrend_yields_positive_ema_gap(fs_mod):
    """Strong upward drift → ema_gap_4h > 0 (fast above slow)."""
    ohlcv = {
        "4h": _synth_ohlcv(120, seed=1, drift=0.5),
        "1h": _synth_ohlcv(120, seed=2, drift=0.5),
        "15m": _synth_ohlcv(120, seed=3, drift=0.5),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    assert feats["ema_gap_4h"] > 0
    assert feats["ema_align_1h"] > 0  # +1 when EMA20 > EMA50


def test_compute_downtrend_yields_negative_ema_gap(fs_mod):
    ohlcv = {
        "4h": _synth_ohlcv(120, seed=1, drift=-0.5),
        "1h": _synth_ohlcv(120, seed=2, drift=-0.5),
        "15m": _synth_ohlcv(120, seed=3, drift=-0.5),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    assert feats["ema_gap_4h"] < 0
    assert feats["ema_align_1h"] < 0


def test_compute_handles_short_ohlcv_gracefully(fs_mod):
    """Short series shouldn't NaN-crash; returns 0/sensible defaults."""
    ohlcv = {
        "4h": _synth_ohlcv(5, seed=1),
        "1h": _synth_ohlcv(5, seed=2),
        "15m": _synth_ohlcv(5, seed=3),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    for name in fs_mod.FEATURE_SCHEMA:
        v = feats[name]
        assert np.isfinite(v), f"{name} must be finite even on short OHLCV; got {v}"


def test_compute_missing_timeframe_uses_defaults(fs_mod):
    """If '15m' is absent, vol_ratio_15m falls back to a default (1.0)."""
    ohlcv = {
        "4h": _synth_ohlcv(120, seed=1),
        "1h": _synth_ohlcv(120, seed=2),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    assert "vol_ratio_15m" in feats
    assert np.isfinite(feats["vol_ratio_15m"])


def test_write_read_roundtrip(fs_mod, wh):
    fs = fs_mod.FeatureStore(wh)
    feats = {"ema_gap_4h": 0.012, "rsi_14_1h": 55.5, "adx_14_1h": 25.0}
    fs.write(ts=1000, symbol="BTC/USDT:USDT", timeframe="multi", features=feats)
    out = fs.read("BTC/USDT:USDT")
    assert 1000 in out
    assert out[1000] == pytest.approx(feats)


def test_write_replaces_on_duplicate_key(fs_mod, wh):
    """Re-writing the same (ts, symbol, tf, name) updates the value."""
    fs = fs_mod.FeatureStore(wh)
    fs.write(ts=2000, symbol="ETH/USDT", timeframe="multi",
             features={"rsi_14_1h": 50.0})
    fs.write(ts=2000, symbol="ETH/USDT", timeframe="multi",
             features={"rsi_14_1h": 70.0})
    out = fs.read("ETH/USDT")
    assert out[2000]["rsi_14_1h"] == 70.0
    rows = wh.query("SELECT COUNT(*) c FROM features WHERE symbol='ETH/USDT'")
    assert rows[0]["c"] == 1


def test_read_filters_by_ts_range(fs_mod, wh):
    fs = fs_mod.FeatureStore(wh)
    for t in (100, 200, 300, 400):
        fs.write(ts=t, symbol="X/USDT", timeframe="multi",
                 features={"adx_14_1h": float(t)})
    out = fs.read("X/USDT", ts_start=200, ts_end=300)
    assert set(out.keys()) == {200, 300}


def test_feature_hash_deterministic(fs_mod):
    f1 = {"ema_gap_4h": 0.01, "rsi_14_1h": 55.0}
    f2 = {"rsi_14_1h": 55.0, "ema_gap_4h": 0.01}  # same content, different order
    assert fs_mod.feature_hash(f1) == fs_mod.feature_hash(f2)
    f3 = {"ema_gap_4h": 0.02, "rsi_14_1h": 55.0}
    assert fs_mod.feature_hash(f1) != fs_mod.feature_hash(f3)


def test_feature_hash_is_short_hex(fs_mod):
    h = fs_mod.feature_hash({"a": 1.0})
    assert isinstance(h, str)
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_and_write_integration(fs_mod, wh):
    """End-to-end: compute features from OHLCV, write to warehouse, read back."""
    fs = fs_mod.FeatureStore(wh)
    ohlcv = {
        "4h": _synth_ohlcv(120, seed=1),
        "1h": _synth_ohlcv(120, seed=2),
        "15m": _synth_ohlcv(120, seed=3),
    }
    feats = fs_mod.compute_features_from_ohlcv(ohlcv)
    fs.write(ts=5000, symbol="BTC/USDT", timeframe="multi", features=feats)
    out = fs.read("BTC/USDT", ts_start=5000, ts_end=5000)
    assert out[5000] == pytest.approx(feats, rel=1e-9)
