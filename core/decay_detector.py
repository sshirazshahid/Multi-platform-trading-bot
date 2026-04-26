"""Concept-drift and performance-decay detectors with quarantine bookkeeping.

Two detectors:

  * :class:`DriftDetector` — Kolmogorov-Smirnov two-sample test on a
    feature's recent vs reference window. Alarm fires when ``p < threshold``
    has been *sustained* for at least ``sustain_seconds``. A return-to-OK
    resets the breach clock so transient blips don't trigger.

  * :class:`PerformanceDecayDetector` — z-score of a rolling Sharpe vs the
    walk-forward expectation. Alarm fires when ``z <= z_threshold`` has
    been sustained for ``sustain_seconds`` (default 24h).

Both detectors are pure library — they do not write any side effects.
The :func:`quarantine` / :func:`is_quarantined` helpers persist a
quarantine record (``{strategy, reason, until_ts}``) to a JSON file
which ``risk_manager`` consults before sizing (wired separately).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import numpy as np
from scipy.stats import ks_2samp

PathLike = Union[str, Path]


class DriftDetector:
    """KS-test feature drift between a recent window and a reference window.

    Tracks the FIRST timestamp at which p crossed below ``p_threshold``.
    Alarm fires when the current ``ts`` is at least ``sustain_seconds``
    past that first-breach timestamp AND p is still below threshold.
    Any return to p >= threshold resets the first-breach timestamp.
    """

    def __init__(
        self,
        p_threshold: float = 0.01,
        sustain_seconds: float = 6 * 3600,
    ) -> None:
        self.p_threshold = float(p_threshold)
        self.sustain_seconds = float(sustain_seconds)
        # first_breach_ts per feature_name
        self._first_breach: dict[str, Optional[float]] = {}
        # last computed p-value per feature_name (for inspection / debug)
        self._last_p: dict[str, float] = {}

    def update(
        self,
        *,
        ts: float,
        feature_name: str,
        recent_values: np.ndarray,
        ref_values: np.ndarray,
    ) -> bool:
        """Return True iff alarm has fired (p < threshold sustained)."""
        recent = np.asarray(recent_values, dtype=float)
        ref = np.asarray(ref_values, dtype=float)
        # Degenerate windows -> can't test, treat as not breaching.
        if recent.size < 2 or ref.size < 2:
            self._first_breach[feature_name] = None
            return False
        try:
            res = ks_2samp(recent, ref)
            p = float(res.pvalue)
        except Exception:
            self._first_breach[feature_name] = None
            return False
        self._last_p[feature_name] = p
        if p < self.p_threshold:
            first = self._first_breach.get(feature_name)
            if first is None:
                self._first_breach[feature_name] = float(ts)
                return False
            return (float(ts) - first) >= self.sustain_seconds
        # Returned to OK -> reset breach clock.
        self._first_breach[feature_name] = None
        return False

    def last_p(self, feature_name: str) -> Optional[float]:
        return self._last_p.get(feature_name)

    def first_breach_ts(self, feature_name: str) -> Optional[float]:
        return self._first_breach.get(feature_name)


class PerformanceDecayDetector:
    """Rolling-Sharpe z-score decay vs walk-forward expectation.

    z = (rolling_sharpe - expected_sharpe) / expected_sharpe_std

    Alarm fires when z <= z_threshold has been sustained for
    ``sustain_seconds``. Same sustain reset semantics as DriftDetector.
    """

    def __init__(
        self,
        expected_sharpe: float,
        expected_sharpe_std: float,
        sustain_seconds: float = 24 * 3600,
        z_threshold: float = -1.5,
    ) -> None:
        if expected_sharpe_std <= 0:
            raise ValueError("expected_sharpe_std must be > 0")
        self.expected_sharpe = float(expected_sharpe)
        self.expected_sharpe_std = float(expected_sharpe_std)
        self.sustain_seconds = float(sustain_seconds)
        self.z_threshold = float(z_threshold)
        self._first_breach: Optional[float] = None
        self._last_z: Optional[float] = None
        self._last_sharpe: Optional[float] = None

    @staticmethod
    def _sharpe(returns: np.ndarray) -> Optional[float]:
        r = np.asarray(returns, dtype=float)
        if r.size < 2:
            return None
        s = r.std(ddof=1)
        if s <= 0:
            return None
        return float(r.mean() / s)

    def update(self, *, ts: float, recent_returns: np.ndarray) -> bool:
        """Return True iff z(rolling_sharpe) <= z_threshold sustained."""
        sr = self._sharpe(recent_returns)
        if sr is None:
            # Can't compute — don't change breach state.
            return False
        self._last_sharpe = sr
        z = (sr - self.expected_sharpe) / self.expected_sharpe_std
        self._last_z = z
        if z <= self.z_threshold:
            if self._first_breach is None:
                self._first_breach = float(ts)
                return False
            return (float(ts) - self._first_breach) >= self.sustain_seconds
        # Returned to OK -> reset.
        self._first_breach = None
        return False

    @property
    def last_z(self) -> Optional[float]:
        return self._last_z

    @property
    def last_sharpe(self) -> Optional[float]:
        return self._last_sharpe

    @property
    def first_breach_ts(self) -> Optional[float]:
        return self._first_breach


# ---------------------------------------------------------------------------
# Quarantine bookkeeping
# ---------------------------------------------------------------------------

DEFAULT_QUARANTINE_PATH = Path("data/quarantine.json")


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def quarantine(
    strategy: str,
    reason: str,
    until_ts: float,
    path: PathLike = DEFAULT_QUARANTINE_PATH,
) -> None:
    """Write / merge a quarantine record. Idempotent on (strategy)."""
    p = Path(path)
    data = _load(p)
    data[str(strategy)] = {
        "strategy": str(strategy),
        "reason": str(reason),
        "until_ts": float(until_ts),
    }
    _save(p, data)


def is_quarantined(
    strategy: str,
    now_ts: float,
    path: PathLike = DEFAULT_QUARANTINE_PATH,
) -> bool:
    """True iff a record exists for ``strategy`` with ``until_ts > now_ts``."""
    p = Path(path)
    data = _load(p)
    rec = data.get(str(strategy))
    if not rec:
        return False
    until = rec.get("until_ts")
    try:
        return float(until) > float(now_ts)
    except (TypeError, ValueError):
        return False


__all__ = [
    "DriftDetector",
    "PerformanceDecayDetector",
    "quarantine",
    "is_quarantined",
]
