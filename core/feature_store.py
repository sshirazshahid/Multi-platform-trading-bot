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
    # Cross-sectional / regime context (extension)
    "regime_id_4h",          # 0/1/2 from RegimeHMM (or -1 if HMM unavailable)
    "garch_vol_z_1h",        # GARCH 1-step forecast / realized vol (>1 == elevated)
    "btc_corr_30d_1h",       # rolling Pearson with BTC over last 30d of 1h bars
    "btc_trend_4h",          # sign(BTC ema20-ema50) on 4h
    "funding_zscore_7d",     # symbol funding rate z-scored over 7-day window
    "time_bucket",           # 0..5 hour-of-day session bucket
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


def _hour_bucket(hour: int) -> int:
    """Group UTC hour-of-day into 6 trading sessions (0..5).

    0 = late/early   (00-03)
    1 = Asia         (04-07)
    2 = EU-am        (08-11)
    3 = EU-pm/US-am  (12-15)
    4 = US-pm        (16-19)
    5 = late-US      (20-23)
    """
    h = int(hour) % 24
    return min(5, max(0, h // 4))


def _compute_context(
    ohlcv: dict[str, pd.DataFrame],
    btc_ohlcv: dict[str, pd.DataFrame] | None = None,
    funding_series: pd.Series | None = None,
    now_ts: int | None = None,
) -> dict[str, float]:
    """Cross-sectional / regime context features.

    All inputs are optional — missing inputs collapse to neutral defaults so
    the schema is always fully populated. The trainer treats 0.0 as a
    legitimate "neutral" value for these features.
    """
    out: dict[str, float] = {
        "regime_id_4h":      -1.0,    # -1 sentinel => HMM unavailable
        "garch_vol_z_1h":     1.0,    # neutral (forecast == realized)
        "btc_corr_30d_1h":    0.0,
        "btc_trend_4h":       0.0,
        "funding_zscore_7d":  0.0,
        "time_bucket":        0.0,
    }

    # ── time_bucket ──────────────────────────────────────────────────
    df_1h = ohlcv.get("1h")
    if now_ts is not None:
        try:
            ts_dt = pd.to_datetime(int(now_ts), unit="s", utc=True)
            out["time_bucket"] = float(_hour_bucket(ts_dt.hour))
        except Exception:
            pass
    elif df_1h is not None and len(df_1h) > 0 and "ts" in df_1h.columns:
        try:
            last = pd.to_datetime(int(df_1h["ts"].iloc[-1]), unit="s", utc=True)
            out["time_bucket"] = float(_hour_bucket(last.hour))
        except Exception:
            pass

    # ── btc_trend_4h ─────────────────────────────────────────────────
    if btc_ohlcv is not None:
        btc_4h = btc_ohlcv.get("4h")
        if btc_4h is not None and len(btc_4h) >= 50:
            try:
                c = btc_4h["close"].astype(float)
                out["btc_trend_4h"] = float(np.sign(
                    ema(c, 20).iloc[-1] - ema(c, 50).iloc[-1]
                ))
            except Exception:
                pass

    # ── btc_corr_30d_1h ─────────────────────────────────────────────
    if btc_ohlcv is not None and df_1h is not None:
        btc_1h = btc_ohlcv.get("1h")
        if btc_1h is not None and len(btc_1h) >= 30 and len(df_1h) >= 30:
            try:
                # Align the last 30 days (~720 bars) on the shorter of the two
                n = min(len(btc_1h), len(df_1h), 720)
                r_sym = df_1h["close"].astype(float).pct_change().iloc[-n:]
                r_btc = btc_1h["close"].astype(float).pct_change().iloc[-n:]
                if len(r_sym) > 5 and len(r_btc) > 5:
                    corr = r_sym.corr(r_btc)
                    if corr is not None and np.isfinite(corr):
                        out["btc_corr_30d_1h"] = _safe_float(corr)
            except Exception:
                pass

    # ── garch_vol_z_1h ──────────────────────────────────────────────
    if df_1h is not None and len(df_1h) >= MIN_GARCH_BARS:
        try:
            from core.garch_vol import GARCHVol  # local import; arch is slow
            r = df_1h["close"].astype(float).pct_change().dropna()
            if len(r) >= MIN_GARCH_BARS:
                gv = GARCHVol(p=1, q=1).fit(r.to_numpy() * 100.0)
                fcast = gv.forecast(horizon=1)
                realized = float(r.iloc[-30:].std()) * 100.0
                if realized > 0 and len(fcast) > 0 and np.isfinite(fcast[0]):
                    out["garch_vol_z_1h"] = _safe_float(
                        float(fcast[0]) / realized, default=1.0)
        except Exception:
            # GARCH fit can diverge; neutral default already set.
            pass

    # ── funding_zscore_7d ───────────────────────────────────────────
    if funding_series is not None and len(funding_series) >= 8:
        try:
            fs = pd.Series(funding_series).astype(float).dropna().iloc[-56:]  # 7d * 8 fundings/day
            if len(fs) >= 8:
                mu = float(fs.mean())
                sd = float(fs.std(ddof=1))
                if sd > 0:
                    out["funding_zscore_7d"] = _safe_float(
                        (float(fs.iloc[-1]) - mu) / sd, default=0.0)
        except Exception:
            pass

    return out


# Minimum bars before GARCH(1,1) fit is attempted; arch's optimizer diverges on
# short series. core/garch_vol.py uses MIN_RETURNS=50 — match it here.
MIN_GARCH_BARS = 50


def compute_features_from_ohlcv(
    ohlcv: dict[str, pd.DataFrame],
    btc_ohlcv: dict[str, pd.DataFrame] | None = None,
    funding_series: pd.Series | None = None,
    regime_state: int | None = None,
    now_ts: int | None = None,
) -> dict[str, float]:
    """Pure: OHLCV per-timeframe → flat feature dict.

    Args:
        ohlcv: dict keyed by timeframe (e.g. "4h", "1h", "15m"); each
            value is a DataFrame with columns open/high/low/close/volume.
            Latest row (iloc[-1]) is the "current" bar.
        btc_ohlcv: optional same-shape dict for BTC/USDT used to compute
            cross-sectional context (correlation + trend overlay).
        funding_series: optional pd.Series of historical funding rates for
            this symbol — used for funding_zscore_7d.
        regime_state: optional pre-computed RegimeHMM state id (0/1/2).
            When None the feature falls back to its sentinel -1.
        now_ts: optional unix-seconds timestamp for time_bucket. Falls back
            to ohlcv['1h']['ts'].iloc[-1] when omitted.

    Returns:
        dict[str, float] with every key in FEATURE_SCHEMA.

    Missing timeframes / context inputs collapse to neutral defaults; the
    schema guarantee is unconditional.
    """
    feats: dict[str, float] = {}
    feats.update(_compute_4h(ohlcv.get("4h")))
    feats.update(_compute_1h(ohlcv.get("1h")))
    feats.update(_compute_15m(ohlcv.get("15m")))
    feats.update(_compute_context(ohlcv, btc_ohlcv, funding_series, now_ts))
    if regime_state is not None:
        feats["regime_id_4h"] = float(int(regime_state))
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


OHLCV_CACHE_DIR = ROOT / "data" / "ohlcv_cache"

_TF_TO_SECONDS = {
    "1m":  60,
    "5m":  5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h":  60 * 60,
    "4h":  4 * 60 * 60,
    "1d":  24 * 60 * 60,
}


def _cache_path(symbol: str, timeframe: str) -> Path:
    """Filesystem-safe cache path for a (symbol, timeframe) pair."""
    safe = symbol.replace("/", "-").replace(":", "")
    OHLCV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return OHLCV_CACHE_DIR / f"{safe}_{timeframe}.parquet"


def load_ohlcv_window(
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
    *,
    fetcher=None,
) -> pd.DataFrame:
    """Read OHLCV from the parquet cache, filling gaps via `fetcher`.

    Schema: columns ['ts', 'open', 'high', 'low', 'close', 'volume'] sorted
    by ts ascending. `ts` is unix seconds. Idempotent — repeated calls with
    the same window are served from disk.

    Args:
        symbol:    canonical symbol e.g. 'BTC/USDT' or 'BTC/USDT:USDT'.
        timeframe: '1h' / '4h' / '15m' (any key in `_TF_TO_SECONDS`).
        start_ts:  inclusive lower bound (unix seconds).
        end_ts:    inclusive upper bound (unix seconds).
        fetcher:   callable(symbol, timeframe, since_ms, limit) -> list of
                   [[ts_ms, open, high, low, close, volume], ...]. When None,
                   only cache contents are returned (no network).

    Returns: DataFrame with all bars in [start_ts, end_ts]. Empty when neither
    cache nor fetcher provides data.
    """
    if timeframe not in _TF_TO_SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    tf_sec = _TF_TO_SECONDS[timeframe]
    p = _cache_path(symbol, timeframe)
    df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    if p.exists():
        try:
            df = pd.read_parquet(p)
        except Exception:
            df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = df["ts"].astype("int64")
    df = df.sort_values("ts").drop_duplicates(subset="ts", keep="last")

    have_start = int(df["ts"].iloc[0]) if len(df) else None
    have_end   = int(df["ts"].iloc[-1]) if len(df) else None
    need_left  = (have_start is None) or (start_ts < have_start)
    need_right = (have_end is None)   or (end_ts   > have_end)

    if (need_left or need_right) and fetcher is not None:
        # Fetch in 1500-bar chunks (Binance/ccxt limit) starting at the gap.
        chunks: list[pd.DataFrame] = [df] if len(df) else []
        cursor_ms = int(start_ts * 1000)
        end_ms = int(end_ts * 1000)
        guard = 0
        while cursor_ms < end_ms and guard < 200:
            guard += 1
            try:
                rows = fetcher(symbol, timeframe, cursor_ms, 1500)
            except Exception:
                break
            if not rows:
                break
            new_df = pd.DataFrame(
                rows, columns=["ts", "open", "high", "low", "close", "volume"]
            )
            new_df["ts"] = (new_df["ts"].astype("int64") // 1000)
            chunks.append(new_df)
            last_ms = int(rows[-1][0])
            if last_ms <= cursor_ms:
                break
            cursor_ms = last_ms + tf_sec * 1000
        if chunks:
            df = pd.concat(chunks, ignore_index=True)
            df["ts"] = df["ts"].astype("int64")
            df = df.sort_values("ts").drop_duplicates(subset="ts", keep="last")
            try:
                df.to_parquet(p, index=False)
            except Exception:
                pass

    return df[(df["ts"] >= int(start_ts)) & (df["ts"] <= int(end_ts))].reset_index(drop=True)


class FeatureStore:
    """Persistence wrapper around `Warehouse.features` table."""

    def __init__(self, warehouse: Warehouse) -> None:
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
