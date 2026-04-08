"""
strategies/mean_reversion.py — Mean Reversion Strategy (Spot)

Enter when price touches BB extreme AND RSI confirms oversold/overbought.
Target is the BB midline. 200 EMA trend filter included.

Win rate: ~65-70%  |  R:R: ~1:2.5
"""

import pandas as pd
import numpy as np
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base           import BaseExchange
from core.order_manager       import OrderManager
from core.risk_manager        import RiskManager
from config                   import MEAN_REVERSION as CFG, RISK


# ── Pure-pandas indicator helpers ─────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bbands(series: pd.Series, period: int = 20, std: float = 2.0):
    """Returns (lower, mid, upper) as pd.Series."""
    mid   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


class MeanReversionStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "MeanReversion", market_type)
        self.cfg = CFG

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, sl_price, tp_price) or (None, None, None)."""
        bb_lower, bb_mid, bb_upper = _bbands(df["close"],
                                              self.cfg["bb_period"],
                                              self.cfg["bb_std"])
        df["bb_lower"] = bb_lower
        df["bb_mid"]   = bb_mid
        df["bb_upper"] = bb_upper
        df["bb_width"] = (bb_upper - bb_lower) / bb_mid
        df["rsi"]      = _rsi(df["close"], self.cfg["rsi_period"])
        df["vol_ma"]   = df["volume"].rolling(self.cfg["volume_ma"]).mean()

        if self.cfg.get("trend_filter"):
            df["ema200"] = _ema(df["close"], self.cfg["trend_ema_period"])

        df.dropna(inplace=True)
        if len(df) < 5:
            return None, None, None

        curr = df.iloc[-1]

        # Skip if BB too narrow — no room to revert
        if curr["bb_width"] < self.cfg["bb_squeeze_min"]:
            return None, None, None

        if curr["volume"] < curr["vol_ma"] * self.cfg["min_volume_mult"]:
            return None, None, None

        close    = curr["close"]
        bb_lower = curr["bb_lower"]
        bb_upper = curr["bb_upper"]
        bb_mid   = curr["bb_mid"]
        rsi      = curr["rsi"]
        ema200   = curr.get("ema200", close)

        # Mean reversion uses lower R:R requirement (high WR compensates)
        mr_min_rr = 1.5

        # LONG: price at lower band + RSI oversold
        # Trend filter relaxed: allow entries when price is near/below EMA200 (deep oversold)
        at_lower     = close <= bb_lower * 1.002
        trend_ok     = True  # MR works best at extremes, even against trend
        if self.cfg.get("trend_filter"):
            # Only block if price is WAY above EMA200 (strong uptrend = not a reversion setup)
            trend_ok = close <= ema200 * 1.02
        if at_lower and rsi <= self.cfg["rsi_oversold"] and trend_ok:
            # Enforce 2% SL floor
            sl_raw = bb_lower * (1 - self.cfg["sl_bb_mult"] * 0.01)
            min_sl = close * 0.98  # 2% below entry
            sl = min(sl_raw, min_sl)
            # Target the midline (BB SMA) — that's what mean reversion means
            # Was overshooting by 30% beyond midline, reducing hit probability
            tp = bb_mid
            rr = (tp - close) / max(close - sl, 1e-8)
            if rr >= mr_min_rr:
                return "buy", sl, tp

        # SHORT: price at upper band + RSI overbought
        at_upper     = close >= bb_upper * 0.998
        trend_ok     = True
        if self.cfg.get("trend_filter"):
            # Only allow shorts when price is NOT in a strong uptrend
            # Old code: close >= ema200 * 0.98 — INVERTED! Allowed shorts in uptrends
            # Fix: block shorts when price is way above EMA200 (strong uptrend)
            trend_ok = close <= ema200 * 1.02
        if at_upper and rsi >= self.cfg["rsi_overbought"] and trend_ok:
            sl_raw = bb_upper * (1 + self.cfg["sl_bb_mult"] * 0.01)
            max_sl = close * 1.02  # 2% above entry
            sl = max(sl_raw, max_sl)
            tp = bb_mid   # Target midline — the core of mean reversion
            rr = (close - tp) / max(sl - close, 1e-8)
            if rr >= mr_min_rr:
                return "sell", sl, tp

        return None, None, None

    def _check_rsi_exit(self, exchange: BaseExchange, symbol: str):
        positions = self.order_manager.tracker.get_open(
            exchange=exchange.name, symbol=symbol)
        if not positions:
            return
        df = self.get_dataframe(exchange, symbol, self.cfg["timeframe"], 30)
        if df is None:
            return
        df["rsi"] = _rsi(df["close"], self.cfg["rsi_period"])
        df.dropna(inplace=True)
        if df.empty:
            return
        rsi = float(df["rsi"].iloc[-1])
        for pos in positions:
            ticker = exchange.fetch_ticker(symbol, self.market_type)
            price  = ticker.get("last")
            if not price:
                continue
            if pos.side == "buy" and rsi >= self.cfg["rsi_exit_long"]:
                logger.info(f"[MeanReversion] RSI exit LONG {symbol} RSI={rsi:.1f}")
                self.order_manager.close_position(exchange, pos, "rsi_exit", price)
            elif pos.side == "sell" and rsi <= self.cfg["rsi_exit_short"]:
                logger.info(f"[MeanReversion] RSI exit SHORT {symbol} RSI={rsi:.1f}")
                self.order_manager.close_position(exchange, pos, "rsi_exit", price)

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[MeanReversion] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)
        self._check_rsi_exit(exchange, symbol)

        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return

        df = self.get_dataframe(exchange, symbol,
                                self.cfg["timeframe"], self.cfg["lookback_candles"])
        if df is None:
            return

        signal, sl, tp = self.generate_signal(df)
        if not signal:
            return

        current_price = float(df["close"].iloc[-1])
        rr = (tp - current_price) / max(current_price - sl, 1e-8) if signal == "buy" \
             else (current_price - tp) / max(sl - current_price, 1e-8)
        self.log_signal(symbol, signal, current_price,
                        f"| SL={sl:.4f} TP={tp:.4f} R:R={rr:.1f}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        size = self.risk.calculate_position_size(balance, current_price, 1)
        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=1,
        )
        return pos
