"""
strategies/asian_range_breakout.py — Asian-session range breakout (research only).

Build the Asian-session range (default UTC 00:00-06:00) from the bars that fall
inside that window, then trade the first decisive break of either edge during
the post-session breakout window:

    close > asian_high + buffer  -> long
    close < asian_low  - buffer  -> short

SL = the opposite range edge, or 1.5*ATR from entry (whichever is the tighter,
i.e. nearer protective stop), TP = configured reward:risk projection.
One trade per session: once the session's range is broken the strategy will not
re-arm until a fresh session forms.

NOT wired into the live Claude-Portfolio pipeline. generate_signal is a pure,
STRICTLY CAUSAL function of the supplied window — it reads only df rows that
exist in the window (bars 0..n-1) and decides on the last bar.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from config import ASIAN_RANGE_BREAKOUT as CFG
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy
from utils.indicators import atr, session_range


def _with_ts(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy carrying a unix-seconds ``ts`` column derived from the
    datetime index (session_range expects it). Causal: pure index arithmetic."""
    out = df.copy()
    out["ts"] = (out.index.astype("int64") // 1_000_000_000).astype("int64")
    return out


class AsianRangeBreakoutStrategy(BaseStrategy):
    def __init__(
        self, order_manager: OrderManager, risk_manager: RiskManager, market_type: str = "futures"
    ):
        super().__init__(order_manager, risk_manager, "AsianRangeBreakout", market_type)
        self.cfg = CFG

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, sl, tp) or (None, None, None). Strictly causal."""
        if df is None or len(df) < 20:
            return None, None, None

        d = _with_ts(df)
        start_h = self.cfg["session_start_hour"]
        end_h = self.cfg["session_end_hour"]
        run_high, run_low = session_range(d, start_h, end_h)
        # session_range leaves NaN on non-session bars; carry the last known
        # session extreme forward so the breakout window sees the range. ffill
        # only propagates PAST values -> strictly causal.
        run_high = run_high.ffill()
        run_low = run_low.ffill()

        a_high = run_high.iloc[-1]
        a_low = run_low.iloc[-1]
        if pd.isna(a_high) or pd.isna(a_low) or a_low <= 0:
            return None, None, None

        # The decision bar must be OUTSIDE the session window (the breakout
        # window) — never trade while still building the range.
        last_hour = int((d["ts"].iloc[-1] // 3600) % 24)
        if start_h <= last_hour <= end_h:
            return None, None, None

        range_pct = (a_high - a_low) / a_low
        if range_pct < self.cfg["min_range_pct"] or range_pct > self.cfg["max_range_pct"]:
            return None, None, None

        close = float(d["close"].iloc[-1])
        buf = self.cfg["breakout_buffer_pct"]
        atr_series = atr(d["high"], d["low"], d["close"], self.cfg["atr_period"])
        atr_val = float(atr_series.iloc[-1])
        if not (atr_val > 0):
            return None, None, None
        rr = self.cfg["rr"]
        sl_mult = self.cfg["atr_sl_mult"]

        # LONG: decisive close above the session high.
        if close > a_high * (1 + buf):
            atr_sl = close - atr_val * sl_mult
            edge_sl = a_low
            sl = max(atr_sl, edge_sl)  # nearer (tighter) protective stop
            risk = close - sl
            if risk <= 0:
                return None, None, None
            tp = close + risk * rr
            return "buy", sl, tp

        # SHORT: decisive close below the session low.
        if close < a_low * (1 - buf):
            atr_sl = close + atr_val * sl_mult
            edge_sl = a_high
            sl = min(atr_sl, edge_sl)
            risk = sl - close
            if risk <= 0:
                return None, None, None
            tp = close - risk * rr
            return "sell", sl, tp

        return None, None, None

    def run(self, exchange: BaseExchange, symbol: str):
        """Thin live wrapper mirroring MeanReversionStrategy.run. Research only;
        not invoked by the live pipeline."""
        logger.debug(f"[{self.name}] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)
        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return

        df = self.get_dataframe(
            exchange, symbol, self.cfg["timeframe"], self.cfg["lookback_candles"]
        )
        if df is None:
            return

        signal, sl, tp = self.generate_signal(df)
        if not signal:
            return

        price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, price, f"| SL={sl:.4f} TP={tp:.4f}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return
        size = self.risk.calculate_position_size(balance, price, 1)
        return self.order_manager.open_position(
            exchange=exchange,
            symbol=symbol,
            side=signal,
            market_type=self.market_type,
            strategy=self.name,
            size=size,
            sl=sl,
            tp=tp,
            leverage=1,
        )
