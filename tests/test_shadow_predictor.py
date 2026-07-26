"""Phase 13.5 — ShadowPredictor invariants.

Tests cover:
  - Returns None when no model artifact exists (graceful degradation)
  - Computes p_win correctly when model is loaded
  - Writes predictions row to warehouse
  - Coerces missing/None feature values to 0
  - Idempotency: same (ts, model_version, symbol, side) replaces row
  - Feature hash is deterministic across calls with same content
"""
from __future__ import annotations

import pytest

pytest.importorskip("joblib")
pytest.importorskip("sklearn")

import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]

FEATURE_KEYS = [
    "score", "layers_ok", "confidence", "sl_pct", "tp_pct",
    "rsi_1h", "adx_1h", "adx_4h", "atr_pct_1h", "bb_width_4h",
    "vol_ratio", "ema20_above_50_4h", "ema20_above_50_1h",
    "funding_rate", "ob_imbalance",
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Each test gets an isolated cwd + warehouse + (optional) model artifact."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "models").mkdir()
    sys.path.insert(0, str(ROOT))
    # Force fresh imports so module-level Paths use new cwd
    for m in [
        "core.shadow_predictor", "core.warehouse", "core.models",
        "scripts.train_models",
    ]:
        sys.modules.pop(m, None)
    sp_mod = importlib.import_module("core.shadow_predictor")
    wh_mod = importlib.import_module("core.warehouse")
    models_mod = importlib.import_module("core.models")
    wh = wh_mod.Warehouse(path=tmp_path / "data" / "wh.sqlite")
    monkeypatch.setattr(wh_mod, "_default", wh)
    return sp_mod, wh_mod, models_mod, wh, tmp_path


def _train_tiny_model(models_mod, tmp_path):
    """Fit a 15-feature LRModel on synthetic data, save to lr_v_latest.pkl."""
    rng = np.random.default_rng(0)
    n = 200
    n_features = len(FEATURE_KEYS)
    X = rng.normal(0, 1, size=(n, n_features))
    y = (X[:, 0] + X[:, 1] > 0).astype(int)
    m = models_mod.LRModel().fit(X, y)
    out = tmp_path / "data" / "models" / "lr_v_latest.pkl"
    m.save(out, feature_keys=FEATURE_KEYS)
    return m, out


def test_predictor_inactive_when_no_model(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    pred = sp_mod.ShadowPredictor()
    p = pred.predict_p_win({"score": 80})
    assert p is None
    assert pred.log_entry(
        ts=1234, symbol="X/USDT", side="buy", features={}) is None


def test_predict_p_win_returns_float_when_model_loaded(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)
    pred = sp_mod.ShadowPredictor()
    feats = {k: 1.0 for k in FEATURE_KEYS}
    p = pred.predict_p_win(feats)
    assert p is not None
    assert 0.0 <= p <= 1.0


def test_log_entry_writes_to_warehouse(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)
    pred = sp_mod.ShadowPredictor()
    feats = {k: 0.5 for k in pred._ensure_model() and pred._feature_keys or []}
    p = pred.log_entry(
        ts=1000, symbol="ATOM/USDT:USDT", side="buy",
        features=feats, warehouse=wh)
    assert p is not None
    rows = wh.query(
        "SELECT * FROM predictions WHERE symbol=? AND ts=?",
        ("ATOM/USDT:USDT", 1000))
    assert len(rows) == 1
    assert rows[0]["side"] == "buy"
    assert 0.0 <= rows[0]["p_win"] <= 1.0
    assert rows[0]["feature_hash"]


def test_missing_feature_coerced_to_zero(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)
    pred = sp_mod.ShadowPredictor()
    # Pass empty features — every feature defaults to 0
    p = pred.log_entry(
        ts=2000, symbol="X/USDT", side="buy", features={}, warehouse=wh)
    assert p is not None  # still produces a prediction; just on the zero vector


def test_idempotent_replay(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)
    pred = sp_mod.ShadowPredictor()
    pred.log_entry(ts=3000, symbol="X/USDT", side="buy",
                   features={"score": 75}, warehouse=wh)
    pred.log_entry(ts=3000, symbol="X/USDT", side="buy",
                   features={"score": 80}, warehouse=wh)  # different features
    rows = wh.query("SELECT * FROM predictions WHERE ts=?", (3000,))
    assert len(rows) == 1, "PK on (ts, model_version, symbol, side) — must dedupe"


def test_does_not_propagate_warehouse_errors(env, monkeypatch):
    """If warehouse insert fails, predictor must still return p_win and not raise."""
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)

    class BrokenWarehouse:
        def _conn(self):
            raise RuntimeError("simulated DB outage")

    pred = sp_mod.ShadowPredictor()
    p = pred.log_entry(
        ts=4000, symbol="X/USDT", side="buy",
        features={"score": 70}, warehouse=BrokenWarehouse())
    assert p is not None  # prediction still happened — only persistence failed


def test_singleton_get(env):
    sp_mod, _, _, _, _ = env
    p1 = sp_mod.ShadowPredictor.get()
    p2 = sp_mod.ShadowPredictor.get()
    assert p1 is p2


def test_feature_hash_stable(env):
    sp_mod, wh_mod, models_mod, wh, tmp_path = env
    _train_tiny_model(models_mod, tmp_path)
    pred = sp_mod.ShadowPredictor()
    pred.log_entry(ts=5000, symbol="A/USDT", side="buy",
                   features={"score": 80, "rsi_1h": 50.0}, warehouse=wh)
    pred.log_entry(ts=5001, symbol="A/USDT", side="buy",
                   features={"rsi_1h": 50.0, "score": 80}, warehouse=wh)  # reordered
    rows = wh.query(
        "SELECT feature_hash FROM predictions WHERE ts IN (5000, 5001)")
    assert len(rows) == 2
    assert rows[0]["feature_hash"] == rows[1]["feature_hash"]
