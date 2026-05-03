"""
strategies/scalping.py — Scalping Strategy
1m EMA crossover + Bollinger Bands + order book imbalance confirmation.
"""

import numpy as np
import pandas as pd
from loguru import logger

from config import RISK
from config import SCALPING as CFG
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy


def _ema(s, p): return s.ewm(span=p, adjust=False).mean()

def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=p-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=p-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _bbands(s, p=20, std=2.0):
    mid   = s.rolling(p).mean()
    sigma = s.rolling(p).std()
    return mid - std * sigma, mid, mid + std * sigma


class ScalpingStrategy(BaseStrategy):

    def __init__(self, order_manager, risk_manager, market_type="spot"):
        super().__init__(order_manager, risk_manager, "Scalping", market_type)
        self.cfg = CFG

    def _analyze_orderbook(self, exchange: BaseExchange, symbol: str) -> dict:
        ob   = exchange.fetch_order_book(symbol, self.cfg["orderbook_depth"], self.market_type)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            return {"ok": False}
        spread = (asks[0][0] - bids[0][0]) / bids[0][0]
        if spread < self.cfg["min_spread_pct"] or spread > self.cfg["max_spread_pct"]:
            return {"ok": False}
        bid_vol   = sum(b[1] for b in bids[:5])
        ask_vol   = sum(a[1] for a in asks[:5])
        imbalance = bid_vol / ask_vol if ask_vol > 0 else 1.0
        return {
            "ok":           True,
            "spread":       spread,
            "imbalance":    imbalance,
            "bid_pressure": imbalance > self.cfg["imbalance_ratio"],
            "ask_pressure": imbalance < (1 / self.cfg["imbalance_ratio"]),
        }

    def generate_signal(self, df: pd.DataFrame):
        df = df.copy()  # Don't mutate shared/cached DataFrame
        df["ema_fast"]  = _ema(df["close"], self.cfg["fast_ema"])
        df["ema_slow"]  = _ema(df["close"], self.cfg["slow_ema"])
        df["rsi"]       = _rsi(df["close"], self.cfg["rsi_period"])
        df["vol_ma"]    = df["volume"].rolling(10).mean()
        lower, mid, upper = _bbands(df["close"], self.cfg["bb_period"], self.cfg["bb_std"])
        df["bb_lower"]  = lower
        df["bb_upper"]  = upper
        df.dropna(inplace=True)
        if len(df) < 3:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        bull = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
        vol_spike = curr["volume"] > curr["vol_ma"] * self.cfg["volume_spike_mult"]

        # Signal A: EMA crossover + RSI confirm + volume spike
        if bull and curr["rsi"] < self.cfg["rsi_overbought"] and vol_spike:
            return "buy"
        if bear and curr["rsi"] > self.cfg["rsi_oversold"] and vol_spike:
            return "sell"

        # Signal B: BB touch + RSI extreme (no crossover needed)
        if curr["close"] <= curr["bb_lower"] * 1.002 and curr["rsi"] < 35 and vol_spike:
            return "buy"
        if curr["close"] >= curr["bb_upper"] * 0.998 and curr["rsi"] > 65 and vol_spike:
            return "sell"

        return None

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[Scalping] {symbol} ({self.market_type})")
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

        ob = self._analyze_orderbook(exchange, symbol)
        if not ob.get("ok"):
            return
        if signal == "buy"  and not ob.get("bid_pressure"):
            return
        if signal == "sell" and not ob.get("ask_pressure"):
            return

        current_price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, current_price,
                        f"| spread={ob['spread']*100:.3f}% imb={ob['imbalance']:.2f}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        leverage = RISK["default_leverage"] if self.market_type == "futures" else 1
        size     = self.risk.calculate_position_size(balance, current_price, leverage)

        if signal == "buy":
            sl = current_price * (1 - self.cfg["stop_loss"])
            tp = current_price * (1 + self.cfg["take_profit"])
        else:
            sl = current_price * (1 + self.cfg["stop_loss"])
            tp = current_price * (1 - self.cfg["take_profit"])

        rr = ((tp - current_price) / max(current_price - sl, 1e-8)) if signal == "buy" \
             else ((current_price - tp) / max(sl - current_price, 1e-8))
        if rr < RISK.get("min_rr_ratio", 1.5):
            logger.debug(f"[Scalping] {symbol}: R:R {rr:.2f} too low — skipped")
            return

        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
        return pos
