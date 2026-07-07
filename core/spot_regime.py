"""core/spot_regime.py — Spot S1 defensive allocation + rebalance (Rev 5 Phase 5).

Risk control, NOT alpha: a daily SMA200 +/- ATR14 regime helper on BTC+ETH
(closed daily candles only) maps to long-only allocation targets for
BTC/ETH/SOL/BNB/USDT.
A rebalance is proposed only when allocation drift >= the threshold AND the
expected risk-reduction benefit exceeds fees+slippage (``core.cost_model``).

No leverage, no shorting — enforced by construction (all weights >= 0, sum <= 1)
and validated at decision time. This module is pure/advisory: it never places
orders. It is wired through ``SpotPortfolioManager.run_s1_cycle`` which is
opt-in (``config.SPOT_S1['enabled']``, default False) and recommendation-only.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from core.cost_model import round_trip_cost
from utils.indicators import atr as _atr_series
from utils.indicators import sma as _sma_series

REGIME_NORMAL = "NORMAL"
REGIME_REDUCED = "REDUCED"
REGIME_DEFENSIVE = "DEFENSIVE"

EMA_PERIOD = 200
S1_SMA_PERIOD = 200
S1_ATR_PERIOD = 14
S1_BAND_ATR_MULT = 0.5
S1_CONFIRM_CLOSES = 3
# Hysteresis band around the EMA: band = mult x mean(|close-to-close delta|)
# over the last HYSTERESIS_BAND_LOOKBACK deltas (whipsaw damping, W4).
HYSTERESIS_BAND_MULT = 0.5
HYSTERESIS_BAND_LOOKBACK = 14
DRIFT_THRESHOLD = 0.05
# Expected risk-reduction value per $1 of drift notional closed (conservative
# placeholder; a paper soak calibrates it). Compared against per-side spot cost.
DEFAULT_BENEFIT_RATE = 0.005

S1_COINS = ("BTC", "ETH", "SOL", "BNB")

# Long-only targets per regime (weights of total spot portfolio incl. USDT).
ALLOCATION_TARGETS = {
    REGIME_NORMAL: {"BTC": 0.35, "ETH": 0.25, "SOL": 0.10, "BNB": 0.05, "USDT": 0.25},
    REGIME_REDUCED: {"BTC": 0.20, "ETH": 0.10, "SOL": 0.05, "BNB": 0.05, "USDT": 0.60},
    REGIME_DEFENSIVE: {"BTC": 0.05, "ETH": 0.05, "SOL": 0.0, "BNB": 0.0, "USDT": 0.90},
}

_DAY_SEC = 86400


# ── candles / regime ───────────────────────────────────────────────────
def closed_daily_closes(candles: list, now_ts: float) -> list:
    """Closes of COMPLETED daily candles only (ccxt-style rows, ts in ms).

    A daily candle opened at ``t`` is closed once ``now_ts >= t + 86400`` —
    the in-progress candle is dropped so the regime never flips intraday.
    """
    out = []
    for c in candles or []:
        open_sec = float(c[0]) / 1000.0
        if now_ts >= open_sec + _DAY_SEC:
            out.append(float(c[4]))
    return out


def closed_daily_ohlc(candles: list, now_ts: float) -> list[dict]:
    """Completed daily candle OHLC rows from ccxt-style candles.

    Returns ``{"ts","high","low","close"}`` rows and drops the still-open
    daily candle using the same completed-candle rule as ``closed_daily_closes``.
    """
    out = []
    for c in candles or []:
        open_sec = float(c[0]) / 1000.0
        if now_ts >= open_sec + _DAY_SEC:
            out.append({
                "ts": open_sec,
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
            })
    return out


def _normalise_ohlc(rows: list) -> list[dict]:
    """Accept v2 OHLC dicts or legacy close lists for test/back-compat callers."""
    out = []
    for i, row in enumerate(rows or []):
        if isinstance(row, dict):
            close = float(row["close"])
            high = float(row.get("high", close))
            low = float(row.get("low", close))
            ts = row.get("ts", i)
        else:
            close = float(row)
            high = low = close
            ts = i
        out.append({"ts": ts, "high": high, "low": low, "close": close})
    return out


def _ema(closes: list, period: int) -> float:
    mult = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for v in closes[period:]:
        ema = (v - ema) * mult + ema
    return ema


def daily_ema200_regime(
    btc_closes: list, eth_closes: list, *, ema_period: int = EMA_PERIOD
) -> str:
    """Regime from closed daily closes: both above EMA200 -> NORMAL,
    exactly one below -> REDUCED, both below -> DEFENSIVE."""
    for name, closes in (("BTC", btc_closes), ("ETH", eth_closes)):
        if len(closes) < ema_period:
            raise ValueError(
                f"{name}: need >= {ema_period} closed daily candles, got {len(closes)}"
            )
    above = sum(
        1 for closes in (btc_closes, eth_closes)
        if closes[-1] > _ema(closes, ema_period)
    )
    return {2: REGIME_NORMAL, 1: REGIME_REDUCED, 0: REGIME_DEFENSIVE}[above]


def daily_ema200_regime_hysteresis(
    btc_closes: list,
    eth_closes: list,
    *,
    ema_period: int = EMA_PERIOD,
    band_mult: float = HYSTERESIS_BAND_MULT,
    band_lookback: int = HYSTERESIS_BAND_LOOKBACK,
    prev_states: dict | None = None,
) -> tuple:
    """Whipsaw-damped EMA200 regime: ``(regime, {"BTC"/"ETH": "above"|"below"})``.

    Per symbol the band is ``band_mult`` x mean(|close-to-close delta|) over the
    last ``band_lookback`` deltas. Beyond ``ema +/- band`` the state flips;
    inside the band the previous state (``prev_states``) is retained. Cold start
    inside the band falls back to the stateless sign, so the FIRST evaluation is
    bit-identical to ``daily_ema200_regime``. Closed-candle discipline is the
    caller's job.
    """
    states = {}
    for name, closes in (("BTC", btc_closes), ("ETH", eth_closes)):
        if len(closes) < ema_period:
            raise ValueError(
                f"{name}: need >= {ema_period} closed daily candles, got {len(closes)}"
            )
        ema = _ema(closes, ema_period)
        close = closes[-1]
        deltas = [
            abs(closes[i] - closes[i - 1])
            for i in range(len(closes) - band_lookback, len(closes))
        ]
        band = band_mult * (sum(deltas) / band_lookback)
        if close > ema + band:
            state = "above"
        elif close < ema - band:
            state = "below"
        else:
            prev = (prev_states or {}).get(name)
            if prev in ("above", "below"):
                state = prev
            else:  # cold start inside the band -> stateless sign fallback
                state = "above" if close > ema else "below"
        states[name] = state
    above = sum(1 for s in states.values() if s == "above")
    return {2: REGIME_NORMAL, 1: REGIME_REDUCED, 0: REGIME_DEFENSIVE}[above], states


def _legacy_state_value(raw) -> tuple[Optional[str], int, Optional[str], Optional[object]]:
    if isinstance(raw, dict):
        state = raw.get("state")
        streak = int(raw.get("streak") or 0)
        streak_dir = raw.get("streak_dir")
        return state if state in ("above", "below") else None, streak, (
            streak_dir if streak_dir in ("above", "below") else None
        ), raw.get("last_bar_ts")
    if raw in ("above", "below"):
        return raw, 0, None, None
    return None, 0, None, None


def daily_sma200_atr_regime(
    btc_ohlc: list,
    eth_ohlc: list,
    *,
    sma_period: int = S1_SMA_PERIOD,
    atr_period: int = S1_ATR_PERIOD,
    band_mult: float = S1_BAND_ATR_MULT,
    confirm_closes: int = S1_CONFIRM_CLOSES,
    prev_states: Optional[dict] = None,
) -> tuple:
    """SMA200 +/- ATR14 regime with 3-closed-bar confirmation.

    State flips only after ``confirm_closes`` consecutive completed bars beyond
    the opposite band. Bars inside the band reset the pending streak and retain
    the previous state. Returned state is schema-v2:
    ``{"state","streak","streak_dir","last_bar_ts"}``.
    """
    states = {}
    min_history = max(int(sma_period), int(atr_period) + 1)
    for name, raw_rows in (("BTC", btc_ohlc), ("ETH", eth_ohlc)):
        rows = _normalise_ohlc(raw_rows)
        if len(rows) < min_history:
            raise ValueError(
                f"{name}: need >= {min_history} closed daily candles, got {len(rows)}"
            )
        high = pd.Series([r["high"] for r in rows], dtype=float)
        low = pd.Series([r["low"] for r in rows], dtype=float)
        close = pd.Series([r["close"] for r in rows], dtype=float)
        last_close = float(close.iloc[-1])
        sma_v = float(_sma_series(close, int(sma_period)).iloc[-1])
        atr_v = float(_atr_series(high, low, close, int(atr_period)).iloc[-1])
        if not all(math.isfinite(x) for x in (last_close, sma_v, atr_v)):
            raise ValueError(f"{name}: non-finite SMA/ATR regime input")

        raw_prev = (prev_states or {}).get(name)
        prev_state, prev_streak, prev_streak_dir, prev_ts = _legacy_state_value(raw_prev)
        last_ts = rows[-1].get("ts", len(rows) - 1)

        # Same-bar idempotency guard — ONLY for genuine epoch timestamps.
        # Plain-close inputs get fabricated index ts (0..n-1); a fixed-length
        # rolling window then repeats last_ts (e.g. 249) on every call, and an
        # unconditional guard would freeze the persisted state FOREVER (a crash
        # could never flip the regime to DEFENSIVE). Epochs (sec or ms) are
        # always > 1e9; enumerate indices never are.
        _is_epoch = isinstance(last_ts, (int, float)) and float(last_ts) > 1e9
        if prev_state in ("above", "below") and _is_epoch and prev_ts == last_ts:
            states[name] = {
                "state": prev_state,
                "streak": prev_streak,
                "streak_dir": prev_streak_dir,
                "last_bar_ts": last_ts,
            }
            continue

        cold = prev_state not in ("above", "below")
        state = prev_state if not cold else ("above" if last_close > sma_v else "below")

        # Confirmation streak, computed STATELESSLY from the trailing bars of
        # the supplied history (each bar judged against its own SMA/ATR band).
        # The persisted streak counter counted function INVOCATIONS, so bars
        # that closed while the process was down were never inspected: a streak
        # could carry across resetting bars (flipping after only 2 truly
        # consecutive closes) or legitimate closes were never counted. The
        # documented contract is "confirm_closes consecutive COMPLETED BARS".
        sma_s = _sma_series(close, int(sma_period))
        atr_s = _atr_series(high, low, close, int(atr_period))

        def _bar_dir(j: int, close=close, sma_s=sma_s, atr_s=atr_s):
            c, s_v, a_v = float(close.iloc[j]), float(sma_s.iloc[j]), float(atr_s.iloc[j])
            if not (math.isfinite(c) and math.isfinite(s_v) and math.isfinite(a_v)):
                return None
            if c > s_v + float(band_mult) * a_v:
                return "above"
            if c < s_v - float(band_mult) * a_v:
                return "below"
            return None

        signal_dir = _bar_dir(len(close) - 1)
        trailing = 0
        if signal_dir is not None:
            j = len(close) - 1
            while j >= 0 and trailing < int(confirm_closes) and _bar_dir(j) == signal_dir:
                trailing += 1
                j -= 1

        streak = 0
        streak_dir = None
        if signal_dir is None:
            pass
        elif cold or signal_dir == state:
            state = signal_dir if cold else state
        else:
            streak = trailing
            streak_dir = signal_dir
            if streak >= int(confirm_closes):
                state = signal_dir
                streak = 0
                streak_dir = None

        states[name] = {
            "state": state,
            "streak": int(streak),
            "streak_dir": streak_dir,
            "last_bar_ts": last_ts,
        }

    above = sum(1 for s in states.values() if s["state"] == "above")
    return {2: REGIME_NORMAL, 1: REGIME_REDUCED, 0: REGIME_DEFENSIVE}[above], states


# ── rebalance decision ─────────────────────────────────────────────────
def _validate_weights(weights: dict, label: str) -> None:
    for coin, w in weights.items():
        if w < 0.0:
            raise ValueError(f"{label} weight {coin}={w} < 0 (shorting forbidden)")
    total = sum(weights.values())
    if total > 1.0 + 1e-9:
        raise ValueError(f"{label} weights sum {total:.4f} > 1 (leverage forbidden)")


def rebalance_decision(
    current_weights: dict,
    regime: str,
    *,
    portfolio_value: float,
    venue: str = "binance",
    drift_threshold: float = DRIFT_THRESHOLD,
    benefit_rate: float = DEFAULT_BENEFIT_RATE,
) -> dict:
    """Long-only rebalance proposal toward the regime's allocation targets.

    Acts only when max per-coin drift >= ``drift_threshold`` AND the expected
    benefit (``benefit_rate`` x drift notional) exceeds the per-side spot
    fee+slippage cost from ``core.cost_model``. Returns an advisory dict —
    never touches an exchange.
    """
    if regime not in ALLOCATION_TARGETS:
        raise ValueError(f"unknown regime {regime!r}")
    targets = ALLOCATION_TARGETS[regime]
    _validate_weights(current_weights, "current")
    _validate_weights(targets, "target")

    deltas = {c: targets[c] - float(current_weights.get(c, 0.0)) for c in S1_COINS}
    max_drift = max(abs(d) for d in deltas.values())
    base = {
        "regime": regime, "max_drift": max_drift,
        "trades": [], "est_cost": 0.0, "est_benefit": 0.0,
    }
    if max_drift < drift_threshold:
        return {**base, "action": "HOLD",
                "reason": f"drift {max_drift:.1%} < {drift_threshold:.0%}"}

    trades = []
    for coin, d in sorted(deltas.items()):
        notional = abs(d) * portfolio_value
        if notional <= 0:
            continue
        side = "BUY" if d > 0 else "SELL"
        if side == "SELL":
            held = float(current_weights.get(coin, 0.0)) * portfolio_value
            assert notional <= held + 1e-9, "SELL exceeds holdings (shorting forbidden)"
        trades.append({"coin": coin, "side": side, "notional": notional})

    turnover = sum(t["notional"] for t in trades)
    per_side_cost = round_trip_cost(venue, market_type="spot") / 2.0
    est_cost = turnover * per_side_cost
    est_benefit = turnover * benefit_rate
    if est_benefit <= est_cost:
        return {**base, "action": "HOLD", "est_cost": est_cost,
                "est_benefit": est_benefit,
                "reason": f"benefit {est_benefit:.4f} <= cost {est_cost:.4f}"}
    return {**base, "action": "REBALANCE", "trades": trades,
            "est_cost": est_cost, "est_benefit": est_benefit,
            "reason": f"drift {max_drift:.1%} >= {drift_threshold:.0%}, benefit > cost"}


# ── StrategySpec ───────────────────────────────────────────────────────
def build_spot_s1_spec():
    """Declarative ``StrategySpec`` for Spot S1 (risk control; no orders)."""
    from core.strategy_spec import StrategySpec

    return StrategySpec(
        id="SPOT_S1_DEFENSIVE",
        family="risk_control",
        market_type="spot",
        venues=["binance"],
        symbols=[f"{c}/USDT" for c in S1_COINS],
        data_required=["ohlcv_1d", "spot_balances", "fees"],
        entry_rules={
            "type": "regime_allocation",
            "regime_signal": "daily_sma200_atr14_3close_btc_eth_closed_candles",
            "targets": ALLOCATION_TARGETS,
            "drift_threshold": DRIFT_THRESHOLD,
            "benefit_must_exceed_costs": True,
        },
        exit_rules={"rebalance_to_target": True, "defensive_max_usdt": True},
        sizing={"long_only": True, "weights_sum_max": 1.0},
        risk_limits={"max_leverage": 1.0, "shorting": False},
        validation_status="untested",
        promotion_status="paper_advisory",
    )
