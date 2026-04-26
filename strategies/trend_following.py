"""
strategies/trend_following.py — EMA crossover + RSI + MACD + ATR
"""

import pandas as pd
import numpy as np
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base           import BaseExchange
from core.order_manager       import OrderManager
from core.risk_manager        import RiskManager
from config                   import TREND_FOLLOWING as CFG, RISK


def _ema(s, p): return s.ewm(span=p, adjust=False).mean()

def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=p-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=p-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd(s, fast=12, slow=26, sig=9):
    macd_line   = _ema(s, fast) - _ema(s, slow)
    signal_line = _ema(macd_line, sig)
    return macd_line - signal_line   # histogram

def _atr(high, low, close, p=14):
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low  - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, adjust=False).mean()

def _adx(high, low, close, p=14):
    tr   = _atr(high, low, close, p)
    up   = high.diff()
    down = -low.diff()
    pdm  = up.where((up > down) & (up > 0), 0)
    mdm  = down.where((down > up) & (down > 0), 0)
    pdi  = 100 * pdm.ewm(com=p-1, adjust=False).mean() / tr.replace(0, np.nan)
    mdi  = 100 * mdm.ewm(com=p-1, adjust=False).mean() / tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(com=p-1, adjust=False).mean()


class TrendFollowingStrategy(BaseStrategy):

    def __init__(self, order_manager, risk_manager, market_type="spot"):
        super().__init__(order_manager, risk_manager, "TrendFollowing", market_type)
        self.cfg = CFG

    def generate_signal(self, df: pd.DataFrame):
        df = df.copy()
        df["ema_fast"]  = _ema(df["close"], self.cfg["fast_ema"])
        df["ema_slow"]  = _ema(df["close"], self.cfg["slow_ema"])
        df["ema_trend"] = _ema(df["close"], self.cfg["trend_ema"])
        df["rsi"]       = _rsi(df["close"], self.cfg["rsi_period"])
        df["macd_hist"] = _macd(df["close"], self.cfg["macd_fast"],
                                 self.cfg["macd_slow"], self.cfg["macd_signal"])
        df["vol_ma"]    = df["volume"].rolling(self.cfg["volume_ma"]).mean()
        df["adx"]       = _adx(df["high"], df["low"], df["close"], 14)
        df.dropna(inplace=True)
        if len(df) < 3:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        vol_ok = curr["volume"] >= curr["vol_ma"] * self.cfg["min_volume_mult"]

        # ADX filter: require ADX >= 22 to confirm a real trend (reduces whipsaws)
        if curr["adx"] < 22:
            return None

        # Primary: EMA stack + MACD positive + RSI + volume (histogram increasing not required)
        if (curr["ema_fast"] > curr["ema_slow"] > curr["ema_trend"]
                and curr["rsi"] < self.cfg["rsi_overbought"]
                and curr["macd_hist"] > 0
                and vol_ok):
            return "buy"

        if (curr["ema_fast"] < curr["ema_slow"] < curr["ema_trend"]
                and curr["rsi"] > self.cfg["rsi_oversold"]
                and curr["macd_hist"] < 0
                and vol_ok):
            return "sell"

        # Secondary: Strong EMA crossover without full stack (catches early moves)
        bull_cross = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear_cross = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

        if bull_cross and curr["close"] > curr["ema_trend"] and curr["adx"] > 25 and vol_ok:
            return "buy"
        if bear_cross and curr["close"] < curr["ema_trend"] and curr["adx"] > 25 and vol_ok:
            return "sell"

        return None

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[TrendFollowing] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)

        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return

        df = self.get_dataframe(exchange, symbol,
                                self.cfg["timeframe"], self.cfg["lookback_candles"])
        if df is None:
            return

        signal = self.generate_signal(df)
        if not signal:
            return

        current_price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, current_price)

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        leverage = RISK["default_leverage"] if self.market_type == "futures" else 1
        size     = self.risk.calculate_position_size(balance, current_price, leverage)
        atr_vals = _atr(df["high"], df["low"], df["close"], self.cfg["atr_period"])
        atr      = float(atr_vals.iloc[-1])
        sl, tp   = self.risk.get_sl_tp(
            current_price, signal, atr=atr,
            atr_sl_mult=self.cfg.get("atr_sl_mult"),
            atr_tp_mult=self.cfg.get("atr_tp_mult"),
            leverage=leverage)

        # R:R check — reject marginal setups
        rr = ((tp - current_price) / max(current_price - sl, 1e-8)) if signal == "buy" \
             else ((current_price - tp) / max(sl - current_price, 1e-8))
        if rr < RISK.get("min_rr_ratio", 1.5):
            logger.debug(f"[TrendFollowing] {symbol}: R:R {rr:.2f} too low — skipped")
            return

        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
        return pos
