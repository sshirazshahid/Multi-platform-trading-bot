"""MeanReversionAgent — RSI extremes on 15m + Bollinger touch + ADX chop filter."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import adx, atr, rsi


class MeanReversionAgent(BaseAgent):

    def __init__(self):
        super().__init__("MeanReversionAgent")

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv_15m: pd.DataFrame = ctx.get("ohlcv_15m")
        if ohlcv_15m is None or len(ohlcv_15m) < 40:
            return None

        c = ohlcv_15m["close"]
        r = rsi(c, 14).iloc[-1]
        a = adx(ohlcv_15m, 14).iloc[-1]
        at = atr(ohlcv_15m, 14).iloc[-1]
        last = c.iloc[-1]
        ma20 = c.rolling(20).mean().iloc[-1]
        sd20 = c.rolling(20).std(ddof=0).iloc[-1]
        if pd.isna(at) or at <= 0 or pd.isna(sd20) or sd20 <= 0:
            return None

        # ADX chop filter — only fire in low-trend conditions
        if pd.notna(a) and a >= 25:
            return None

        upper, lower = ma20 + 2 * sd20, ma20 - 2 * sd20

        long_ok = r < 25 and last <= lower * 1.005
        short_ok = r > 75 and last >= upper * 0.995

        if long_ok:
            sl = last - 1.0 * at
            tp = ma20  # mean-revert to the mean
            if tp <= last:
                return None
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="buy",
                entry=last, sl=sl, tp=tp,
                confidence=0.55,
                reason=f"mr_long rsi={r:.0f} adx={a:.0f}",
            )
        if short_ok:
            sl = last + 1.0 * at
            tp = ma20
            if tp >= last:
                return None
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="sell",
                entry=last, sl=sl, tp=tp,
                confidence=0.55,
                reason=f"mr_short rsi={r:.0f} adx={a:.0f}",
            )
        return None
