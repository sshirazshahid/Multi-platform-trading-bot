"""Kronos foundation-model directional forecaster — thin adapter (Phase 1).

Wraps the vendored MIT Kronos model (`vendor/kronos/model`) behind a small,
decoupled interface so the rest of the bot never imports torch unless this
adapter is explicitly used.

Design constraints (see plan twinkly-skipping-stallman.md):
  * torch / Kronos are imported INSIDE methods, never at module top — importing
    this module is cheap and side-effect-free; it raises a clear error only when
    `.forecast()`/`.load()` is actually called without torch+weights present.
  * Lazy singleton: model+tokenizer load once via `get_kronos_forecaster()`.
  * Pure inference: no disk/network writes, no global state mutation.

The model returns a *mean* forecast path (Kronos averages `sample_count` samples
internally), so the directional signal is `exp_ret` = predicted close at the
horizon / entry close − 1. `p_up` is a documented monotone convenience derived
from `exp_ret` (NOT a sample frequency); thresholding on `exp_ret` is preferred
for the falsification probes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
_VENDOR = _ROOT / "vendor" / "kronos"
_TOK_DIR = _ROOT / "models" / "kronos" / "Kronos-Tokenizer-2k"
_MDL_DIR = _ROOT / "models" / "kronos" / "Kronos-mini"

MODEL_VERSION = "kronos-mini-2k"
PRICE_COLS = ["open", "high", "low", "close"]


@dataclass
class Forecast:
    """Directional forecast for one decision bar."""

    entry_close: float
    pred_close: float  # mean predicted close at the target horizon
    exp_ret: float  # pred_close / entry_close - 1   (primary signal)
    p_up: float  # sigmoid(exp_ret / scale) — convenience, see module doc
    horizon: int
    pred_path: np.ndarray  # mean predicted close for every step 1..horizon

    @property
    def direction(self) -> int:
        return int(np.sign(self.exp_ret))

    def exp_ret_at(self, h: int) -> float:
        """Expected return at intermediate horizon h (1-indexed, ≤ horizon)."""
        return float(self.pred_path[h - 1]) / self.entry_close - 1.0


class KronosForecaster:
    """Lazy-loaded Kronos-mini adapter. Construct via `get_kronos_forecaster()`."""

    def __init__(
        self, *, lookback: int = 400, max_context: int = 512, p_up_scale: float = 0.005
    ) -> None:
        self.lookback = lookback
        self.max_context = max_context
        self.p_up_scale = p_up_scale  # exp_ret that maps to p_up≈0.73
        self._predictor = None
        self._device: str | None = None

    # -- lifecycle -------------------------------------------------------
    def load(self) -> KronosForecaster:
        """Load tokenizer+model once. torch imported here, not at module top."""
        if self._predictor is not None:
            return self
        import sys

        if str(_VENDOR) not in sys.path:
            sys.path.insert(0, str(_VENDOR))
        try:
            import torch
            from model import Kronos, KronosPredictor, KronosTokenizer
        except Exception as e:  # pragma: no cover - environment guard
            raise RuntimeError(
                "Kronos requires torch + the vendored model package. "
                "Install: python -m pip install -r requirements-kronos.txt"
            ) from e
        # Eval-only override (defaults to mini): point at a larger model/tokenizer
        # via KRONOS_MDL_DIR / KRONOS_TOK_DIR to fairly test bigger Kronos sizes.
        import os
        tok_dir = Path(os.environ.get("KRONOS_TOK_DIR", str(_TOK_DIR)))
        mdl_dir = Path(os.environ.get("KRONOS_MDL_DIR", str(_MDL_DIR)))
        if not tok_dir.exists() or not mdl_dir.exists():
            raise RuntimeError(
                f"Kronos weights missing (tok={tok_dir}, mdl={mdl_dir}). "
                "Download with huggingface_hub snapshot_download (see scripts/kronos_smoke.py)."
            )
        self._device = "cuda:0" if torch.cuda.is_available() else "cpu"
        tok = KronosTokenizer.from_pretrained(str(tok_dir))
        mdl = Kronos.from_pretrained(str(mdl_dir))
        self._predictor = KronosPredictor(
            mdl, tok, device=self._device, max_context=self.max_context
        )
        return self

    @property
    def available(self) -> bool:
        """True if torch + weights are present (does NOT load the model)."""
        try:
            import importlib.util

            return (
                importlib.util.find_spec("torch") is not None
                and _TOK_DIR.exists()
                and _MDL_DIR.exists()
            )
        except Exception:  # pragma: no cover
            return False

    # -- inference -------------------------------------------------------
    def forecast(
        self,
        df_ohlcv: pd.DataFrame,
        *,
        horizon: int = 4,
        samples: int = 5,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> Forecast:
        """Forecast `horizon` bars ahead from a context window.

        df_ohlcv: rows up to and including the decision bar, with columns
            ['ts','open','high','low','close'] (+ optional 'volume'); 'ts' = unix
            seconds. The newest row is the entry bar. Future timestamps are
            extrapolated from the bar cadence (24/7 crypto → exact); they are
            clock features only, so this introduces no look-ahead.
        """
        if self._predictor is None:
            self.load()
        if not all(c in df_ohlcv.columns for c in PRICE_COLS):
            raise ValueError(f"df_ohlcv needs columns {PRICE_COLS}")

        ctx = df_ohlcv.tail(self.lookback).reset_index(drop=True)
        cols = PRICE_COLS + (["volume"] if "volume" in ctx.columns else [])
        x_df = ctx[cols].astype(float)

        if "ts" in ctx.columns:
            x_ts = pd.to_datetime(ctx["ts"].to_numpy(), unit="s")
        else:  # fall back to a synthetic 15m cadence
            x_ts = pd.to_datetime(
                np.arange(len(ctx)) * 900, unit="s", origin=pd.Timestamp("2025-09-01")
            )
        x_ts = pd.Series(x_ts)
        step = x_ts.diff().median()
        if pd.isna(step) or step <= pd.Timedelta(0):
            step = pd.Timedelta(minutes=15)
        last = x_ts.iloc[-1]
        y_ts = pd.Series([last + step * (k + 1) for k in range(horizon)])

        pred = self._predictor.predict(
            df=x_df,
            x_timestamp=x_ts,
            y_timestamp=y_ts,
            pred_len=horizon,
            T=temperature,
            top_p=top_p,
            sample_count=samples,
            verbose=False,
        )
        entry_close = float(x_df["close"].iloc[-1])
        pred_path = pred["close"].to_numpy(dtype=float)
        pred_close = float(pred_path[-1])
        exp_ret = pred_close / entry_close - 1.0
        p_up = float(1.0 / (1.0 + np.exp(-exp_ret / self.p_up_scale)))
        return Forecast(
            entry_close=entry_close,
            pred_close=pred_close,
            exp_ret=exp_ret,
            p_up=p_up,
            horizon=horizon,
            pred_path=pred_path,
        )


_SINGLETON: KronosForecaster | None = None


def get_kronos_forecaster(**kwargs) -> KronosForecaster:
    """Return the process-wide Kronos adapter (constructed once; not yet loaded)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = KronosForecaster(**kwargs)
    return _SINGLETON
