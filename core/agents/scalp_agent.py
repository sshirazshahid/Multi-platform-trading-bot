"""ScalpAgent — 5m/15m candle-primary entries (Phase A reading-a).

Per user directive 2026-05-02: futures trades taken by 5m/15m timeframe.
Signal: 5m EMA9/EMA21 cross + RSI sweet spot + 15m alignment + 4h trend
filter (blocks counter-trend, does not gate aligned).

SL: 0.5x ATR(5m, 14) — tighter than live's 1.5x ATR(1h)
TP: 1.5 × SL distance — lower R:R reflects shorter horizon
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import atr, ema, rsi


class ScalpAgent(BaseAgent):

    def __init__(self):
        super().__init__("ScalpAgent")

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv_5m: pd.DataFrame = ctx.get("ohlcv_5m")
        ohlcv_15m: pd.DataFrame = ctx.get("ohlcv_15m")
        ohlcv_4h: pd.DataFrame = ctx.get("ohlcv_4h")
        if ohlcv_5m is None or ohlcv_15m is None or ohlcv_4h is None:
            return None
        if len(ohlcv_5m) < 50 or len(ohlcv_15m) < 30 or len(ohlcv_4h) < 30:
            return None

        c5, c15, c4 = ohlcv_5m["close"], ohlcv_15m["close"], ohlcv_4h["close"]
        e9_5, e21_5 = ema(c5, 9).iloc[-1], ema(c5, 21).iloc[-1]
        e9_5_prev, e21_5_prev = ema(c5, 9).iloc[-2], ema(c5, 21).iloc[-2]
        e9_15, e21_15 = ema(c15, 9).iloc[-1], ema(c15, 21).iloc[-1]
        e20_4, e50_4 = ema(c4, 20).iloc[-1], ema(c4, 50).iloc[-1]
        rsi_5 = rsi(c5, 14).iloc[-1]
        atr_5 = atr(ohlcv_5m, 14).iloc[-1]
        last = c5.iloc[-1]

        if pd.isna(atr_5) or atr_5 <= 0:
            return None

        # 5m fresh cross detection
        cross_up = e9_5_prev <= e21_5_prev and e9_5 > e21_5
        cross_down = e9_5_prev >= e21_5_prev and e9_5 < e21_5

        # 15m alignment + 4h trend filter (block counter-trend)
        long_ok = cross_up and e9_15 > e21_15 and 40 <= rsi_5 <= 70 and e20_4 >= e50_4
        short_ok = cross_down and e9_15 < e21_15 and 30 <= rsi_5 <= 60 and e20_4 <= e50_4

        if long_ok:
            sl = last - 0.5 * atr_5
            tp = last + 1.5 * (last - sl)
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="buy",
                entry=last, sl=sl, tp=tp,
                confidence=0.62,
                reason=f"scalp_long_5m_cross rsi={rsi_5:.0f}",
            )
        if short_ok:
            sl = last + 0.5 * atr_5
            tp = last - 1.5 * (sl - last)
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side="sell",
                entry=last, sl=sl, tp=tp,
                confidence=0.62,
                reason=f"scalp_short_5m_cross rsi={rsi_5:.0f}",
            )
        return None
