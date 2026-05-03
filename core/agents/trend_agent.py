"""TrendAgent — 1h/4h trend-aligned entries.

Mirrors the live MCPBrain 4-required-condition spirit:
  R1: 4h EMA gap >= 0.15% (strong trend)
  R2: 1h EMA20 above EMA50 (longs) / below (shorts) - alignment
  R3: 1h RSI in sweet spot (40-65 longs / 35-60 shorts)
  R4: 1h ADX >= 20 (trend strength)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import adx, atr, ema, rsi


class TrendAgent(BaseAgent):

    def __init__(self):
        super().__init__("TrendAgent")

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv_1h: pd.DataFrame = ctx.get("ohlcv_1h")
        ohlcv_4h: pd.DataFrame = ctx.get("ohlcv_4h")
        if ohlcv_1h is None or ohlcv_4h is None:
            return None
        if len(ohlcv_1h) < 60 or len(ohlcv_4h) < 30:
            return None

        c1, c4 = ohlcv_1h["close"], ohlcv_4h["close"]
        ema20_4h, ema50_4h = ema(c4, 20).iloc[-1], ema(c4, 50).iloc[-1]
        ema20_1h, ema50_1h = ema(c1, 20).iloc[-1], ema(c1, 50).iloc[-1]
        rsi_1h = rsi(c1, 14).iloc[-1]
        adx_1h = adx(ohlcv_1h, 14).iloc[-1]
        atr_1h = atr(ohlcv_1h, 14).iloc[-1]
        last = c1.iloc[-1]

        gap_pct = abs(ema20_4h - ema50_4h) / ema50_4h * 100
        if gap_pct < 0.15:
            return None
        if adx_1h < 20 or pd.isna(adx_1h):
            return None
        if pd.isna(atr_1h) or atr_1h <= 0:
            return None

        long_aligned = ema20_4h > ema50_4h and ema20_1h > ema50_1h and 40 <= rsi_1h <= 65
        short_aligned = ema20_4h < ema50_4h and ema20_1h < ema50_1h and 35 <= rsi_1h <= 60

        if long_aligned:
            sl = last - 1.5 * atr_1h
            tp = last + 2.5 * (last - sl)
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="buy",
                entry=last, sl=sl, tp=tp,
                confidence=min(0.85, 0.55 + min(adx_1h, 50) / 100),
                reason=f"trend_long: 4hgap={gap_pct:.2f}% adx={adx_1h:.0f} rsi={rsi_1h:.0f}",
            )
        if short_aligned:
            sl = last + 1.5 * atr_1h
            tp = last - 2.5 * (sl - last)
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="sell",
                entry=last, sl=sl, tp=tp,
                confidence=min(0.85, 0.55 + min(adx_1h, 50) / 100),
                reason=f"trend_short: 4hgap={gap_pct:.2f}% adx={adx_1h:.0f} rsi={rsi_1h:.0f}",
            )
        return None
