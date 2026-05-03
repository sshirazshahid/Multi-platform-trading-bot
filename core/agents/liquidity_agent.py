"""LiquidityAgent — stop-hunt detection + order-book imbalance.

Detects two complementary signals:
  - Sweep: last 1h bar wicks beyond recent N-bar swing high/low but closes
    back inside the range → likely stop hunt + reversal opportunity
  - OB imbalance: top-N depth ratio > threshold → directional pressure

Either alone can fire; both together → stronger confidence.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import atr


def _detect_sweep(ohlcv: pd.DataFrame, lookback: int = 20) -> Optional[dict]:
    """Last bar wicks past N-bar swing extreme then closes inside."""
    if len(ohlcv) < lookback + 2:
        return None
    last = ohlcv.iloc[-1]
    prior = ohlcv.iloc[-(lookback + 1):-1]
    swing_high = float(prior["high"].max())
    swing_low = float(prior["low"].min())
    high, low, close = float(last["high"]), float(last["low"]), float(last["close"])

    sweep_high = high > swing_high and close < swing_high
    sweep_low = low < swing_low and close > swing_low

    if sweep_high:
        return {"direction": "sell", "swept_level": swing_high,
                "wick_excess": (high - swing_high) / swing_high}
    if sweep_low:
        return {"direction": "buy", "swept_level": swing_low,
                "wick_excess": (swing_low - low) / swing_low}
    return None


def _ob_imbalance(orderbook: dict, depth: int = 5) -> float:
    """Top-N depth ratio: positive = bid-heavy, negative = ask-heavy.

    Range roughly [-1, +1] after the normalize.
    """
    if not orderbook:
        return 0.0
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid_notional = sum(p * q for p, q in bids[:depth])
    ask_notional = sum(p * q for p, q in asks[:depth])
    total = bid_notional + ask_notional
    if total <= 0:
        return 0.0
    return (bid_notional - ask_notional) / total


class LiquidityAgent(BaseAgent):

    SWEEP_MIN_EXCESS = 0.0005   # 0.05% excursion past swing
    OB_STRONG_THRESHOLD = 0.30  # 30% imbalance to count as confirming

    def __init__(self):
        super().__init__("LiquidityAgent")

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv: pd.DataFrame = ctx.get("ohlcv_1h")
        if ohlcv is None or len(ohlcv) < 25:
            return None
        sweep = _detect_sweep(ohlcv, lookback=20)
        if sweep is None or sweep["wick_excess"] < self.SWEEP_MIN_EXCESS:
            return None

        atr_v = atr(ohlcv, 14).iloc[-1]
        if pd.isna(atr_v) or atr_v <= 0:
            return None
        last = float(ohlcv["close"].iloc[-1])

        # OB imbalance optional confirmation
        ob = ctx.get("orderbook")
        imb = _ob_imbalance(ob) if ob else 0.0

        side = sweep["direction"]
        # Confirmation: imbalance should agree (bid-heavy for buy, ask-heavy for sell)
        # If contradicts strongly, skip
        if side == "buy" and imb < -self.OB_STRONG_THRESHOLD:
            return None
        if side == "sell" and imb > self.OB_STRONG_THRESHOLD:
            return None

        if side == "buy":
            sl = last - 0.8 * atr_v
            tp = last + 1.5 * (last - sl)
        else:
            sl = last + 0.8 * atr_v
            tp = last - 1.5 * (sl - last)

        # Confidence: base 0.55 + 0.10 if OB confirms direction
        conf = 0.55
        if (side == "buy" and imb > self.OB_STRONG_THRESHOLD) or \
           (side == "sell" and imb < -self.OB_STRONG_THRESHOLD):
            conf = 0.65

        return Proposal(
            agent_id=self.name, symbol=ctx["symbol"], side=side,
            entry=last, sl=sl, tp=tp, confidence=conf,
            reason=f"sweep:{side} excess={sweep['wick_excess']*100:.2f}% ob_imb={imb:+.2f}",
        )
