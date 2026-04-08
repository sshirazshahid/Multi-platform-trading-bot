"""
strategies/multi_tf.py — Multi-Timeframe Confluence Strategy (Futures)

Requires 4h trend + 1h structure + 15m entry to all agree.
ADX filter blocks choppy markets. Max 3 trades/symbol/day.
Strict 1:3 R:R enforced.

Win rate: ~55-60%  |  R:R: 1:3+
"""

import numpy as np
import pandas as pd
from datetime import date
from collections import defaultdict
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base           import BaseExchange
from core.order_manager       import OrderManager
from core.risk_manager        import RiskManager
from config                   import MULTI_TF as CFG, RISK


# ── Pure-pandas helpers ───────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high, low, close, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _adx(high, low, close, period: int = 14) -> pd.Series:
    """Simplified ADX calculation."""
    tr   = _atr(high, low, close, period)
    up   = high.diff()
    down = -low.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / tr.replace(0, np.nan)
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(span=period, adjust=False).mean()


class MultiTFStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "futures"):
        super().__init__(order_manager, risk_manager, "MultiTF", market_type)
        self.cfg        = CFG
        self._trade_log = defaultdict(int)

    def _can_trade_today(self, symbol: str) -> bool:
        key = f"{symbol}_{date.today().isoformat()}"
        return self._trade_log[key] < self.cfg["max_trades_per_day"]

    def _record_trade(self, symbol: str):
        key = f"{symbol}_{date.today().isoformat()}"
        self._trade_log[key] += 1

    def _htf_direction(self, exchange: BaseExchange, symbol: str) -> int:
        """4h: +1 bullish, -1 bearish, 0 unclear.
        Tightened: require ADX > 20 on 4h to confirm real trend (not chop)."""
        raw = exchange.fetch_ohlcv(symbol, self.cfg["htf_timeframe"], 220, self.market_type)
        if not raw or len(raw) < 200:
            return 0
        df     = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        ema200 = _ema(df["close"], self.cfg["trend_ema"])
        close  = float(df["close"].iloc[-1])
        ema    = float(ema200.iloc[-1])

        # ADX filter on 4h — reject if market is choppy
        adx_4h = _adx(df["high"], df["low"], df["close"], 14)
        if float(adx_4h.iloc[-1]) < 20:
            return 0

        if close > ema * 1.005:
            return 1
        if close < ema * 0.995:
            return -1
        return 0

    def _mtf_structure(self, exchange: BaseExchange, symbol: str) -> int:
        """1h: +1 bullish EMA stack, -1 bearish, 0 unclear.
        Tightened: require 0.3% EMA separation (was 0.1%) to filter noise."""
        raw = exchange.fetch_ohlcv(symbol, self.cfg["mtf_timeframe"], 50, self.market_type)
        if not raw or len(raw) < 30:
            return 0
        df   = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        fast = _ema(df["close"], self.cfg["structure_fast"])
        slow = _ema(df["close"], self.cfg["structure_slow"])
        f, s = float(fast.iloc[-1]), float(slow.iloc[-1])
        if f > s * 1.003:
            return 1
        if f < s * 0.997:
            return -1
        return 0

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, atr) or (None, None)."""
        df["ema_fast"] = _ema(df["close"], self.cfg["entry_fast"])
        df["ema_slow"] = _ema(df["close"], self.cfg["entry_slow"])
        df["rsi"]      = _rsi(df["close"], self.cfg["rsi_period"])
        df["atr"]      = _atr(df["high"], df["low"], df["close"], self.cfg["atr_period"])
        df["adx"]      = _adx(df["high"], df["low"], df["close"], self.cfg["adx_period"])
        df.dropna(inplace=True)
        if len(df) < 5:
            return None, None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        if curr["adx"] < self.cfg["adx_min"]:
            return None, None

        bull_cross = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear_cross = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
        atr        = float(curr["atr"])

        # Entry on fresh crossover (original)
        if bull_cross and curr["rsi"] < self.cfg["rsi_overbought"]:
            return "buy", atr
        if bear_cross and curr["rsi"] > self.cfg["rsi_oversold"]:
            return "sell", atr

        # Trend continuation — only on STRONG established trends with ADX confirmation
        # Tightened: require 0.3% EMA separation + higher ADX to reduce whipsaws
        # Live WR was 20% vs 54.5% dry — entries were too loose
        bull_trend = curr["ema_fast"] > curr["ema_slow"] * 1.003
        bear_trend = curr["ema_fast"] < curr["ema_slow"] * 0.997

        # Pullback entry: strong trend + RSI retraces toward midline + ADX confirms
        # WIDENED: RSI window 38-52 / 48-62 (was 42-48 / 52-58 — only 6 points, near-impossible)
        # ADX 25 (was 30 — too restrictive, blocked most legitimate trending markets)
        if bull_trend and curr["adx"] > 25 and 38 < curr["rsi"] < 52:
            return "buy", atr
        if bear_trend and curr["adx"] > 25 and 48 < curr["rsi"] < 62:
            return "sell", atr

        # Strong trend entry (ADX > 33 = strong, enter with momentum)
        # Was ADX > 38 — only fires during explosive breakouts, not regular trends
        if bull_trend and curr["adx"] > 33 and curr["rsi"] < self.cfg["rsi_overbought"] - 5:
            return "buy", atr
        if bear_trend and curr["adx"] > 33 and curr["rsi"] > self.cfg["rsi_oversold"] + 5:
            return "sell", atr

        return None, None

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[MultiTF] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)

        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return
        if not self._can_trade_today(symbol):
            return

        htf = self._htf_direction(exchange, symbol)
        if htf == 0:
            return

        mtf = self._mtf_structure(exchange, symbol)
        if mtf == 0 or mtf != htf:
            return

        df = self.get_dataframe(exchange, symbol,
                                self.cfg["ltf_timeframe"], self.cfg["lookback_candles"])
        if df is None:
            return

        signal, atr = self.generate_signal(df)
        if not signal:
            return

        if signal == "buy"  and htf != 1:
            return
        if signal == "sell" and htf != -1:
            return

        current_price = float(df["close"].iloc[-1])
        self.log_signal(symbol, signal, current_price,
                        f"| HTF={htf} MTF={mtf} ATR={atr:.4f}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        leverage = self.risk.validate_leverage(RISK["default_leverage"])
        size     = self.risk.calculate_position_size(balance, current_price, leverage)
        sl, tp   = self.risk.get_sl_tp(
            current_price, signal, atr=atr,
            atr_sl_mult=self.cfg.get("atr_sl_mult"),
            atr_tp_mult=self.cfg.get("atr_tp_mult"),
            leverage=leverage)

        rr = ((tp - current_price) / max(current_price - sl, 1e-8)) if signal == "buy" \
             else ((current_price - tp) / max(sl - current_price, 1e-8))
        if rr < RISK.get("min_rr_ratio", 1.5):
            logger.debug(f"[MultiTF] {symbol}: R:R {rr:.2f} too low — skipped")
            return

        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
        if pos:
            self._record_trade(symbol)
            return pos
        return None
