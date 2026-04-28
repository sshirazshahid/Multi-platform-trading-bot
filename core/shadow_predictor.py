"""Phase 13.5 — shadow prediction logger.

Records the LR model's `p_win` for every live entry decision into the
warehouse `predictions` table (added in Phase 0.2). LOG ONLY — does not
gate, modify, or otherwise affect entry decisions. The live engine
keeps using the existing MCP scorer; this scaffold accumulates
(prediction, realized outcome) pairs so a future re-fit has clean
forward-attributed labels.

Usage from a live entry path:

    pred = ShadowPredictor.get()
    pred.log_entry(
        ts=now_ts, symbol="ATOM/USDT:USDT", side="buy",
        features={"score": 80, "rsi_1h": 65, ...},
    )

The predictor:
  - Lazy-loads `data/models/lr_v_latest.pkl` once per process.
  - Coerces a feature dict into the FEATURE_KEYS vector
    (same schema as scripts.train_models).
  - Calls `LRModel.p_win(X)` and writes a row to `predictions`.
  - All exceptions caught and downgraded to debug log — never
    propagates to the entry path.

Coupling note: keeps FEATURE_KEYS in sync with scripts/train_models by
importing it. If train_models grows the feature set, the predictor
expects the live-entry features dict to carry the same keys.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LATEST_MODEL = Path("data/models/lr_v_latest.pkl")


def _safe_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(v)
        if f != f:  # NaN
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


class ShadowPredictor:
    """Singleton wrapping the latest LR model + warehouse predictions logger."""

    _instance = None

    @classmethod
    def get(cls) -> "ShadowPredictor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._model = None
        self._model_version: Optional[str] = None
        self._feature_keys: Optional[list[str]] = None
        self._load_attempted = False

    def _ensure_model(self) -> bool:
        """Lazy load the latest LR model. Returns True on success."""
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        try:
            from core.models import LRModel
            self._model = LRModel.load(LATEST_MODEL)
            # Phase 13.5b: read the actual model_version from the saved
            # joblib payload (set by scripts/train_models). On Windows the
            # symlink-stem fallback is "lr_v_latest" which loses the
            # version metadata, so we read it from inside the artifact.
            try:
                import joblib
                payload = joblib.load(LATEST_MODEL)
                self._model_version = str(
                    payload.get("model_version")
                    or payload.get("version_name")
                    or LATEST_MODEL.resolve().stem
                )
            except Exception:
                try:
                    self._model_version = LATEST_MODEL.resolve().stem
                except Exception:
                    self._model_version = LATEST_MODEL.stem
            try:
                from scripts.train_models import FEATURE_KEYS
                self._feature_keys = list(FEATURE_KEYS)
            except Exception as e:
                logger.debug(f"[Shadow] feature_keys import failed: {e}")
                return False
            logger.info(
                f"[Shadow] loaded LR model {self._model_version} "
                f"({len(self._feature_keys)} features)")
            return True
        except FileNotFoundError:
            logger.debug("[Shadow] no model artifact yet — predictor inactive")
            return False
        except Exception as e:
            logger.warning(f"[Shadow] model load failed: {e}")
            return False

    def predict_p_win(self, features: dict) -> Optional[float]:
        """Return p_win for the given feature dict, or None if unavailable."""
        if not self._ensure_model():
            return None
        if not self._feature_keys:
            return None
        try:
            import numpy as np
            vec = np.array(
                [[_safe_float(features.get(k)) for k in self._feature_keys]],
                dtype=float,
            )
            p = float(self._model.p_win(vec)[0])
            return p
        except Exception as e:
            logger.debug(f"[Shadow] predict failed: {e}")
            return None

    def log_entry(
        self,
        *,
        ts: float,
        symbol: str,
        side: str,
        features: dict,
        warehouse=None,
        candidate_id: Optional[int] = None,
        market_type: Optional[str] = None,
    ) -> Optional[float]:
        """Compute p_win and persist to warehouse.predictions.

        Args
        ----
        symbol : str
            Use unified ccxt format. If `market_type='futures'` and the
            symbol lacks the `:USDT` suffix, the predictor appends it so
            the row joins cleanly to trades (which always use the
            settle-currency suffix).
        candidate_id : int | None
            The warehouse candidate row id this prediction is logged
            against. Phase 13.5b — enables `predictions JOIN trades ON
            trades.candidate_id = predictions.candidate_id` for
            re-fitting with realised outcomes.

        Returns the p_win value (or None if predictor inactive).
        """
        p = self.predict_p_win(features)
        if p is None:
            return None
        # Phase 13.5b: normalise symbol to the trades.symbol format so
        # downstream joins work. Trades for futures are stored as
        # "BASE/QUOTE:QUOTE"; predictions previously stored
        # "BASE/QUOTE" which never matched.
        sym = str(symbol)
        if market_type == "futures" and ":" not in sym:
            sym = f"{sym}:USDT"
        try:
            from core.warehouse import get_warehouse
            wh = warehouse if warehouse is not None else get_warehouse()
            feat_canonical = json.dumps(
                {k: _safe_float(features.get(k)) for k in self._feature_keys},
                sort_keys=True,
                separators=(",", ":"),
            )
            feat_hash = hashlib.sha256(feat_canonical.encode()).hexdigest()[:16]
            wh._conn().execute(
                "INSERT OR REPLACE INTO predictions"
                "(ts, model_version, symbol, side, p_win, raw_score, "
                " feature_hash, candidate_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(ts), str(self._model_version),
                    sym, str(side),
                    float(p), float(features.get("score") or 0.0),
                    feat_hash,
                    int(candidate_id) if candidate_id is not None else None,
                ),
            )
        except Exception as e:
            logger.debug(f"[Shadow] log_entry persistence failed: {e}")
        return p
