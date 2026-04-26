"""
core/features.py — Unified feature extractor for the meta-filter + warehouse.

Consolidates the many disparate indicators scattered across `mcp_brain`,
`exchange_indicators`, and the order-book fetchers into a single
`FeatureVector` that every downstream consumer (meta-filter, ClaudeCode
advisory prompts, what-if reports) reads from. This is a *refactor*: we
are not introducing new math here — we are pointing existing inputs
through one entry point so meta-filter decisions become deterministic
and reproducible.

Spec §7 (feature catalog) and §8 (meta-filter inputs).

The key concept is **percentiles**: absolute values are meaningless in
isolation ("ATR = 2.1%" — is that high or low?). Percentiles against
the trailing 30-day warehouse window turn those values into comparable
features across symbols and regimes.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FeatureVector:
    """One row of inputs for the meta-filter + Claude advisory prompts.

    All fields are either floats in [0, 1] (percentiles / normalized rates)
    or small enums / ints. `None` means "not available" and is a valid
    input — the meta-filter treats missing fields as neutral.
    """

    # ── Microstructure ───────────────────────────────────────────────
    spread_pctl:   float | None = None    # spread percentile vs 30d symbol window
    vol_pctl:      float | None = None    # ATR% percentile vs 30d symbol window
    funding_bias:  float | None = None    # raw funding rate, positive => longs paying
    ob_imbalance:  float | None = None    # top-book ratio in [-1, 1]

    # ── Trend / regime ───────────────────────────────────────────────
    trend_strength: float | None = None   # 4h ADX / 100 ∈ [0, 1]
    dist_to_sr:     float | None = None   # distance to S/R as fraction of price
    regime_label:   str   | None = None   # 'trend' | 'chop' | 'squeeze' | 'expansion'

    # ── Time of day ──────────────────────────────────────────────────
    hour_of_day:  int | None = None       # UTC hour 0-23
    day_of_week:  int | None = None       # Monday=0

    # ── Account state ────────────────────────────────────────────────
    recent_loss_streak: int | None = None

    # ── Identity ─────────────────────────────────────────────────────
    exchange_id: str | None = None
    symbol_id:   str | None = None

    # ── Raw scoring inputs (passthrough, for warehouse + debugging) ──
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _pctl(value: float | None, sample: list[float]) -> float | None:
    """Fraction of `sample` that is <= `value`. None on empty sample."""
    if value is None or not sample:
        return None
    below = sum(1 for s in sample if s is not None and s <= value)
    return round(below / len(sample), 4)


def extract(
    *,
    symbol: str,
    exchange: str,
    ei: dict,
    market_data: dict | None = None,
    warehouse=None,
    risk_manager=None,
    now: datetime | None = None,
) -> FeatureVector:
    """Build a FeatureVector from the same inputs `mcp_brain._score_coin` uses.

    Percentiles are computed against the warehouse's trailing 30-day features
    window if a warehouse is provided; otherwise they are left as None (the
    meta-filter will then treat them as neutral).

    Parameters
    ----------
    symbol        : 'BTC/USDT' or 'BTC/USDT:USDT'
    exchange      : lowercase exchange id ('binance', 'bybit', 'bitget')
    ei            : exchange_indicators[coin] dict with '4h' / '1h' / '15m' keys
    market_data   : the MCP data bundle (funding, orderbook, ...)
    warehouse     : optional core.warehouse.Warehouse for percentile windows
    risk_manager  : optional RiskManager for recent_loss_streak
    now           : optional timezone-aware datetime, defaults to UTC now
    """
    market_data = market_data or {}
    now = now or datetime.now(timezone.utc)

    ei_4h = ei.get("4h", {}) or {}
    ei_1h = ei.get("1h", {}) or {}

    coin = symbol.split("/")[0]
    fr = (market_data.get("funding", {}) or {}).get(coin, {}) or {}
    ob = (market_data.get("orderbook", {}) or {}).get(coin, {}) or {}

    # ── Raw values ───────────────────────────────────────────────────
    spread_raw = ob.get("spread_pct")          # %, not fraction
    atr_pct    = ei_1h.get("atr_pct")
    adx_4h     = ei_4h.get("adx")
    funding    = fr.get("funding_rate")
    imb        = ob.get("imbalance")

    # ── Regime label ─────────────────────────────────────────────────
    bb_4h = ei_4h.get("bb_width", 0) or 0
    regime = "trend"
    if adx_4h is None:
        regime = None
    elif adx_4h < 15:
        regime = "chop"
    elif bb_4h < 1.0:
        regime = "squeeze"
    elif bb_4h > 5.0:
        regime = "expansion"

    # ── Distance to nearest S/R (fraction of price) ──────────────────
    last_px  = ei_1h.get("close") or ei_4h.get("close")
    sr_up    = ei_1h.get("resistance") or ei_4h.get("resistance")
    sr_dn    = ei_1h.get("support")    or ei_4h.get("support")
    dist_sr  = None
    if last_px and last_px > 0:
        candidates = [d for d in [
            abs(sr_up - last_px) / last_px if sr_up else None,
            abs(last_px - sr_dn) / last_px if sr_dn else None,
        ] if d is not None]
        if candidates:
            dist_sr = round(min(candidates), 5)

    # ── Percentile windows from warehouse (30d trailing) ─────────────
    spread_pctl = None
    vol_pctl    = None
    if warehouse is not None:
        try:
            import time as _t
            since = _t.time() - 30 * 86400
            sample_rows = warehouse.query(
                "SELECT features_json FROM candidates "
                "WHERE symbol=? AND ts >= ? AND features_json IS NOT NULL",
                (symbol, since),
            )
            sp_sample = []
            at_sample = []
            import json as _j
            for r in sample_rows:
                try:
                    f = _j.loads(r.get("features_json") or "{}")
                except Exception:
                    continue
                if isinstance(f.get("spread_pct"), (int, float)):
                    sp_sample.append(float(f["spread_pct"]))
                if isinstance(f.get("atr_pct_1h"), (int, float)):
                    at_sample.append(float(f["atr_pct_1h"]))
            spread_pctl = _pctl(spread_raw, sp_sample)
            vol_pctl    = _pctl(atr_pct,    at_sample)
        except Exception:
            pass

    # ── Recent loss streak from risk manager ─────────────────────────
    streak = None
    if risk_manager is not None:
        streak = (
            getattr(risk_manager, "_global_streak", None)
            or getattr(risk_manager, "consec_losses", None)
        )

    return FeatureVector(
        spread_pctl=spread_pctl,
        vol_pctl=vol_pctl,
        funding_bias=float(funding) if funding is not None else None,
        ob_imbalance=float(imb) if imb is not None else None,
        trend_strength=(float(adx_4h) / 100.0) if adx_4h is not None else None,
        dist_to_sr=dist_sr,
        regime_label=regime,
        hour_of_day=now.hour,
        day_of_week=now.weekday(),
        recent_loss_streak=int(streak) if streak is not None else None,
        exchange_id=exchange.lower() if exchange else None,
        symbol_id=symbol,
        raw={
            "spread_pct":  spread_raw,
            "atr_pct_1h":  atr_pct,
            "adx_4h":      adx_4h,
            "adx_1h":      ei_1h.get("adx"),
            "bb_width_4h": bb_4h,
            "rsi_1h":      ei_1h.get("rsi"),
            "vol_ratio":   ei_1h.get("vol_ratio"),
            "funding":     funding,
            "ob_imbalance": imb,
        },
    )
