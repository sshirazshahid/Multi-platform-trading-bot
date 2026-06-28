"""Deterministic machine-style strategy engine.

Purpose
-------
Convert multiple causal detectors into one action-shaped decision without
sentiment, discretion, or LLM chart interpretation.

This module is research/shadow-safe by design. It produces decisions; it does
not place orders and it does not bypass the live gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.signals.harmonic_patterns import signal as harmonic_signal
from core.signals.ema_rsi import signal as ema_rsi_signal
from core.signals.ict_variation import signal as ict_signal
from core.signals.session_breakout import signal as asian_range_signal
from core.signals.squeeze_breakout import signal as squeeze_signal
from core.signals.stochastic_crossover import signal as stochastic_signal
from core.signals.swing_structure import signal as swing_signal
from core.signals.valuation_model import signal as valuation_signal

DEFAULT_MACHINE_UNIVERSE = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "DOGE/USDT:USDT",
)

DETECTOR_FUNCS = {
    "valuation": valuation_signal,
    "harmonic": harmonic_signal,
    "stochastic": stochastic_signal,
    "ict": ict_signal,
    "asian_range": asian_range_signal,
    "dow_swing": swing_signal,
    "bb_squeeze": squeeze_signal,
    "ema_rsi": ema_rsi_signal,
}


@dataclass(frozen=True)
class MachineStrategyConfig:
    min_score: float = 0.34
    rr: float = 1.5
    atr_period: int = 14
    atr_stop_mult: float = 1.6
    min_stop_pct: float = 0.0035
    max_stop_pct: float = 0.025
    scalp_time_stop_bars: int = 12
    allow_short_spot: bool = False
    detector_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirmation_detectors: tuple[str, ...] = ()
    unconfirmed_min_score: float = 0.0
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "valuation": 0.14,
            "harmonic": 0.14,
            "stochastic": 0.10,
            "ict": 0.16,
            "asian_range": 0.08,
            "dow_swing": 0.10,
            "bb_squeeze": 0.12,
            "ema_rsi": 0.16,
        }
    )


@dataclass(frozen=True)
class StrategyDecision:
    action: str  # OPEN | HOLD
    symbol: str
    market_type: str
    side: str  # buy | sell | none
    score: float
    confidence: float
    stop_loss_pct: float
    take_profit_pct: float
    time_stop_bars: int
    reasons: tuple[str, ...]
    diagnostics: dict[str, Any]

    def as_action_dict(self) -> dict[str, Any]:
        """Return a bot-engine-like action dict for future wiring."""
        if self.action != "OPEN":
            return {"type": "HOLD", "symbol": self.symbol, "reason": "; ".join(self.reasons)}
        return {
            "type": "OPEN",
            "symbol": self.symbol,
            "exchange": "binance",
            "market_type": self.market_type,
            "side": self.side,
            "leverage": 1,
            "size_pct": 1.0,
            "sl_pct": round(self.stop_loss_pct * 100.0, 3),
            "tp_pct": round(self.take_profit_pct * 100.0, 3),
            "confidence": round(self.confidence, 4),
            "reason": "; ".join(self.reasons),
            "position_id": "",
            "source": "machine_strategy_engine",
        }


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    out = df.copy()
    if "ts" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out["ts"] = (out.index.view("int64") // 1_000_000_000).astype("int64")
        else:
            out["ts"] = np.arange(len(out), dtype="int64") * 60
    return out.reset_index(drop=True)


def _atr_pct(df: pd.DataFrame, period: int) -> float:
    atr = _atr_pct_series(df, period)
    if atr.empty:
        return 0.0
    last = float(atr.iloc[-1])
    return last if np.isfinite(last) else 0.0


def _atr_pct_series(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / max(1, period), adjust=False).mean()
    out = atr / close.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _last_signal(series: pd.Series) -> int:
    if series.empty:
        return 0
    try:
        v = int(series.iloc[-1])
    except Exception:
        return 0
    return 1 if v > 0 else -1 if v < 0 else 0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def detector_signal_frame(
    df: pd.DataFrame,
    detector_params: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Return all detector signal series for a complete OHLCV frame.

    Each detector is causal, so precomputing the full frame is equivalent to
    scoring each prefix one at a time, but far faster for replay/backtests.
    """
    cols: dict[str, pd.Series] = {}
    detector_params = detector_params or {}
    for name, fn in DETECTOR_FUNCS.items():
        try:
            series = fn(df, **dict(detector_params.get(name) or {}))
        except Exception:
            series = pd.Series(0, index=df.index, dtype="int8")
        cols[name] = series.reindex(df.index).fillna(0).astype("int8")
    return pd.DataFrame(cols, index=df.index)


def _coerce_signal_value(value: Any) -> int:
    try:
        v = int(value)
    except Exception:
        return 0
    return 1 if v > 0 else -1 if v < 0 else 0


