"""Isotonic probability calibration — Phase 3.3 of the quant rebuild plan.

Wraps `sklearn.isotonic.IsotonicRegression` to map raw model probabilities
(e.g. from the Phase 3.2 logistic regression) onto a monotone, non-parametric
calibrated curve fit on (raw_proba, realized_outcome) pairs. A monotone fit
preserves the model's relative ordering of candidates while improving
absolute probability quality (lower Brier score / better reliability).

Below `min_samples` (default 50), the fit is skipped and `transform` returns
the raw probabilities unchanged — a safe warm-up fallback so the calibrator
never degrades the pipeline when training data is thin.

Used by:
  - `scripts/train_models.py` (Phase 3.2): post-fit on the LR's OOS
    predictions, persisted via `IsotonicCalibrator.save`.
  - `core/mcp_brain.score_via_model` (Phase 3.5): loads the persisted
    calibrator and applies `transform` to raw model probabilities before
    thresholding.

This module deliberately does NOT replace `core/probability_calibrator.py`
(the legacy bucketed implementation). The swap is a separate task once the
LR + isotonic stack is wired through training.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


class IsotonicCalibrator:
    """sklearn IsotonicRegression wrapper with persistence + warm-up fallback.

    Attributes
    ----------
    min_samples : int
        Minimum number of (raw, realized) pairs required to actually fit the
        isotonic regression. Below this, `fit` records the sample count but
        does not fit, and `transform` returns raw probabilities unchanged.
    """

    def __init__(self, min_samples: int = 50):
        self.min_samples = int(min_samples)
        self._iso: IsotonicRegression | None = None
        self._n_fit = 0

    def fit(
        self,
        raw_probas: np.ndarray,
        realized: np.ndarray,
    ) -> IsotonicCalibrator:
        """Fit isotonic regression on raw probabilities and realized 0/1 outcomes.

        Parameters
        ----------
        raw_probas : array-like, shape (n,)
            Uncalibrated model probabilities in [0, 1].
        realized : array-like, shape (n,)
            Realized binary outcomes (0 or 1).

        Returns
        -------
        self

        Notes
        -----
        Below `min_samples`, `_n_fit` is recorded but the underlying
        `IsotonicRegression` is left unfit; `is_fitted` will return False
        and `transform` will pass raw probabilities through unchanged.
        """
        raw = np.asarray(raw_probas, dtype=float).ravel()
        y = np.asarray(realized, dtype=float).ravel()

        if raw.shape[0] != y.shape[0]:
            raise ValueError(
                f"length mismatch: raw_probas={raw.shape[0]} realized={y.shape[0]}"
            )

        self._n_fit = int(raw.shape[0])

        if self._n_fit < self.min_samples:
            # Warm-up: do not fit; transform() will pass through.
            self._iso = None
            return self

        iso = IsotonicRegression(
            out_of_bounds="clip",
            y_min=0.0,
            y_max=1.0,
            increasing=True,
        )
        iso.fit(raw, y)
        self._iso = iso
        return self

    def transform(self, raw_probas: np.ndarray) -> np.ndarray:
        """Return calibrated probabilities for `raw_probas`.

        Falls back to the raw input when the calibrator has not been fit
        (i.e. the training set was below `min_samples`).
        """
        raw = np.asarray(raw_probas, dtype=float).ravel()
        if raw.size == 0:
            return raw.copy()
        if not self.is_fitted():
            return raw.copy()
        return np.asarray(self._iso.transform(raw), dtype=float)

    def is_fitted(self) -> bool:
        """True iff the underlying IsotonicRegression was actually fit."""
        return self._iso is not None and self._n_fit >= self.min_samples

    def save(self, path: str | Path) -> None:
        """Persist the calibrator (state + fitted regressor) via joblib."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "min_samples": self.min_samples,
            "n_fit": self._n_fit,
            "iso": self._iso,
        }
        joblib.dump(payload, path)

    @classmethod
    def load(cls, path: str | Path) -> IsotonicCalibrator:
        """Load a calibrator previously written via `save`."""
        payload = joblib.load(Path(path))
        obj = cls(min_samples=int(payload["min_samples"]))
        obj._n_fit = int(payload["n_fit"])
        obj._iso = payload["iso"]
        return obj
