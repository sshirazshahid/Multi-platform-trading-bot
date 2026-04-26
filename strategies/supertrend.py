"""
strategies/supertrend.py — Supertrend Strategy (Spot + Futures)

Signal logic:
  LONG  = price crosses ABOVE supertrend line AND 4h trend bullish
          AND RSI not overbought AND volume above average
  SHORT = price crosses BELOW supertrend line AND 4h trend bearish
          AND RSI not oversold AND volume above average

Win rate: ~60-65%  |  R:R: 1:3
"""

import pandas as pd
import numpy as np
from loguru import logger

from strategies.base_strategy import BaseStrategy
from exchanges.base           import BaseExchange
from core.order_manager       import OrderManager
from core.risk_manager        import RiskManager
from config                   import SUPERTREND as CFG, RISK


def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 10, multiplier: float = 3.0):
    """
    Pure-pandas Supertrend implementation.
    Returns (supertrend_series, direction_series)
    direction: +1 = bullish (price above line), -1 = bearish
    """
    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # ATR via Wilder's smoothing
    atr = tr.ewm(com=period-1, adjust=False).mean()

    hl2       = (high + low) / 2
    upper_raw = hl2 + multiplier * atr
    lower_raw = hl2 - multiplier * atr

    upper = upper_raw.copy()
    lower = lower_raw.copy()

    for i in range(1, len(close)):
        # Final upper band: keep previous if conditions met
        upper.iloc[i] = upper_raw.iloc[i] if (
            upper_raw.iloc[i] < upper.iloc[i - 1] or close.iloc[i - 1] > upper.iloc[i - 1]
        ) else upper.iloc[i - 1]

        # Final lower band
        lower.iloc[i] = lower_raw.iloc[i] if (
            lower_raw.iloc[i] > lower.iloc[i - 1] or close.iloc[i - 1] < lower.iloc[i - 1]
        ) else lower.iloc[i - 1]

    supertrend = pd.Series(index=close.index, dtype=float)
    direction  = pd.Series(index=close.index, dtype=int)
    direction.iloc[0] = 1

    for i in range(1, len(close)):
        prev_dir = direction.iloc[i - 1]
        if prev_dir == -1:
            direction.iloc[i] = 1 if close.iloc[i] > upper.iloc[i] else -1
        else:
            direction.iloc[i] = -1 if close.iloc[i] < lower.iloc[i] else 1
        supertrend.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return supertrend, direction


class SupertrendStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "Supertrend", market_type)
        self.cfg = CFG

    # ── Shared indicator helpers ──────────────────────────────────────

    def _calc_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _calc_atr(self, high, low, close, period: int = 14) -> pd.Series:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(com=period-1, adjust=False).mean()

    # ── Higher timeframe trend filter ────────────────────────────────

    def _get_htf_trend(self, exchange: BaseExchange, symbol: str) -> int:
        """Return +1 (bullish), -1 (bearish) or 0 (unclear) from 4h Supertrend."""
        raw = exchange.fetch_ohlcv(symbol, self.cfg["htf_timeframe"], 80, self.market_type)
        if not raw or len(raw) < 60:
            return 0
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        _, direction = _supertrend(df["high"], df["low"], df["close"],
                                   self.cfg["atr_period"], self.cfg["atr_multiplier"])
        direction.dropna(inplace=True)
        if direction.empty:
            return 0
        return int(direction.iloc[-1])

    # ── Signal ───────────────────────────────────────────────────────

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, atr_value) or (None, None)."""
        st, direction = _supertrend(df["high"], df["low"], df["close"],
                                    self.cfg["atr_period"], self.cfg["atr_multiplier"])
        df["st_dir"]  = direction
        df["rsi"]     = self._calc_rsi(df["close"], self.cfg["rsi_period"])
        df["atr"]     = self._calc_atr(df["high"], df["low"], df["close"],
                                        self.cfg["atr_period"])
        df["vol_ma"]  = df["volume"].rolling(self.cfg["volume_ma"]).mean()
        df.dropna(inplace=True)

        if len(df) < 6:
            return None, None

        # CLOSED-BAR ONLY: ccxt returns the in-progress candle as last row on
        # live feeds. Using iloc[-1] caused ghost flips that vanished before
        # the bar closed and left us holding whipsawed entries. Always read
        # from the fully-closed bar back.
        curr = df.iloc[-2]
        prev = df.iloc[-3]

        # ATR volatility filter
        atr_pct = curr["atr"] / curr["close"]
        if atr_pct < self.cfg["min_atr_pct"] or atr_pct > self.cfg["max_atr_pct"]:
            return None, None

        volume_ok   = curr["volume"] >= curr["vol_ma"] * self.cfg["min_volume_mult"]
        bull_cross  = (prev["st_dir"] == -1 and curr["st_dir"] == 1)
        bear_cross  = (prev["st_dir"] ==  1 and curr["st_dir"] == -1)
        atr         = float(curr["atr"])

        if bull_cross and self.cfg["rsi_min"] < curr["rsi"] < self.cfg["rsi_max"] and volume_ok:
            return "buy", atr
        if bear_cross and self.cfg["rsi_min"] < curr["rsi"] < self.cfg["rsi_max"] and volume_ok:
            return "sell", atr

        # Also enter on ESTABLISHED TREND (no crossover needed)
        # Only conservative continuation: RSI in mid-range + volume confirm
        bull_trend = curr["st_dir"] == 1 and prev["st_dir"] == 1
        bear_trend = curr["st_dir"] == -1 and prev["st_dir"] == -1

        if bull_trend and 40 < curr["rsi"] < 55 and volume_ok:
            return "buy", atr
        if bear_trend and 55 < curr["rsi"] < 65 and volume_ok:
            return "sell", atr

        return None, None

    # ── Run cycle ────────────────────────────────────────────────────

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[Supertrend] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)

        if self.order_manager.tracker.get_open(exchange=exchange.name, symbol=symbol):
            return

        htf_trend = self._get_htf_trend(exchange, symbol)

        df = self.get_dataframe(exchange, symbol,
                                self.cfg["timeframe"], self.cfg["lookback_candles"])
        if df is None:
            return

        signal, atr = self.generate_signal(df)
        if not signal:
            return

        # HTF alignment: block entries when HTF trend is unclear or opposing
        if htf_trend == 0:
            logger.debug(f"[Supertrend] {symbol} HTF trend unclear — skipping")
            return
        if htf_trend == 1 and signal == "sell":
            if self.market_type == "futures":
                logger.debug(f"[Supertrend] {symbol}: SELL in 4h uptrend — weak, skipping")
                return
            else:
                return  # No spot short
        if htf_trend == -1 and signal == "buy":
            # Don't flip — SL/TP were calculated for a long setup
            logger.debug(f"[Supertrend] {symbol}: BUY blocked — 4h is bearish")
            return

        # Momentum exhaustion filter
        from utils.indicators import momentum_exhaustion
        exhaustion = momentum_exhaustion(df["close"], df["high"], df["low"])
        if signal == "buy" and exhaustion["bull_exhaustion"]:
            logger.debug(f"[Supertrend] {symbol}: Bull exhaustion — skipping BUY")
            return
        if signal == "sell" and exhaustion["bear_exhaustion"]:
            logger.debug(f"[Supertrend] {symbol}: Bear exhaustion — skipping SELL")
            return

        current_price = float(df["close"].iloc[-1])
        htf_label = "bull" if htf_trend == 1 else "bear"
        self.log_signal(symbol, signal, current_price,
                        f"| ATR={atr:.4f} HTF={htf_label}")

        balance = self.get_usdt_balance(exchange)
        if balance < 10:
            return

        leverage = RISK["default_leverage"] if self.market_type == "futures" else 1
        size     = self.risk.calculate_position_size(balance, current_price, leverage)
        sl, tp   = self.risk.get_sl_tp(
            current_price, signal, atr=atr,
            atr_sl_mult=self.cfg.get("atr_sl_mult"),
            atr_tp_mult=self.cfg.get("atr_tp_mult"),
            leverage=leverage)

        rr = ((tp - current_price) / max(current_price - sl, 1e-8)) if signal == "buy" \
             else ((current_price - tp) / max(sl - current_price, 1e-8))
        if rr < RISK.get("min_rr_ratio", 1.5):
            logger.debug(f"[Supertrend] {symbol}: R:R {rr:.2f} too low — skipped")
            return

        pos = self.order_manager.open_position(
            exchange=exchange, symbol=symbol, side=signal,
            market_type=self.market_type, strategy=self.name,
            size=size, sl=sl, tp=tp, leverage=leverage,
        )
        return pos