class MachineStrategyEngine:
    """Score causal pattern detectors into a single OPEN/HOLD decision."""

    def __init__(self, config: MachineStrategyConfig | None = None):
        self.config = config or MachineStrategyConfig()

    def score_frame(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        market_type: str = "futures",
    ) -> StrategyDecision:
        data = _ensure_ohlcv(df)
        cfg = self.config
        if len(data) < 40:
            return StrategyDecision(
                "HOLD", symbol, market_type, "none", 0.0, 0.0, 0.0, 0.0,
                cfg.scalp_time_stop_bars, ("insufficient_data",), {},
            )

        signals = detector_signal_frame(data, cfg.detector_params)
        detector_values = {
            name: _last_signal(signals[name])
            for name in DETECTOR_FUNCS
        }
        atrp = _atr_pct(data, cfg.atr_period)
        return self.decision_from_detector_values(
            symbol,
            detector_values,
            atr_pct=atrp,
            market_type=market_type,
        )

    def detector_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Precompute detector signals for replay and attribution."""
        return detector_signal_frame(_ensure_ohlcv(df), self.config.detector_params)

    def atr_pct_series(self, df: pd.DataFrame) -> pd.Series:
        """Precompute ATR percent for replay and attribution."""
        return _atr_pct_series(_ensure_ohlcv(df), self.config.atr_period)

    def decision_from_detector_values(
        self,
        symbol: str,
        detector_values: dict[str, Any],
        *,
        atr_pct: float,
        market_type: str = "futures",
    ) -> StrategyDecision:
        cfg = self.config
        clean_values = {
            name: _coerce_signal_value(detector_values.get(name, 0))
            for name in DETECTOR_FUNCS
        }

        long_score = 0.0
        short_score = 0.0
        long_reasons: list[str] = []
        short_reasons: list[str] = []
        for name, v in clean_values.items():
            w = float(cfg.weights.get(name, 0.0))
            if v > 0:
                long_score += w
                long_reasons.append(f"{name}=bull")
            elif v < 0:
                short_score += w
                short_reasons.append(f"{name}=bear")

        if market_type == "spot" and not cfg.allow_short_spot:
            short_score = 0.0
            short_reasons = []

        confirmers = tuple(cfg.confirmation_detectors or ())
        long_confirmed = any(clean_values.get(name, 0) > 0 for name in confirmers)
        short_confirmed = any(clean_values.get(name, 0) < 0 for name in confirmers)
        unconfirmed_floor = max(float(cfg.min_score), float(cfg.unconfirmed_min_score or 0.0))
        long_required = float(cfg.min_score) if long_confirmed else unconfirmed_floor
        short_required = float(cfg.min_score) if short_confirmed else unconfirmed_floor

        if long_score >= long_required and long_score >= short_score:
            side = "buy"
            score = long_score
            reasons = tuple(long_reasons)
            required_score = long_required
            confirmed = long_confirmed
        elif short_score >= short_required:
            side = "sell"
            score = short_score
            reasons = tuple(short_reasons)
            required_score = short_required
            confirmed = short_confirmed
        else:
            return StrategyDecision(
                "HOLD", symbol, market_type, "none",
                max(long_score, short_score), max(long_score, short_score),
                0.0, 0.0, cfg.scalp_time_stop_bars,
                ("score_below_threshold",),
                {
                    "detectors": clean_values,
                    "long_score": long_score,
                    "short_score": short_score,
                    "long_required_score": long_required,
                    "short_required_score": short_required,
                    "confirmation_detectors": confirmers,
                    "long_confirmed": long_confirmed,
                    "short_confirmed": short_confirmed,
                },
            )

        atrp = max(0.0, _finite(atr_pct))
        stop_pct = min(cfg.max_stop_pct, max(cfg.min_stop_pct, atrp * cfg.atr_stop_mult))
        tp_pct = stop_pct * cfg.rr
        confidence = max(0.0, min(1.0, score))
        return StrategyDecision(
            "OPEN", symbol, market_type, side, score, confidence,
            stop_pct, tp_pct, cfg.scalp_time_stop_bars, reasons,
            {
                "detectors": clean_values,
                "long_score": long_score,
                "short_score": short_score,
                "required_score": required_score,
                "confirmed_by_detector": confirmed,
                "confirmation_detectors": confirmers,
                "atr_pct": atrp,
                "rr": cfg.rr,
            },
        )


def rank_tradeable_universe(
    tickers: dict[str, dict[str, Any]],
    *,
    max_symbols: int = 12,
    min_quote_volume: float = 10_000_000.0,
    max_spread_bps: float = 12.0,
) -> list[str]:
    """Select liquid symbols from ticker/orderbook metadata.

    Expected per-symbol fields are flexible: ``quoteVolume``/``quote_volume``,
    ``percentage``/``change_pct``, and optional ``spread_bps``.
    """
    rows = []
    for symbol, t in (tickers or {}).items():
        try:
            qv = float(t.get("quoteVolume", t.get("quote_volume", 0.0)) or 0.0)
            spread = float(t.get("spread_bps", 0.0) or 0.0)
            move = abs(float(t.get("percentage", t.get("change_pct", 0.0)) or 0.0))
        except Exception:
            continue
        if qv < min_quote_volume:
            continue
        if spread and spread > max_spread_bps:
            continue
        # Liquidity first, movement second. The robot wants tradable volatility,
        # not illiquid moonshots.
        rank = qv * (1.0 + min(move, 20.0) / 100.0)
        rows.append((rank, symbol))
    rows.sort(reverse=True)
    return [sym for _, sym in rows[:max_symbols]]


def system_blueprint() -> dict[str, Any]:
    """Machine-readable target architecture summary."""
    return {
        "language": "Python for research/execution; SQL/SQLite for audit state",
        "runtime_mode": "PAPER until after-cost walk-forward edge is proven",
        "universe": list(DEFAULT_MACHINE_UNIVERSE),
        "detectors": [
            "valuation fair-value deviation",
            "harmonic ratios",
            "stochastic crossovers",
            "ICT liquidity sweep/FVG variation",
            "Asian range breakout",
            "Dow swing structure",
            "Bollinger squeeze breakout",
            "EMA/RSI trend pullback and midline recovery",
        ],
        "risk": {
            "sizing": "volatility/ATR constrained",
            "stop": "ATR multiple with min/max clamps",
            "take_profit": "fixed RR from stop distance",
            "time_stop": "scalp_time_stop_bars",
        },
        "validation": [
            "causality checks",
            "after-fee/slippage backtest",
            "parameter plateau test",
            "walk-forward OOS",
            "paper shadow promotion only",
        ],
    }
