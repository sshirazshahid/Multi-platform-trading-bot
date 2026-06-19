"""
strategies/dow_swing_structure.py — Dow-theory swing structure (research only).

Read confirmed fractal swing pivots (utils.indicators.swing_points). Classify
structure from the most recent CONFIRMED pivots:

    Uptrend   : higher-high (HH) followed by a higher-low (HL)
                -> long when price breaks up past the prior swing high.
    Downtrend : lower-high (LH) followed by a lower-low (LL)
                -> short when price breaks down past the prior swing low.
    Otherwise : structure is broken / ambiguous -> flat (None).

SL sits just beyond the protected swing (the HL for longs, the LH for shorts).
TP is the configured reward:risk projection.

NOT wired into the live pipeline. generate_signal is STRICTLY CAUSAL: it uses
swing_points, which only confirms a pivot ``swing_right`` bars after it occurs,
so the last ``swing_right`` bars can never be pivots — no future leak.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from config import DOW_SWING as CFG
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy
from utils.indicators import atr, ema, swing_points


class DowSwingStructureStrategy(BaseStrategy):
    def __init__(
        self, order_manager: OrderManager, risk_manager: RiskManager, market_type: str = "futures"
    ):
        super().__init__(order_manager, risk_manager, "DowSwingStructure", market_type)
        self.cfg = CFG

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, sl, tp) or (None, None, None). Strictly causal."""
        if df is None or len(df) < 20:
            return None, None, None

        left = self.cfg["swing_left"]
        right = self.cfg["swing_right"]
        ph, pl = swing_points(df["high"], df["low"], left, right)

        # Confirmed pivots only (swing_points already excludes the last `right`
        # bars, which is what makes this causal).
        high_idx = [i for i, v in enumerate(ph.to_numpy()) if v]
        low_idx = [i for i, v in enumerate(pl.to_numpy()) if v]
        if len(high_idx) < 2 or len(low_idx) < 2:
            return None, None, None

        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        close = float(df["close"].iloc[-1])

        # Last two confirmed swing highs / lows.
        h1, h2 = high_idx[-2], high_idx[-1]
        l1, l2 = low_idx[-2], low_idx[-1]
        last_high_v = highs[h2]
        prev_high_v = highs[h1]
        last_low_v = lows[l2]
        prev_low_v = lows[l1]

        buf = self.cfg["breakout_buffer_pct"]
        atr_val = float(atr(df["high"], df["low"], df["close"], self.cfg["atr_period"]).iloc[-1])
        if not (atr_val > 0):
            return None, None, None
        rr = self.cfg["rr"]
        sl_mult = self.cfg["atr_sl_mult"]

        trend_ok_long = trend_ok_short = True
        if self.cfg.get("trend_filter"):
            ema_v = float(ema(df["close"], self.cfg["trend_ema_period"]).iloc[-1])
            if pd.notna(ema_v):
                trend_ok_long = close >= ema_v
                trend_ok_short = close <= ema_v

        # UPTREND: HH (last high > prev high) AND HL (last low > prev low),
        # with the protected HL confirmed AFTER the HH. Enter long on a break
        # above the most recent swing high.
        uptrend = last_high_v > prev_high_v and last_low_v > prev_low_v and l2 > h1
        if uptrend and trend_ok_long and close > last_high_v * (1 + buf):
            protected = last_low_v
            atr_sl = close - atr_val * sl_mult
            sl = min(protected, atr_sl)  # beyond the protected swing low
            risk = close - sl
            if risk <= 0:
                return None, None, None
            tp = close + risk * rr
            return "buy", sl, tp

        # DOWNTREND: LH (last high < prev high) AND LL (last low < prev low),
        # with the protected LH confirmed AFTER the LL. Enter short on a break
        # below the most recent swing low.
        downtrend = last_high_v < prev_high_v and last_low_v < prev_low_v and h2 > l1
        if downtrend and trend_ok_short and close < last_low_v * (1 - buf):
            protected = last_high_v
            atr_sl = close + atr_val * sl_mult
            sl = max(protected, atr_sl)  # beyond the protected swing high
            risk = sl - close
            if risk <= 0:
                return None, None, None
            tp = close - risk * rr
            return "sell", sl, tp

        return None, None, None

    def run(self, exchange: BaseExchange, symbol: str):
        """Thin live wrapper mirroring MeanReversionStrategy.run. Research only."""
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
