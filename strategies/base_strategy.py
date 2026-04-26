"""
strategies/base_strategy.py — Abstract base class for all trading strategies.

KEY FIX: get_usdt_balance() now checks for a paper wallet on the order_manager
first. In DRY RUN / multi-profile mode the strategy's order_manager carries a
ProfileWallet (or VirtualWallet) with the paper balance. Only if no paper
wallet is present does it fall back to the real exchange balance API.

FIX: get_dataframe() now requires >= 30 candles (was 2 — useless for indicators).
     Logged at DEBUG level so it doesn't spam the log for unavailable symbols.
"""

from abc import ABC, abstractmethod
import pandas as pd
from loguru import logger
from exchanges.base  import BaseExchange
from core.order_manager import OrderManager
from core.risk_manager  import RiskManager

# Minimum candles needed to compute any indicator reliably
MIN_CANDLES = 30


class BaseStrategy(ABC):

    # Class-level OHLCV cache — shared across all strategies, set by BotEngine
    _ohlcv_cache = None

    def __init__(self, order_manager: OrderManager, risk_manager: RiskManager,
                 name: str, market_type: str = "spot"):
        self.order_manager = order_manager
        self.risk          = risk_manager
        self.name          = name
        self.market_type   = market_type

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame):
        ...

    @abstractmethod
    def run(self, exchange: BaseExchange, symbol: str):
        ...

    def get_dataframe(self, exchange: BaseExchange, symbol: str,
                      timeframe: str, limit: int):
        # Use OHLCV cache if available (avoids redundant exchange API calls)
        if self._ohlcv_cache:
            raw = self._ohlcv_cache.get(exchange, symbol, timeframe, limit, self.market_type)
        else:
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit, self.market_type)
        if not raw or len(raw) < MIN_CANDLES:
            # Downgraded to DEBUG — this is expected for illiquid/new symbols
            # and should not clutter the main INFO log
            logger.debug(
                f"[{self.name}] Insufficient OHLCV data for {symbol} "
                f"({len(raw) if raw else 0} candles, need {MIN_CANDLES}) — skipped")
            return None
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def get_usdt_balance(self, exchange: BaseExchange) -> float:
        """
        Return the USDT balance to use for position sizing.

        Priority:
          1. Paper wallet on the order_manager (DRY RUN / multi-profile)
          2. Real exchange balance (LIVE mode)
        """

        # ── 1. Paper wallet ───────────────────────────────────────────
        wallet = getattr(self.order_manager, "wallet", None)
        if wallet is not None:
            ex_name = getattr(exchange, "name", "binance").lower()
            try:
                bal = wallet.balance(ex_name)
                if bal and bal > 0:
                    logger.debug(
                        f"[{self.name}] Paper wallet {ex_name} "
                        f"{self.market_type}: {bal:.4f} USDT"
                    )
                    return float(bal)
            except Exception:
                pass

        # ── 2. Real exchange balance ──────────────────────────────────
        def _read_usdt(bal_data, ex_name_hint: str = ""):
            if not bal_data:
                return 0.0
            ex_lower = ex_name_hint.lower()

            # Bybit Unified Account: balance is in info.result.list[0].totalEquity
            if ex_lower == "bybit":
                try:
                    lst = bal_data.get("info", {}).get("result", {}).get("list", [{}])
                    if lst:
                        for key in ("totalEquity", "totalWalletBalance"):
                            val = lst[0].get(key)
                            if val:
                                v = float(val)
                                if v > 0:
                                    return v
                except Exception:
                    pass
                # Bybit fallback: ccxt total.USDT
                usdt = bal_data.get("USDT") or {}
                if isinstance(usdt, dict):
                    val = usdt.get("total")
                    if val is not None:
                        try:
                            v = float(val)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass

            # Standard ccxt (Binance, Bitget)
            usdt = bal_data.get("USDT")
            if isinstance(usdt, dict):
                v = usdt.get("free") or usdt.get("total") or 0.0
                if v: return float(v)
            free_d = bal_data.get("free", {})
            if isinstance(free_d, dict):
                v = free_d.get("USDT", 0.0)
                if v: return float(v)
            total_d = bal_data.get("total", {})
            if isinstance(total_d, dict):
                v = total_d.get("USDT", 0.0)
                if v: return float(v)
            return 0.0

        try:
            ex_name = getattr(exchange, "name", "")
            bal_data = exchange.fetch_balance(self.market_type)
            primary  = _read_usdt(bal_data, ex_name)

            if primary > 0:
                logger.debug(
                    f"[{self.name}] {exchange.name} {self.market_type}: "
                    f"{primary:.4f} USDT")
                return primary

            # NO FALLBACK: If this market type has $0, return $0.
            # The old code checked the OTHER account and said "will auto-transfer"
            # but the transfer never happened → DCA spam, failed orders.
            # Rule: spot=0 → return 0. futures=0 → return 0. Period.
            logger.debug(
                f"[{self.name}] {exchange.name} {self.market_type}=0 USDT — skipping")
            return 0.0

        except Exception as e:
            logger.debug(f"[{self.name}] Balance fetch on {exchange.name}: {e}")
            return 0.0

    def log_signal(self, symbol: str, signal: str, price: float,
                   extra: str = ""):
        if signal:
            logger.info(
                f"[{self.name}] SIGNAL {signal.upper()} | "
                f"{symbol} @ {price:.4f} {extra}"
            )
