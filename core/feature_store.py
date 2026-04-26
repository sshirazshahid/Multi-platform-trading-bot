"""FeatureStore — single source of truth for ML features.

Phase 2.1 of the quant rebuild plan. The model layer (Phase 3) consumes
features from here; the shadow runner (Phase 7) writes them on every
candidate. Features are deterministic numeric values keyed on
(ts, symbol, timeframe, feature_name) in the warehouse `features` table.

Two responsibilities:

  1. **Pure compute** (`compute_features_from_ohlcv(ohlcv_dict)`):
     given a dict of OHLCV DataFrames keyed by timeframe (e.g. "4h",
     "1h", "15m"), return a flat `dict[str, float]` of every feature
     in `FEATURE_SCHEMA`. Pure function — no I/O, no caching.

  2. **Persistence** (`FeatureStore` class wrapping a Warehouse):
     `write(ts, symbol, timeframe, features)` and `read(symbol, ts_range)`
     for round-trip persistence; idempotent (INSERT OR REPLACE on the
     composite PK).

MVP scope (this commit): the 8 technical features below. Phase 2.2/2.3
will add HMM regime + GARCH vol; Phase 2.4 microstructure; Phase 2.5
deterministic sentiment.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

# utils/indicators.py is in the project root — used by mcp_brain too.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.indicators import adx, atr, ema, macd, rsi  # noqa: E402

if TYPE_CHECKING:
    from core.warehouse import Warehouse


FEATURE_SCHEMA: list[str] = [
    # 4h timeframe
    "ema_gap_4h",
    "ema_slope_4h",
    # 1h timeframe
    "ema_align_1h",
    "rsi_14_1h",
    "adx_14_1h",
    "atr_pct_1h",
    "macd_hist_1h",
    # 15m timeframe
    "vol_ratio_15m",
]


def _safe_float(x, default: float = 0.0) -> float:
    """Coerce to float, returning `default` for NaN/inf/None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return v


def _compute_4h(df: pd.DataFrame) -> dict[str, float]:
    out = {"ema_gap_4h": 0.0, "ema_slope_4h": 0.0}
    if df is None or len(df) < 3:
        return out
    closes = df["close"].astype(float)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    last_close = float(closes.iloc[-1])
    if last_close > 0:
        out["ema_gap_4h"] = _safe_float(
            (e20.iloc[-1] - e50.iloc[-1]) / last_close
        )
    if len(e20) >= 3:
        e20_lag = float(e20.iloc[-3])
        if e20_lag > 0:
            out["ema_slope_4h"] = _safe_float(
                (e20.iloc[-1] - e20_lag) / e20_lag
            )
    return out


def _compute_1h(df: pd.DataFrame) -> dict[str, float]:
    out = {
        "ema_align_1h": 0.0,
        "rsi_14_1h": 50.0,
        "adx_14_1h": 0.0,
        "atr_pct_1h": 0.0,
        "macd_hist_1h": 0.0,
    }
    if df is None or len(df) < 5:
        return out
    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    out["ema_align_1h"] = _safe_float(np.sign(e20.iloc[-1] - e50.iloc[-1]))
    out["rsi_14_1h"] = _safe_float(rsi(closes, 14).iloc[-1], default=50.0)
    adx_s, _, _ = adx(highs, lows, closes, 14)
    out["adx_14_1h"] = _safe_float(adx_s.iloc[-1])
    last_close = float(closes.iloc[-1])
    if last_close > 0:
        atr_s = atr(highs, lows, closes, 14)
        out["atr_pct_1h"] = _safe_float(atr_s.iloc[-1] / last_close)
    _, _, hist = macd(closes)
    out["macd_hist_1h"] = _safe_float(hist.iloc[-1])
    return out


def _compute_15m(df: pd.DataFrame) -> dict[str, float]:
    out = {"vol_ratio_15m": 1.0}
    if df is None or len(df) < 2:
        return out
    vols = df["volume"].astype(float)
    if len(vols) >= 20:
        recent = vols.iloc[-20:].mean()
    else:
        recent = vols.mean()
    if recent > 0:
        out["vol_ratio_15m"] = _safe_float(vols.iloc[-1] / recent, default=1.0)
    return out


def compute_features_from_ohlcv(
    ohlcv: dict[str, pd.DataFrame],
) -> dict[str, float]:
    """Pure: OHLCV per-timeframe → flat feature dict.

    Args:
        ohlcv: dict keyed by timeframe (e.g. "4h", "1h", "15m"); each
            value is a DataFrame with columns open/high/low/close/volume.
            Latest row (iloc[-1]) is the "current" bar.

    Returns:
        dict[str, float] with every key in FEATURE_SCHEMA.

    Missing timeframes are filled with sensible defaults (no NaN).
    """
    feats: dict[str, float] = {}
    feats.update(_compute_4h(ohlcv.get("4h")))
    feats.update(_compute_1h(ohlcv.get("1h")))
    feats.update(_compute_15m(ohlcv.get("15m")))
    # Schema guarantee: every documented key present.
    for k in FEATURE_SCHEMA:
        feats.setdefault(k, 0.0)
    return feats


def feature_hash(features: dict[str, float]) -> str:
    """Deterministic short hash of a feature dict for idempotency keys.

    Sorting keys ensures the hash is stable regardless of dict iteration
    order. Returns 16 hex chars (64 bits — collision-safe for our scale).
    """
    canonical = json.dumps(
        {k: float(v) for k, v in features.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class FeatureStore:
    """Persistence wrapper around `Warehouse.features` table."""

    def __init__(self, warehouse: "Warehouse") -> None:
        self.wh = warehouse

    def write(
        self,
        *,
        ts: int,
        symbol: str,
        timeframe: str,
        features: dict[str, float],
    ) -> None:
        """Insert or replace one row per feature in `features`.

        Idempotent on (ts, symbol, timeframe, feature_name) — re-running
        with the same key updates the value rather than duplicating.
        """
        if not features:
            return
        rows = [
            (int(ts), str(symbol), str(timeframe), str(k), float(v))
            for k, v in features.items()
        ]
        self.wh._conn().executemany(
            "INSERT OR REPLACE INTO features"
            "(ts, symbol, timeframe, feature_name, value) VALUES (?,?,?,?,?)",
            rows,
        )

    def read(
        self,
        symbol: str,
        ts_start: int | None = None,
        ts_end: int | None = None,
    ) -> dict[int, dict[str, float]]:
        """Read features back into nested dict {ts: {feature_name: value}}.

        Optional ts range filters (inclusive on both ends).
        """
        sql = "SELECT ts, feature_name, value FROM features WHERE symbol=?"
        params: list = [symbol]
        if ts_start is not None:
            sql += " AND ts >= ?"
            params.append(int(ts_start))
        if ts_end is not None:
            sql += " AND ts <= ?"
            params.append(int(ts_end))
        sql += " ORDER BY ts"
        out: dict[int, dict[str, float]] = {}
        for row in self.wh._conn().execute(sql, tuple(params)).fetchall():
            ts = int(row["ts"])
            out.setdefault(ts, {})[str(row["feature_name"])] = float(row["value"])
        return out
