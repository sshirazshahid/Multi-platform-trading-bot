"""
strategies/bb_squeeze_breakout.py — Bollinger/Keltner squeeze breakout (research).

Detect a volatility squeeze (utils.indicators.bb_squeeze — Bollinger Bands
coiling inside the Keltner Channel). On the squeeze RELEASE (squeeze was active
recently but is no longer), enter in the direction of a volume-confirmed close
beyond the corresponding Bollinger band:

    release + close > upper band + buffer + volume confirm -> long
    release + close < lower band - buffer + volume confirm -> short

SL = 1.5*ATR from entry or the Bollinger midline (whichever is the nearer/tighter
protective stop). TP = configured reward:risk projection.

mode='normal' reads config.BB_SQUEEZE; mode='scalp' reads config.SCALP_PROFILE
(tighter stop, shorter timeframe). NOT wired into the live pipeline.
generate_signal is STRICTLY CAUSAL: bb_squeeze / bollinger_bands / atr are all
rolling/expanding, so the decision on the last bar reads only bars 0..n-1.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from config import BB_SQUEEZE, SCALP_PROFILE
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy
from utils.indicators import atr, bb_squeeze, bollinger_bands


class BBSqueezeBreakoutStrategy(BaseStrategy):
    def __init__(
        self,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        market_type: str = "futures",
        mode: str = "normal",
    ):
        if mode not in ("normal", "scalp"):
            raise ValueError(f"mode must be 'normal' or 'scalp', got {mode!r}")
        self.mode = mode
        self.cfg = SCALP_PROFILE if mode == "scalp" else BB_SQUEEZE
        name = "BBSqueezeScalp" if mode == "scalp" else "BBSqueezeBreakout"
        super().__init__(order_manager, risk_manager, name, market_type)

    def _params(self):
        # SCALP_PROFILE doesn't carry BB/KC keys; fall back to BB_SQUEEZE values.
        cfg = self.cfg
        return {
            "bb_period": cfg.get("bb_period", BB_SQUEEZE["bb_period"]),
            "bb_std": cfg.get("bb_std", BB_SQUEEZE["bb_std"]),
            "kc_period": cfg.get("kc_period", BB_SQUEEZE["kc_period"]),
            "kc_mult": cfg.get("kc_mult", BB_SQUEEZE["kc_mult"]),
            "min_squeeze_bars": cfg.get("min_squeeze_bars", BB_SQUEEZE["min_squeeze_bars"]),
        }

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, sl, tp) or (None, None, None). Strictly causal."""
        p = self._params()
        need = max(p["bb_period"], p["kc_period"]) + p["min_squeeze_bars"] + 2
        if df is None or len(df) < need:
            return None, None, None

        sq = bb_squeeze(
            df["close"],
            df["high"],
            df["low"],
            p["bb_period"],
            p["bb_std"],
            p["kc_period"],
            p["kc_mult"],
        )
        # RELEASE: squeeze is OFF on the decision bar but was ON within the
        # prior min_squeeze_bars window (a coil that just released).
        if bool(sq.iloc[-1]):
            return None, None, None
        recent = sq.iloc[-(p["min_squeeze_bars"] + 1) : -1]
        if not bool(recent.any()):
            return None, None, None

        lower, mid, upper = bollinger_bands(df["close"], p["bb_period"], p["bb_std"])
        lo = float(lower.iloc[-1])
        mi = float(mid.iloc[-1])
        up = float(upper.iloc[-1])
        close = float(df["close"].iloc[-1])

        # Volume confirmation: breakout bar volume above its trailing average.
        vol = df["volume"]
        vol_ma = float(vol.rolling(p["bb_period"]).mean().iloc[-1])
        if not (float(vol.iloc[-1]) > vol_ma):
            return None, None, None

        buf = self.cfg["breakout_buffer_pct"]
        atr_val = float(atr(df["high"], df["low"], df["close"], self.cfg["atr_period"]).iloc[-1])
        if not (atr_val > 0):
            return None, None, None
        rr = self.cfg["rr"]
        sl_mult = self.cfg["atr_sl_mult"]

        # LONG: close breaks above the upper band.
        if close > up * (1 + buf):
            atr_sl = close - atr_val * sl_mult
            sl = max(atr_sl, mi)  # nearer (tighter) of ATR stop / midline
            risk = close - sl
            if risk <= 0:
                return None, None, None
            tp = close + risk * rr
            return "buy", sl, tp

        # SHORT: close breaks below the lower band.
        if close < lo * (1 - buf):
            atr_sl = close + atr_val * sl_mult
            sl = min(atr_sl, mi)
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
