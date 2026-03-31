"""
strategies/base_strategy.py — Abstract base for all trading strategies.

FIX: get_dataframe() requires >= 30 candles (not 2 — useless for indicators).
     Insufficient data logged at DEBUG, not WARNING, to avoid log spam.
FIX: get_usdt_balance() checks paper wallet first (DRY RUN / multi-profile),
     falls back to exchange balance only in LIVE mode.
"""

from abc import ABC, abstractmethod
import pandas as pd
from loguru import logger
from exchanges.base     import BaseExchange
from core.order_manager import OrderManager
from core.risk_manager  import RiskManager

MIN_CANDLES = 30  # Minimum candles needed for any indicator to be meaningful


class BaseStrategy(ABC):

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 name: str, market_type: str = "spot"):
        self.order_manager = order_manager
        self.risk          = risk_manager
        self.name          = name
        self.market_type   = market_type

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame):
        """Return "buy", "sell", or None."""
        ...

    @abstractmethod
    def run(self, exchange: BaseExchange, symbol: str):
        ...

    def get_dataframe(self, exchange: BaseExchange, symbol: str,
                      timeframe: str, limit: int) -> pd.DataFrame | None:
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit, self.market_type)
        if not raw or len(raw) < MIN_CANDLES:
            logger.debug(
                f"[{self.name}] Insufficient OHLCV for {symbol} "
                f"({len(raw) if raw else 0} candles, need {MIN_CANDLES}) — skipped")
            return None
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def get_usdt_balance(self, exchange: BaseExchange) -> float:
        """
        Return USDT balance for position sizing.

        Priority:
          1. Paper wallet on order_manager (DRY RUN / multi-profile)
          2. Real exchange balance (LIVE mode)
        """
        wallet = getattr(self.order_manager, "wallet", None)
        if wallet is not None:
            ex_name = getattr(exchange, "name", "binance").lower()
            try:
                bal = wallet.balance(ex_name)
                if bal and bal > 0:
                    return float(bal)
            except Exception:
                pass

        # Live balance fallback
        def _read(bal_data):
            if not bal_data: return 0.0
            usdt = bal_data.get("USDT")
            if isinstance(usdt, dict):
                v = usdt.get("free") or usdt.get("total") or 0.0
                if v: return float(v)
            free = bal_data.get("free", {})
            if isinstance(free, dict) and free.get("USDT"):
                return float(free["USDT"])
            return 0.0

        try:
            return _read(exchange.fetch_balance(self.market_type)) or 0.0
        except Exception as e:
            logger.debug(f"[{self.name}] Balance fetch: {e}")
            return 0.0

    def log_signal(self, symbol: str, signal: str, price: float, extra: str = ""):
        if signal:
            logger.info(
                f"[{self.name}] SIGNAL {signal.upper()} | "
                f"{symbol} @ {price:.6g} {extra}")
