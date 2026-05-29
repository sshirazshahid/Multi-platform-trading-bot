"""
strategies/mean_reversion.py — Range Mean Reversion Strategy (Spot + Futures)

Detects horizontal ranges, waits for liquidity sweep at boundaries,
enters on sweep reversal. Fast in/out with time-based exit.
"""

import numpy as np
import pandas as pd
from loguru import logger

from config import MEAN_REVERSION as CFG
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from exchanges.base import BaseExchange
from strategies.base_strategy import BaseStrategy

# ── Pure-pandas indicator helpers ─────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bbands(series: pd.Series, period: int = 20, std: float = 2.0):
    """Returns (lower, mid, upper) as pd.Series."""
    mid   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid - std * sigma, mid, mid + std * sigma


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _adx(high, low, close, period: int = 14) -> pd.Series:
    """Simplified ADX for range detection."""
    up   = high.diff()
    down = -low.diff()
    tr_components = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1)
    tr = tr_components.max(axis=1).ewm(com=period-1, adjust=False).mean()
    plus_dm  = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    plus_di  = 100 * plus_dm.ewm(com=period-1, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(com=period-1, adjust=False).mean() / tr.replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(com=period-1, adjust=False).mean()


def _detect_range(df: pd.DataFrame, lookback: int = 20,
                  max_range_pct: float = 0.06) -> tuple:
    """Detect horizontal range over lookback candles.
    Returns (range_high, range_low, is_ranging).
    Ranging = price oscillates within max_range_pct for 10+ candles with ADX < 25."""
    if len(df) < lookback:
        return 0, 0, False
    window = df.iloc[-lookback:]
    range_high = float(window["high"].max())
    range_low  = float(window["low"].min())
    if range_low <= 0:
        return 0, 0, False
    amplitude = (range_high - range_low) / range_low
    if amplitude > max_range_pct:
        return 0, 0, False
    # Check that at least 10 candles stayed within the range
    inside = ((window["close"] >= range_low) & (window["close"] <= range_high)).sum()
    if inside < 10:
        return 0, 0, False
    # ADX should be low (< 25) confirming no strong trend
    adx = _adx(df["high"], df["low"], df["close"], 14)
    if len(adx.dropna()) > 0 and float(adx.iloc[-1]) > 25:
        return 0, 0, False
    return range_high, range_low, True


def _is_liquidity_sweep(df: pd.DataFrame, boundary: float,
                        side: str) -> bool:
    """Check if the last candle swept beyond a boundary then closed back inside.
    For 'buy' (long at range low): candle low < boundary but close > boundary.
    For 'sell' (short at range high): candle high > boundary but close < boundary."""
    if len(df) < 1:
        return False
    last = df.iloc[-1]
    if side == "buy":
        return float(last["low"]) < boundary and float(last["close"]) > boundary
    else:
        return float(last["high"]) > boundary and float(last["close"]) < boundary


class MeanReversionStrategy(BaseStrategy):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 market_type: str = "spot"):
        super().__init__(order_manager, risk_manager, "MeanReversion", market_type)
        self.cfg = CFG

    def generate_signal(self, df: pd.DataFrame):
        """Returns (signal, sl_price, tp_price) or (None, None, None).

        Priority: range detection + liquidity sweep (if enabled).
        Fallback: BB extreme + RSI confirmation (original logic).
        """
        df = df.copy()
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

        curr = df.iloc[-2]  # Use closed candle — iloc[-1] is still forming on live feeds

        if curr["bb_width"] < self.cfg["bb_squeeze_min"]:
            return None, None, None
        if curr["volume"] < curr["vol_ma"] * self.cfg["min_volume_mult"]:
            return None, None, None

        close  = curr["close"]
        rsi    = curr["rsi"]
        ema200 = curr.get("ema200", close)
        mr_min_rr = 1.2

        # --- Range detection + liquidity sweep (primary) ---
        range_lookback = self.cfg.get("range_lookback", 20)
        max_range_pct  = self.cfg.get("max_range_pct", 0.06)
        require_sweep  = self.cfg.get("require_sweep", True)
        tp_range_pct   = self.cfg.get("tp_range_pct", 0.6)

        range_high, range_low, is_ranging = _detect_range(
            df, range_lookback, max_range_pct)

        if is_ranging and range_high > range_low:
            range_width = range_high - range_low

            # LONG at range low
            near_low = close <= range_low * 1.005
            sweep_ok = (not require_sweep) or _is_liquidity_sweep(df, range_low, "buy")
            if near_low and sweep_ok and rsi <= self.cfg["rsi_oversold"]:
                sl = range_low - range_width * 0.3
                tp = range_low + range_width * tp_range_pct
                rr = (tp - close) / max(close - sl, 1e-8)
                if rr >= mr_min_rr:
                    return "buy", sl, tp

            # SHORT at range high
            near_high = close >= range_high * 0.995
            sweep_ok = (not require_sweep) or _is_liquidity_sweep(df, range_high, "sell")
            if near_high and sweep_ok and rsi >= self.cfg["rsi_overbought"]:
                sl = range_high + range_width * 0.3
                tp = range_high - range_width * tp_range_pct
                rr = (close - tp) / max(sl - close, 1e-8)
                if rr >= mr_min_rr:
                    return "sell", sl, tp

        # --- Fallback: BB extreme + RSI (original logic) ---
        bb_lower_val = curr["bb_lower"]
        bb_upper_val = curr["bb_upper"]
        bb_mid_val   = curr["bb_mid"]

        at_lower = close <= bb_lower_val * 1.002
        trend_ok = True
        if self.cfg.get("trend_filter"):
            trend_ok = close <= ema200 * 1.02
        if at_lower and rsi <= self.cfg["rsi_oversold"] and trend_ok:
            sl_raw = bb_lower_val * (1 - self.cfg["sl_bb_mult"] / 100)
            min_sl = close * 0.98
            sl = min(sl_raw, min_sl)
            tp = bb_mid_val
            rr = (tp - close) / max(close - sl, 1e-8)
            if rr >= mr_min_rr:
                return "buy", sl, tp

        at_upper = close >= bb_upper_val * 0.998
        trend_ok = True
        if self.cfg.get("trend_filter"):
            trend_ok = close >= ema200 * 0.98
        if at_upper and rsi >= self.cfg["rsi_overbought"] and trend_ok:
            sl_raw = bb_upper_val * (1 + self.cfg["sl_bb_mult"] / 100)
            min_sl = close * 1.02
            sl = max(sl_raw, min_sl)
            tp = bb_mid_val
            rr = (close - tp) / max(sl - close, 1e-8)
            if rr >= mr_min_rr:
                return "sell", sl, tp

        return None, None, None

    def _check_exits(self, exchange: BaseExchange, symbol: str) -> bool:
        """Check RSI-based exits and time-based exits. Returns True if any position was closed."""
        positions = self.order_manager.tracker.get_open(
            exchange=exchange.name, symbol=symbol)
        if not positions:
            return False
        df = self.get_dataframe(exchange, symbol, self.cfg["timeframe"], 30)
        if df is None:
            return False
        df["rsi"] = _rsi(df["close"], self.cfg["rsi_period"])
        df.dropna(inplace=True)
        if df.empty:
            return False
        rsi = float(df["rsi"].iloc[-1])
        closed = False
        import time as _t
        max_hold = self.cfg.get("max_hold_candles", 4)
        # Approximate candle duration from timeframe (1h = 3600s)
        tf_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        candle_sec = tf_map.get(self.cfg.get("timeframe", "1h"), 3600)
        max_hold_sec = max_hold * candle_sec

        for pos in positions:
            ticker = exchange.fetch_ticker(symbol, self.market_type)
            price  = ticker.get("last")
            if not price:
                continue

            # Time-based exit: close after max_hold_candles regardless
            age_sec = _t.time() - pos.open_time if hasattr(pos, "open_time") else 0
            if age_sec > max_hold_sec:
                logger.info(f"[MeanReversion] Time exit {symbol} after {age_sec/3600:.1f}h")
                self.order_manager.close_position(exchange, pos, "time_exit", price)
                closed = True
                continue

            # RSI-based exit
            if pos.side == "buy" and rsi >= self.cfg["rsi_exit_long"]:
                logger.info(f"[MeanReversion] RSI exit LONG {symbol} RSI={rsi:.1f}")
                self.order_manager.close_position(exchange, pos, "rsi_exit", price)
                closed = True
            elif pos.side == "sell" and rsi <= self.cfg["rsi_exit_short"]:
                logger.info(f"[MeanReversion] RSI exit SHORT {symbol} RSI={rsi:.1f}")
                self.order_manager.close_position(exchange, pos, "rsi_exit", price)
                closed = True
        return closed

    def run(self, exchange: BaseExchange, symbol: str):
        logger.debug(f"[MeanReversion] {symbol} ({self.market_type})")
        self.order_manager.check_sl_tp(exchange, self.market_type)
        rsi_closed = self._check_exits(exchange, symbol)

        # After RSI exit, don't re-enter in the same cycle
        if rsi_closed:
            return
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
