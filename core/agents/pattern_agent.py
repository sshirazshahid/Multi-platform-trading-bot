"""PatternAgent — chart pattern detection on 1h bars.

Detects three pattern families:
  - H&S / Inverse H&S (5 swing points: shoulder-head-shoulder + neckline)
  - Double top / double bottom (2 swing extremes within ~1%)
  - Flag (impulse + tight consolidation, continuation)

All emit Proposals with ATR-based SL and pattern-target TP. Shadow-mode
same safety as the other agents.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .base_agent import BaseAgent, Proposal
from .indicators import atr


def _pivots(close: pd.Series, lookback: int = 5) -> tuple[list[int], list[int]]:
    """Return (high_pivot_indices, low_pivot_indices) — local extrema."""
    n = len(close)
    highs: list[int] = []
    lows: list[int] = []
    for i in range(lookback, n - lookback):
        window = close.iloc[i - lookback:i + lookback + 1]
        if close.iloc[i] == window.max():
            highs.append(i)
        if close.iloc[i] == window.min():
            lows.append(i)
    return highs, lows


def _detect_double_top(close: pd.Series, highs: list[int], tol_pct: float = 1.0) -> Optional[dict]:
    if len(highs) < 2:
        return None
    h1, h2 = highs[-2], highs[-1]
    # require some separation (>= 5 bars apart) and last pivot recent
    if h2 - h1 < 5 or len(close) - 1 - h2 > 8:
        return None
    p1, p2 = close.iloc[h1], close.iloc[h2]
    if abs(p1 - p2) / max(p1, p2) * 100 > tol_pct:
        return None
    neckline = float(close.iloc[h1:h2 + 1].min())
    last = float(close.iloc[-1])
    if last >= neckline:
        return None  # neckline not yet broken
    return {"pattern": "double_top", "side": "sell",
            "neckline": neckline, "peak": float(max(p1, p2))}


def _detect_double_bottom(close: pd.Series, lows: list[int], tol_pct: float = 1.0) -> Optional[dict]:
    if len(lows) < 2:
        return None
    l1, l2 = lows[-2], lows[-1]
    if l2 - l1 < 5 or len(close) - 1 - l2 > 8:
        return None
    p1, p2 = close.iloc[l1], close.iloc[l2]
    if abs(p1 - p2) / max(p1, p2) * 100 > tol_pct:
        return None
    neckline = float(close.iloc[l1:l2 + 1].max())
    last = float(close.iloc[-1])
    if last <= neckline:
        return None
    return {"pattern": "double_bottom", "side": "buy",
            "neckline": neckline, "trough": float(min(p1, p2))}


def _detect_head_shoulders(close: pd.Series, highs: list[int]) -> Optional[dict]:
    """H&S = 3 highs with middle highest, similar outers."""
    if len(highs) < 3:
        return None
    a, b, c = highs[-3], highs[-2], highs[-1]
    if c - a > 60:  # too spread out
        return None
    pa, pb, pc = float(close.iloc[a]), float(close.iloc[b]), float(close.iloc[c])
    if not (pb > pa and pb > pc):
        return None
    if abs(pa - pc) / max(pa, pc) * 100 > 3.0:
        return None  # shoulders should be similar height
    neckline = float(close.iloc[a:c + 1].min())
    last = float(close.iloc[-1])
    if last >= neckline:
        return None
    return {"pattern": "head_and_shoulders", "side": "sell",
            "neckline": neckline, "head": pb}


def _detect_inverse_hs(close: pd.Series, lows: list[int]) -> Optional[dict]:
    if len(lows) < 3:
        return None
    a, b, c = lows[-3], lows[-2], lows[-1]
    if c - a > 60:
        return None
    pa, pb, pc = float(close.iloc[a]), float(close.iloc[b]), float(close.iloc[c])
    if not (pb < pa and pb < pc):
        return None
    if abs(pa - pc) / max(pa, pc) * 100 > 3.0:
        return None
    neckline = float(close.iloc[a:c + 1].max())
    last = float(close.iloc[-1])
    if last <= neckline:
        return None
    return {"pattern": "inverse_h_s", "side": "buy",
            "neckline": neckline, "head": pb}


def _detect_bull_flag(close: pd.Series, lookback: int = 25) -> Optional[dict]:
    """Sharp up-move (impulse) followed by tight consolidation drift."""
    if len(close) < lookback + 5:
        return None
    impulse_window = close.iloc[-lookback:-5]
    consol = close.iloc[-5:]
    if len(impulse_window) < 5:
        return None
    impulse_pct = (impulse_window.iloc[-1] - impulse_window.iloc[0]) / impulse_window.iloc[0] * 100
    if impulse_pct < 1.5:  # require meaningful impulse
        return None
    consol_range_pct = (consol.max() - consol.min()) / consol.iloc[0] * 100
    if consol_range_pct > 0.7:  # tight consolidation
        return None
    last = float(close.iloc[-1])
    return {"pattern": "bull_flag", "side": "buy",
            "breakout_target": last * (1 + impulse_pct / 100 * 0.6)}


def _detect_bear_flag(close: pd.Series, lookback: int = 25) -> Optional[dict]:
    if len(close) < lookback + 5:
        return None
    impulse_window = close.iloc[-lookback:-5]
    consol = close.iloc[-5:]
    if len(impulse_window) < 5:
        return None
    impulse_pct = (impulse_window.iloc[-1] - impulse_window.iloc[0]) / impulse_window.iloc[0] * 100
    if impulse_pct > -1.5:
        return None
    consol_range_pct = (consol.max() - consol.min()) / consol.iloc[0] * 100
    if consol_range_pct > 0.7:
        return None
    last = float(close.iloc[-1])
    return {"pattern": "bear_flag", "side": "sell",
            "breakout_target": last * (1 + impulse_pct / 100 * 0.6)}


class PatternAgent(BaseAgent):

    def __init__(self):
        super().__init__("PatternAgent")

    def propose(self, ctx: dict) -> Optional[Proposal]:
        ohlcv: pd.DataFrame = ctx.get("ohlcv_1h")
        if ohlcv is None or len(ohlcv) < 60:
            return None
        close = ohlcv["close"]
        highs, lows = _pivots(close, lookback=4)
        atr_v = atr(ohlcv, 14).iloc[-1]
        if pd.isna(atr_v) or atr_v <= 0:
            return None
        last = float(close.iloc[-1])

        # Try detectors in priority order — H&S more specific than DT/DB,
        # which is more specific than flag
        for det in (
            lambda: _detect_head_shoulders(close, highs),
            lambda: _detect_inverse_hs(close, lows),
            lambda: _detect_double_top(close, highs),
            lambda: _detect_double_bottom(close, lows),
            lambda: _detect_bull_flag(close),
            lambda: _detect_bear_flag(close),
        ):
            r = det()
            if r is None:
                continue
            side = r["side"]
            if side == "buy":
                sl = last - 1.0 * atr_v
                tp = r.get("breakout_target") or (last + 2.0 * (last - sl))
                if tp <= last:
                    continue
            else:
                sl = last + 1.0 * atr_v
                tp = r.get("breakout_target") or (last - 2.0 * (sl - last))
                if tp >= last:
                    continue
            return Proposal(
                agent_id=self.name, symbol=ctx["symbol"], side=side,
                entry=last, sl=sl, tp=tp,
                confidence=0.60,
                reason=f"pattern:{r['pattern']}",
            )
        return None
