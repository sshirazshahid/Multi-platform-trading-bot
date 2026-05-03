"""GARCH(1,1) one-step-ahead volatility forecaster (Phase 2.3).

Wraps `arch.arch_model` to fit a GARCH(p,q) (defaults p=1, q=1) model on a
returns series, then exposes:

  * `forecast(horizon=k)` — k-step-ahead conditional volatility (sigma)
    as a numpy array of shape (k,). NOT variance; the caller squares
    when variance is needed.
  * `conditional_volatility()` — in-sample sigma series (sqrt of
    conditional variance), shape == len(returns_in).
  * `params()` — dict of fitted parameters (omega, alpha[1], beta[1], ...).
  * `save(path)` / `load(path)` — joblib persistence so refits can be
    cached and reloaded without retraining.

Used by Phase 4.2 `core.vol_target.VolTarget.size(...)` to set per-symbol
position notional based on a forecast vol relative to a portfolio
annualized-vol target.

Notes
-----
* `arch_model` is called with `rescale=False` so the returned parameters
  match the units of the input returns (no internal scale factor).
* `.fit(disp='off')` silences the default verbose iteration log.
* This module is a pure compute library — refresh cadence (e.g. daily
  refit) is the caller's responsibility.

Reference: compiled-pondering-key.md Phase 2.3.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
from arch import arch_model

# Minimum return-series length we will accept for fitting. arch's optimizer
# diverges on tiny samples and the resulting params are useless downstream.
MIN_RETURNS = 50


class GarchVol:
    """GARCH(p,q) wrapper around `arch.arch_model` with persistence."""

    def __init__(self, p: int = 1, q: int = 1, dist: str = "normal"):
        self.p = int(p)
        self.q = int(q)
        self.dist = str(dist)
        self._fitted: Any | None = None  # arch ARCHModelResult after .fit()

    # -- Fitting -----------------------------------------------------------

    def fit(self, returns: np.ndarray) -> GarchVol:
        """Fit GARCH(p,q) on `returns`. Returns self for chaining."""
        arr = np.asarray(returns, dtype=float).ravel()
        if arr.size == 0:
            raise ValueError("returns is empty")
        if arr.size < MIN_RETURNS:
            raise ValueError(
                f"returns too short ({arr.size}); need >= {MIN_RETURNS}"
            )
        if not np.all(np.isfinite(arr)):
            raise ValueError("returns contains non-finite values")

        model = arch_model(
            arr,
            mean="Zero",
            vol="GARCH",
            p=self.p,
            q=self.q,
            dist=self.dist,
            rescale=False,
        )
        self._fitted = model.fit(disp="off", show_warning=False)
        return self

    # -- Inference ---------------------------------------------------------

    def _require_fitted(self) -> Any:
        if self._fitted is None:
            raise RuntimeError("GarchVol: must call .fit(returns) first")
        return self._fitted

    def forecast(self, horizon: int = 1) -> np.ndarray:
        """k-step-ahead conditional vol (sigma), shape (horizon,)."""
        if int(horizon) < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")
        res = self._require_fitted()
        fc = res.forecast(horizon=int(horizon), reindex=False)
        # Variance frame: rows = origins, cols = h.1..h.k. Take the last row
        # (most recent origin) and convert variance -> vol.
        var = np.asarray(fc.variance.values, dtype=float)
        last = var[-1]
        sigma = np.sqrt(last)
        return sigma.astype(float)

    def conditional_volatility(self) -> np.ndarray:
        """In-sample conditional volatility series (sigma)."""
        res = self._require_fitted()
        cv = np.asarray(res.conditional_volatility, dtype=float)
        return cv

    def params(self) -> dict[str, float]:
        """Fitted parameter dict, e.g. {'omega':..., 'alpha[1]':..., 'beta[1]':...}."""
        res = self._require_fitted()
        return {k: float(v) for k, v in res.params.to_dict().items()}

    # -- Persistence -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist fitted state via joblib."""
        self._require_fitted()
        joblib.dump(
            {
                "p": self.p,
                "q": self.q,
                "dist": self.dist,
                "fitted": self._fitted,
            },
            str(path),
        )

    @classmethod
    def load(cls, path: str | Path) -> GarchVol:
        """Load a previously-saved GarchVol."""
        blob = joblib.load(str(path))
        obj = cls(p=blob["p"], q=blob["q"], dist=blob["dist"])
        obj._fitted = blob["fitted"]
        return obj
