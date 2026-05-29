"""Phase 13.2 — LRModel wrapper invariants.

Wraps sklearn LogisticRegression + StandardScaler with joblib persistence.
Tests cover: shape contract, value range, reproducibility under fixed seed,
roundtrip via joblib, edge cases on degenerate inputs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("joblib")
pytest.importorskip("sklearn")

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "models_test", ROOT / "core" / "models.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["models_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def models():
    return _load()


@pytest.fixture
def synthetic_dataset():
    """Linearly separable-ish binary problem so LR can find signal."""
    rng = np.random.default_rng(seed=42)
    n = 400
    X = rng.normal(0, 1, size=(n, 5))
    # Decision boundary = sum of first 2 features + noise
    logits = X[:, 0] + 0.5 * X[:, 1] + rng.normal(0, 0.3, size=n)
    y = (logits > 0).astype(int)
    return X, y


# ── API shape ──────────────────────────────────────────────────────────

def test_predict_proba_shape(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    p = m.predict_proba(X)
    assert p.shape == (X.shape[0], 2)


def test_predict_proba_in_unit_interval(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    p = m.predict_proba(X)
    assert np.all(p >= 0)
    assert np.all(p <= 1)


def test_predict_proba_rows_sum_to_1(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    p = m.predict_proba(X)
    np.testing.assert_allclose(p.sum(axis=1), np.ones(X.shape[0]), atol=1e-9)


def test_p_win_helper_returns_class1_column(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    p_full = m.predict_proba(X)
    p_win = m.p_win(X)
    np.testing.assert_array_equal(p_win, p_full[:, 1])


# ── Reproducibility ────────────────────────────────────────────────────

def test_fit_is_seed_reproducible(models, synthetic_dataset):
    X, y = synthetic_dataset
    m1 = models.LRModel(random_state=42).fit(X, y)
    m2 = models.LRModel(random_state=42).fit(X, y)
    np.testing.assert_allclose(
        m1.predict_proba(X), m2.predict_proba(X), atol=1e-12)


# ── Persistence ────────────────────────────────────────────────────────

def test_save_load_roundtrip(models, synthetic_dataset, tmp_path):
    X, y = synthetic_dataset
    m1 = models.LRModel().fit(X, y)
    p1 = m1.predict_proba(X)
    out = tmp_path / "lr_test.pkl"
    m1.save(out)
    assert out.exists()
    m2 = models.LRModel.load(out)
    p2 = m2.predict_proba(X)
    np.testing.assert_allclose(p1, p2, atol=1e-12)


def test_load_raises_on_missing_file(models, tmp_path):
    with pytest.raises(FileNotFoundError):
        models.LRModel.load(tmp_path / "does-not-exist.pkl")


# ── Standardization ────────────────────────────────────────────────────

def test_scaler_normalizes_inputs(models, synthetic_dataset):
    """Internal StandardScaler transforms a feature with mean 100 / std 50
    to roughly zero-mean / unit-std. Otherwise LR fit converges poorly."""
    X, y = synthetic_dataset
    X[:, 0] = X[:, 0] * 50 + 100  # huge scale
    m = models.LRModel().fit(X, y)
    transformed = m._scaler.transform(X)
    assert abs(transformed[:, 0].mean()) < 0.1
    assert abs(transformed[:, 0].std() - 1.0) < 0.1


# ── Class imbalance ────────────────────────────────────────────────────

def test_class_balanced_fit_handles_skew(models):
    """class_weight='balanced' must let LR fit even on 80/20 imbalance."""
    rng = np.random.default_rng(7)
    n = 400
    X = rng.normal(0, 1, size=(n, 3))
    # 20% positive class
    y = (rng.uniform(0, 1, size=n) < 0.2).astype(int)
    m = models.LRModel().fit(X, y)
    p = m.predict_proba(X)
    # Just confirm it didn't collapse to a single class
    assert 0.05 < p[:, 1].mean() < 0.95


# ── Edge cases ─────────────────────────────────────────────────────────

def test_fit_raises_on_single_class(models):
    """sklearn LR raises if y has only one class — wrapper surfaces clearly."""
    X = np.random.RandomState(0).normal(0, 1, size=(50, 3))
    y = np.zeros(50, dtype=int)
    with pytest.raises(ValueError):
        models.LRModel().fit(X, y)


def test_fit_records_n_features(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    assert m.n_features_ == X.shape[1]


def test_predict_proba_rejects_wrong_n_features(models, synthetic_dataset):
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    bad = np.zeros((10, X.shape[1] + 1))
    with pytest.raises(ValueError):
        m.predict_proba(bad)


# ── AUC-style sanity ───────────────────────────────────────────────────

def test_lr_finds_signal_on_linearly_separable_data(models, synthetic_dataset):
    """On the synthetic problem the AUC should be well above chance."""
    from sklearn.metrics import roc_auc_score
    X, y = synthetic_dataset
    m = models.LRModel().fit(X, y)
    auc = roc_auc_score(y, m.p_win(X))
    assert auc > 0.80, f"in-sample AUC should be >0.80 on solvable data; got {auc}"
