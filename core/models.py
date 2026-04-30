"""Phase 13.2 — fitted model layer.

LRModel: thin sklearn LogisticRegression wrapper that:
  * Standardizes features internally (StandardScaler — saved + loaded
    alongside the model) so callers don't need to pre-scale.
  * Reproducibly fits with `random_state=42` by default.
  * Persists via joblib to a single .pkl file containing both the
    fitted scaler and the fitted classifier.
  * Exposes `predict_proba(X)` (full sklearn-shape (n,2)) and `p_win(X)`
    (just the class-1 column) for callers that only care about the
    "this trade will win" probability.

Why LR-only at this stage:
  * Sample size is the binding constraint (~290 fitted-attribution trades
    available). XGBoost + Optuna search would burn statistical power on
    multiple-comparison overhead.
  * `class_weight='balanced'` handles WR ≠ 50% gracefully without tuning.
  * Calibration is a separate concern: pair this with `core/calibration.IsotonicCalibrator`
    when you want post-fit probability quality.

Reference: compiled-pondering-key.md Phase 3.2.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


class LRModel:
    """Reproducible logistic regression with internal scaling + persistence."""

    def __init__(
        self,
        *,
        C: float = 1.0,
        random_state: int = 42,
        max_iter: int = 1000,
        class_weight: Optional[str] = "balanced",
    ) -> None:
        self.C = float(C)
        self.random_state = int(random_state)
        self.max_iter = int(max_iter)
        self.class_weight = class_weight
        self._scaler: Optional[StandardScaler] = None
        self._clf: Optional[LogisticRegression] = None
        self.n_features_: Optional[int] = None
        self.classes_: Optional[np.ndarray] = None

    def fit(self, X, y) -> "LRModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D (n, n_features); got shape {X.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X.shape[0]={X.shape[0]} but y.shape[0]={y.shape[0]}")
        unique = np.unique(y)
        if unique.size < 2:
            raise ValueError(
                f"y must contain >= 2 classes; got {unique.tolist()}")

        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        clf = LogisticRegression(
            C=self.C,
            random_state=self.random_state,
            max_iter=self.max_iter,
            class_weight=self.class_weight,
            solver="lbfgs",
        )
        clf.fit(Xs, y)

        self._scaler = scaler
        self._clf = clf
        self.n_features_ = int(X.shape[1])
        self.classes_ = clf.classes_
        return self

    def predict_proba(self, X) -> np.ndarray:
        if self._clf is None or self._scaler is None:
            raise RuntimeError("LRModel must be fit() before predict_proba()")
        X = np.asarray(X, dtype=float)
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D; got shape {X.shape}")
        if X.shape[1] != self.n_features_:
            raise ValueError(
                f"expected {self.n_features_} features; got {X.shape[1]}")
        return self._clf.predict_proba(self._scaler.transform(X))

    def p_win(self, X) -> np.ndarray:
        """Probability of class==1 (the "win" class).

        Caller is responsible for ensuring training labels were 0=loss, 1=win.
        """
        proba = self.predict_proba(X)
        if self.classes_ is not None and 1 in self.classes_:
            idx = int(np.where(self.classes_ == 1)[0][0])
            return proba[:, idx]
        return proba[:, -1]

    def save(self, path, *, model_version: Optional[str] = None) -> None:
        """Persist to a single .pkl. `model_version`, when provided, is
        embedded in the payload so callers (e.g. ShadowPredictor on
        Windows where symlinks aren't reliable) can recover the
        canonical version name regardless of the on-disk filename."""
        if self._clf is None:
            raise RuntimeError("Cannot save unfit model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "C": self.C,
            "random_state": self.random_state,
            "max_iter": self.max_iter,
            "class_weight": self.class_weight,
            "scaler": self._scaler,
            "clf": self._clf,
            "n_features": self.n_features_,
            "classes": self.classes_,
        }
        if model_version is not None:
            payload["model_version"] = str(model_version)
        joblib.dump(payload, path)

    @classmethod
    def load(cls, path) -> "LRModel":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"model artifact not found: {path}")
        data = joblib.load(path)
        m = cls(
            C=float(data.get("C", 1.0)),
            random_state=int(data.get("random_state", 42)),
            max_iter=int(data.get("max_iter", 1000)),
            class_weight=data.get("class_weight", "balanced"),
        )
        m._scaler = data["scaler"]
        m._clf = data["clf"]
        m.n_features_ = int(data["n_features"])
        m.classes_ = data["classes"]
        return m


class GBMModel:
    """Reproducible HistGradientBoostingClassifier with internal scaling +
    persistence. Mirrors LRModel's API so callers can swap models cleanly
    via duck-typing.

    HistGradientBoosting captures non-linear feature interactions without
    needing LightGBM/XGBoost — sklearn 1.5.2 (already pinned) is enough.
    StandardScaler isn't strictly required for tree models, but we keep it
    so the persisted artifact is interchangeable with LRModel artifacts at
    the ensemble layer.
    """

    def __init__(
        self,
        *,
        max_iter: int = 200,
        learning_rate: float = 0.05,
        max_depth: int = 5,
        l2_regularization: float = 1.0,
        random_state: int = 42,
        class_weight: Optional[str] = "balanced",
    ) -> None:
        self.max_iter = int(max_iter)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.l2_regularization = float(l2_regularization)
        self.random_state = int(random_state)
        self.class_weight = class_weight
        self._scaler: Optional[StandardScaler] = None
        self._clf: Optional[HistGradientBoostingClassifier] = None
        self.n_features_: Optional[int] = None
        self.classes_: Optional[np.ndarray] = None

    def fit(self, X, y) -> "GBMModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).ravel()
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D (n, n_features); got shape {X.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(
                f"X.shape[0]={X.shape[0]} but y.shape[0]={y.shape[0]}")
        unique = np.unique(y)
        if unique.size < 2:
            raise ValueError(
                f"y must contain >= 2 classes; got {unique.tolist()}")

        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        clf = HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            l2_regularization=self.l2_regularization,
            random_state=self.random_state,
            class_weight=self.class_weight,
        )
        clf.fit(Xs, y)

        self._scaler = scaler
        self._clf = clf
        self.n_features_ = int(X.shape[1])
        self.classes_ = clf.classes_
        return self

    def predict_proba(self, X) -> np.ndarray:
        if self._clf is None or self._scaler is None:
            raise RuntimeError("GBMModel must be fit() before predict_proba()")
        X = np.asarray(X, dtype=float)
        if X.ndim != 2:
            raise ValueError(f"X must be 2-D; got shape {X.shape}")
        if X.shape[1] != self.n_features_:
            raise ValueError(
                f"expected {self.n_features_} features; got {X.shape[1]}")
        return self._clf.predict_proba(self._scaler.transform(X))

    def p_win(self, X) -> np.ndarray:
        proba = self.predict_proba(X)
        if self.classes_ is not None and 1 in self.classes_:
            idx = int(np.where(self.classes_ == 1)[0][0])
            return proba[:, idx]
        return proba[:, -1]

    def save(self, path, *, model_version: Optional[str] = None) -> None:
        if self._clf is None:
            raise RuntimeError("Cannot save unfit model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "max_iter": self.max_iter,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "l2_regularization": self.l2_regularization,
            "random_state": self.random_state,
            "class_weight": self.class_weight,
            "scaler": self._scaler,
            "clf": self._clf,
            "n_features": self.n_features_,
            "classes": self.classes_,
        }
        if model_version is not None:
            payload["model_version"] = str(model_version)
        joblib.dump(payload, path)

    @classmethod
    def load(cls, path) -> "GBMModel":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"model artifact not found: {path}")
        data = joblib.load(path)
        m = cls(
            max_iter=int(data.get("max_iter", 200)),
            learning_rate=float(data.get("learning_rate", 0.05)),
            max_depth=int(data.get("max_depth", 5)),
            l2_regularization=float(data.get("l2_regularization", 1.0)),
            random_state=int(data.get("random_state", 42)),
            class_weight=data.get("class_weight", "balanced"),
        )
        m._scaler = data["scaler"]
        m._clf = data["clf"]
        m.n_features_ = int(data["n_features"])
        m.classes_ = data["classes"]
        return m
