"""HMM regime detector (Phase 2.2).

3-state Gaussian Hidden Markov Model that classifies bars into
trend / chop / expansion regimes from a (returns, vol) feature pair.

Pure-function library only — caller wiring (replacing the heuristic
ADX/Hurst classifier in `core/market_regime.py` when ≥ 200 bars of fit
data exist) is a sibling task.

Typical usage
-------------
    >>> hmm = RegimeHMM(n_states=3, random_state=42).fit(returns, vol)
    >>> states = hmm.predict(returns, vol)              # (T,)
    >>> proba  = hmm.predict_proba(returns, vol)        # (T, n_states)
    >>> hmm.save("data/models/hmm_BTC_USDT.pkl")
    >>> RegimeHMM.load("data/models/hmm_BTC_USDT.pkl")

State IDs are arbitrary — fitted state 0 is whichever cluster the EM
algorithm settled on first. Callers that need stable semantic labels
(trend/chop/expansion) should map by inspecting per-state means/covars
after fit.

Dependencies: hmmlearn, numpy, joblib.

Reference: compiled-pondering-key.md Phase 2.2.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import joblib
import numpy as np
from hmmlearn.hmm import GaussianHMM

PathLike = Union[str, Path]


class RegimeHMM:
    """3-state Gaussian HMM over (returns, vol) features."""

    def __init__(self, n_states: int = 3, random_state: int = 42) -> None:
        if n_states < 2:
            raise ValueError(f"n_states must be >= 2 (got {n_states})")
        self.n_states = int(n_states)
        self.random_state = int(random_state)
        self._model: GaussianHMM | None = None

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _stack(returns: np.ndarray, vol: np.ndarray) -> np.ndarray:
        """Validate and stack (returns, vol) into an (N, 2) feature matrix."""
        r = np.asarray(returns, dtype=float).reshape(-1)
        v = np.asarray(vol, dtype=float).reshape(-1)
        if r.shape[0] != v.shape[0]:
            raise ValueError(
                f"returns and vol must have the same length "
                f"(got {r.shape[0]} vs {v.shape[0]})"
            )
        if not np.isfinite(r).all() or not np.isfinite(v).all():
            raise ValueError("returns and vol must be finite (no NaN/inf)")
        return np.column_stack([r, v])

    # -------------------------------------------------------------------- fit

    def fit(self, returns: np.ndarray, vol: np.ndarray) -> RegimeHMM:
        """Fit on (returns, vol) feature pairs. Stacks into N×2 matrix.

        Raises
        ------
        ValueError
            If lengths mismatch, inputs contain non-finite values, or the
            number of samples is below `n_states`.
        """
        X = self._stack(returns, vol)
        if X.shape[0] < self.n_states:
            raise ValueError(
                f"insufficient data: n_samples={X.shape[0]} < n_states={self.n_states}"
            )

        model = GaussianHMM(
            n_components=self.n_states,
            covariance_type="full",
            n_iter=100,
            random_state=self.random_state,
        )
        model.fit(X)
        self._model = model
        return self

    # ---------------------------------------------------------------- predict

    def _ensure_fit(self) -> GaussianHMM:
        if self._model is None:
            raise RuntimeError("RegimeHMM not yet fit — call .fit() first")
        return self._model

    def predict(self, returns: np.ndarray, vol: np.ndarray) -> np.ndarray:
        """Most-likely state per bar; shape (T,) of int."""
        model = self._ensure_fit()
        X = self._stack(returns, vol)
        return model.predict(X).astype(int)

    def predict_proba(self, returns: np.ndarray, vol: np.ndarray) -> np.ndarray:
        """Posterior probability of each state per bar; shape (T, n_states)."""
        model = self._ensure_fit()
        X = self._stack(returns, vol)
        return np.asarray(model.predict_proba(X), dtype=float)

    # --------------------------------------------------------- persistence

    def save(self, path: PathLike) -> None:
        """Persist via joblib. Caller is responsible for parent dir creation."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, p)

    @classmethod
    def load(cls, path: PathLike) -> RegimeHMM:
        """Load a previously-saved RegimeHMM via joblib."""
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls):
            raise TypeError(
                f"loaded object is not a RegimeHMM (got {type(obj).__name__})"
            )
        return obj
