"""TPProbeAgent — geometric TP-accuracy probe (2026-07-05 owner /goal).

The owner's goal: "75-80% accuracy hitting TP on any coin, any pair, futures."
TP-hit probability is set by TP/SL GEOMETRY, not by prediction: with stop
distance s and target distance t, a driftless market hits TP first with
p ≈ s/(s+t). This agent takes the momentum side on ANY symbol it is shown
(the shadow lane feeds it the day's biggest movers) with t = s/3.5 —
theoretical TP-hit ≈ 3.5/4.5 ≈ 77.8%, inside the owner's 75-80% band.

It exists to MEASURE that ceiling honestly in the log-only lane, where the
forward resolver scores every proposal after fees and SL-first wick logic.
The expected outcome is a hit-rate near 78% with ~zero-to-negative after-cost
net — hit-rate must NEVER be read without the resolved net_pnl next to it.
If the resolved record ever shows the hit-rate AND positive after-cost net,
the standard promotion gate (core/promotion_gate.py) applies unchanged.

Log-only: proposals flow to shadow_decisions via the coordinator; nothing
here can place an order.
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import atr, ema

SL_ATR_MULT = 1.5          # stop: 1.5 x ATR(15m,14) — wide enough not to wick
TP_FRACTION_OF_SL = 1 / 3.5  # target: s/3.5 -> p(TP first) ~ 77.8% by geometry
COOLDOWN_S = 3600          # one probe per symbol per hour (DB hygiene)


class TPProbeAgent(BaseAgent):

    def __init__(self):
        super().__init__("TPProbeAgent")
        self._last_fire: dict[str, float] = {}

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv_15m: pd.DataFrame = ctx.get("ohlcv_15m")
        if ohlcv_15m is None or len(ohlcv_15m) < 30:
            return None
        symbol = ctx.get("symbol")
        if not symbol:
            return None
        now = time.time()
        if now - self._last_fire.get(symbol, 0.0) < COOLDOWN_S:
            return None

        c15 = ohlcv_15m["close"]
        e9, e21 = ema(c15, 9).iloc[-1], ema(c15, 21).iloc[-1]
        atr_15 = atr(ohlcv_15m, 14).iloc[-1]
        last = float(c15.iloc[-1])
        if pd.isna(atr_15) or atr_15 <= 0 or pd.isna(e9) or pd.isna(e21):
            return None
        if e9 == e21:
            return None  # no momentum side to take

        side = "buy" if e9 > e21 else "sell"
        s_dist = SL_ATR_MULT * float(atr_15)
        t_dist = s_dist * TP_FRACTION_OF_SL
        if side == "buy":
            sl, tp = last - s_dist, last + t_dist
        else:
            sl, tp = last + s_dist, last - t_dist

        self._last_fire[symbol] = now
        return Proposal(
            agent_id=self.name, symbol=symbol, side=side,
            entry=last, sl=sl, tp=tp,
            confidence=0.50,
            reason=f"tp_probe geometric p~{1 / (1 + TP_FRACTION_OF_SL):.0%}",
            venue=str(ctx.get("venue") or "binance"),
            timeframe="15m",
            horizon_bars=32,  # 8h on 15m bars: enough for either barrier
        )
