"""core/spot_regime.py — Spot S1 defensive allocation + rebalance (Rev 5 Phase 5).

Risk control, NOT alpha: a daily-EMA200 regime helper on BTC+ETH (closed daily
candles only) maps to long-only allocation targets for BTC/ETH/SOL/BNB/USDT.
A rebalance is proposed only when allocation drift >= the threshold AND the
expected risk-reduction benefit exceeds fees+slippage (``core.cost_model``).

No leverage, no shorting — enforced by construction (all weights >= 0, sum <= 1)
and validated at decision time. This module is pure/advisory: it never places
orders. It is wired through ``SpotPortfolioManager.run_s1_cycle`` which is
opt-in (``config.SPOT_S1['enabled']``, default False) and recommendation-only.
"""
from __future__ import annotations

from core.cost_model import round_trip_cost

REGIME_NORMAL = "NORMAL"
REGIME_REDUCED = "REDUCED"
REGIME_DEFENSIVE = "DEFENSIVE"

EMA_PERIOD = 200
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
            "regime_signal": "daily_ema200_btc_eth_closed_candles",
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
