"""
exchanges/base.py — Abstract base class for all exchange connectors.

Every method checks self._ready() before touching ccxt.
Timestamp errors trigger auto-sync + one retry.
Symbol-not-found errors are silenced (return empty, not error).
"""

from abc import ABC, abstractmethod
from typing import Optional
import ccxt
from loguru import logger

_SYMBOL_ERRORS = (
    "does not have market symbol", "symbol not found", "invalid symbol",
    "market symbol", "unknown symbol", "not exist", "not supported",
    "not available", "instrument not found", "No market",
    "is not listed", "trading pair", "symbol error",
)
_TIMESTAMP_ERRORS = (
    "-1021", "Timestamp for this request",
    "outside of the recvWindow", "recvWindow",
)
_TRANSIENT_ERRORS = (
    "429", "rate limit", "Too Many Requests", "ETIMEDOUT",
    "ECONNRESET", "timeout", "Timeout", "timed out",
    "502 Bad Gateway", "503 Service", "504 Gateway",
    "getaddrinfo", "socket hang up",
)


def _is_transient(e):  return any(s in str(e) for s in _TRANSIENT_ERRORS)
def _is_symbol(e):     return any(s.lower() in str(e).lower() for s in _SYMBOL_ERRORS)
def _is_timestamp(e):  return any(s in str(e) for s in _TIMESTAMP_ERRORS)


class BaseExchange(ABC):

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        self.api_key  = api_key
        self.secret   = secret
        self.testnet  = testnet
        self.exchange: Optional[ccxt.Exchange] = None
        self._connected: bool = False
        self._init_exchange()

    @abstractmethod
    def _init_exchange(self): ...

    def _ready(self) -> bool:
        return self.exchange is not None and getattr(self, "_connected", False)

    def _sync_time(self):
        try:
            if hasattr(self.exchange, "load_time_difference"):
                self.exchange.load_time_difference()
        except Exception:
            pass

    # ── Market data ──────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    limit: int = 100, market_type: str = "spot") -> list:
        if not self._ready(): return []
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit, params=params)
        except Exception as e:
            if _is_symbol(e):    return []
            if _is_timestamp(e): self._sync_time()
            if _is_transient(e) or _is_timestamp(e):
                import time as _t; _t.sleep(1)
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit, params=params)
                except Exception: pass
            logger.debug(f"[{self.name}] fetch_ohlcv {symbol}: {e}")
            return []

    def fetch_ticker(self, symbol: str, market_type: str = "spot") -> dict:
        if not self._ready(): return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_ticker(symbol, params=params)
        except Exception as e:
            if _is_symbol(e): return {}
            logger.debug(f"[{self.name}] fetch_ticker {symbol}: {e}")
            return {}

    def fetch_order_book(self, symbol: str, limit: int = 20,
                         market_type: str = "spot") -> dict:
        if not self._ready(): return {"bids": [], "asks": []}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_order_book(symbol, limit, params=params)
        except Exception as e:
            if _is_symbol(e): return {"bids": [], "asks": []}
            logger.debug(f"[{self.name}] fetch_order_book {symbol}: {e}")
            return {"bids": [], "asks": []}

    # ── Account ──────────────────────────────────────────────────────

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ready(): return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_balance(params=params)
        except Exception as e:
            if _is_timestamp(e):
                self._sync_time()
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_balance(params=params)
                except Exception as e2:
                    logger.debug(f"[{self.name}] fetch_balance retry: {e2}")
            logger.debug(f"[{self.name}] fetch_balance: {e}")
            return {}

    def fetch_positions(self, symbols: list = None) -> list:
        if not self._ready(): return []
        try:
            return self.exchange.fetch_positions(symbols)
        except Exception as e:
            logger.debug(f"[{self.name}] fetch_positions: {e}")
            return []

    # ── Orders ───────────────────────────────────────────────────────

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ready():
            raise RuntimeError(f"[{self.name}] Not connected")
        _params = self._futures_params() if market_type == "futures" else {}
        if params: _params.update(params)
        try:
            order = self.exchange.create_order(symbol, order_type, side, amount, price, _params)
            logger.info(f"[{self.name}] ORDER {side.upper()} {amount} {symbol} "
                        f"@ {price or 'MKT'} id={order.get('id')}")
            return order
        except Exception as e:
            if _is_timestamp(e):
                self._sync_time()
                order = self.exchange.create_order(symbol, order_type, side, amount, price, _params)
                return order
            logger.error(f"[{self.name}] create_order {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str,
                     market_type: str = "spot") -> dict:
        if not self._ready(): return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.cancel_order(order_id, symbol, params=params)
        except Exception as e:
            logger.debug(f"[{self.name}] cancel_order {order_id}: {e}")
            return {}

    def fetch_open_orders(self, symbol: str,
                          market_type: str = "spot") -> list:
        if not self._ready(): return []
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_open_orders(symbol, params=params)
        except Exception as e:
            logger.debug(f"[{self.name}] fetch_open_orders: {e}")
            return []

    def set_leverage(self, symbol: str, leverage: int):
        if not self._ready(): return
        try:
            self.exchange.set_leverage(leverage, symbol)
        except Exception as e:
            logger.debug(f"[{self.name}] set_leverage {symbol}: {e}")

    # ── Precision ────────────────────────────────────────────────────

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ready(): return 0.0001
        try:
            market = (self.exchange.markets or self.exchange.load_markets()).get(symbol, {})
            return market.get("limits", {}).get("amount", {}).get("min", 0.0001)
        except Exception:
            return 0.0001

    def get_amount_precision(self, symbol: str) -> float:
        if not self._ready(): return 0.0001
        try:
            market = (self.exchange.markets or self.exchange.load_markets()).get(symbol, {})
            prec = market.get("precision", {}).get("amount")
            if prec is not None:
                if isinstance(prec, int):   return 10 ** (-prec)
                if isinstance(prec, float): return prec
            cs = market.get("contractSize")
            if cs and cs > 0: return cs
        except Exception:
            pass
        return 0.0001

    def round_quantity(self, symbol: str, quantity: float) -> float:
        import math
        step = self.get_amount_precision(symbol)
        if step <= 0: step = 0.0001
        if step >= 1.0:
            return max(step, math.ceil(quantity / step) * step)
        return round(math.floor(quantity / step) * step, 8)

    def has_symbol(self, symbol: str) -> bool:
        if not self._ready(): return False
        try:
            return symbol in (self.exchange.markets or self.exchange.load_markets())
        except Exception:
            return False

    # ── Helpers ──────────────────────────────────────────────────────

    def _futures_params(self) -> dict:
        return {}

    @property
    def name(self) -> str:
        return self.__class__.__name__
