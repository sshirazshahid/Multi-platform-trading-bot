"""
exchanges/base.py — Abstract base class for all exchange connectors.

Key behaviours:
- Every method checks self._ready() before touching ccxt
- Binance -1021 timestamp errors trigger auto-sync + one retry
- Symbol-not-found errors are silenced (return empty, not an error log)
"""

from abc import ABC, abstractmethod
from typing import Optional
import ccxt
from loguru import logger


# Error substrings that mean "symbol doesn't exist on this exchange"
_SYMBOL_ERRORS = (
    "does not have market symbol",
    "symbol not found",
    "invalid symbol",
    "market symbol",
    "unknown symbol",
    "not exist",
    "not supported",
    "not available",
    "instrument not found",
    "No market",
    "is not listed",
    "trading pair",
    "symbol error",
)

# Error substrings that mean "clock drift — retry after sync"
_TIMESTAMP_ERRORS = (
    "-1021",
    "Timestamp for this request",
    "outside of the recvWindow",
    "recvWindow",
)

# Transient errors — retry once, log as WARNING not ERROR
_TRANSIENT_ERRORS = (
    "429",
    "rate limit",
    "Too Many Requests",
    "ETIMEDOUT",
    "ECONNRESET",
    "ECONNREFUSED",
    "EHOSTUNREACH",
    "timeout",
    "Timeout",
    "timed out",
    "network",
    "Network",
    "fetch failed",
    "502 Bad Gateway",
    "503 Service",
    "504 Gateway",
    "getaddrinfo",
    "CERT_HAS_EXPIRED",
    "socket hang up",
)


def _is_transient_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _TRANSIENT_ERRORS)


def _is_symbol_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s.lower() in msg for s in _SYMBOL_ERRORS)


def _is_timestamp_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _TIMESTAMP_ERRORS)


class BaseExchange(ABC):

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        self.api_key  = api_key
        self.secret   = secret
        self.testnet  = testnet
        self.exchange: Optional[ccxt.Exchange] = None
        self._connected: bool = False
        self._init_exchange()

    @abstractmethod
    def _init_exchange(self):
        ...

    def _ready(self) -> bool:
        return self.exchange is not None and getattr(self, "_connected", False)

    def _sync_time(self):
        """Re-sync clock offset. Subclasses can override."""
        try:
            if hasattr(self.exchange, "load_time_difference"):
                self.exchange.load_time_difference()
        except Exception:
            pass

    # ── Market data ─────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    limit: int = 100, market_type: str = "spot") -> list:
        if not self._ready():
            return []
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_ohlcv(
                symbol, timeframe, limit=limit, params=params)
        except Exception as e:
            if _is_symbol_error(e):
                logger.debug(f"[{self.name}] {symbol} not available — skipped")
                return []
            if _is_timestamp_error(e):
                logger.debug(f"[{self.name}] Timestamp drift — syncing clock and retrying")
                self._sync_time()
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_ohlcv(
                        symbol, timeframe, limit=limit, params=params)
                except Exception as e2:
                    logger.error(f"[{self.name}] fetch_ohlcv {symbol} (retry): {e2}")
                    return []
            if _is_transient_error(e):
                logger.warning(f"[{self.name}] fetch_ohlcv {symbol}: transient error, retrying...")
                import time as _t; _t.sleep(1)
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit, params=params)
                except Exception:
                    pass
                return []
            logger.warning(f"[{self.name}] fetch_ohlcv {symbol}: {e}")
            return []

    def fetch_ticker(self, symbol: str, market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_ticker(symbol, params=params)
        except Exception as e:
            if _is_symbol_error(e):
                logger.debug(f"[{self.name}] {symbol} not available — skipped")
                return {}
            if _is_timestamp_error(e):
                logger.debug(f"[{self.name}] Timestamp drift — syncing and retrying")
                self._sync_time()
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_ticker(symbol, params=params)
                except Exception as e2:
                    logger.error(f"[{self.name}] fetch_ticker {symbol} (retry): {e2}")
                    return {}
            if _is_transient_error(e):
                logger.warning(f"[{self.name}] fetch_ticker {symbol}: transient, retrying...")
                import time as _t; _t.sleep(1)
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_ticker(symbol, params=params)
                except Exception:
                    pass
                return {}
            logger.warning(f"[{self.name}] fetch_ticker {symbol}: {e}")
            return {}

    def fetch_order_book(self, symbol: str, limit: int = 20,
                         market_type: str = "spot") -> dict:
        if not self._ready():
            return {"bids": [], "asks": []}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_order_book(symbol, limit, params=params)
        except Exception as e:
            if _is_symbol_error(e):
                return {"bids": [], "asks": []}
            logger.error(f"[{self.name}] fetch_order_book {symbol}: {e}")
            return {"bids": [], "asks": []}

    # ── Account ─────────────────────────────────────────────────────

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_balance(params=params)
        except Exception as e:
            if _is_timestamp_error(e):
                logger.debug(f"[{self.name}] Timestamp drift on balance — syncing and retrying")
                self._sync_time()
                try:
                    params = self._futures_params() if market_type == "futures" else {}
                    return self.exchange.fetch_balance(params=params)
                except Exception as e2:
                    logger.error(f"[{self.name}] fetch_balance (retry): {e2}")
                    return {}
            logger.error(f"[{self.name}] fetch_balance: {e}")
            return {}

    def fetch_positions(self, symbols: list = None) -> list:
        if not self._ready():
            return []
        # Try with category=linear first (Bybit requires it), then without
        for params in ({"category": "linear"}, {}):
            try:
                result = self.exchange.fetch_positions(symbols, params=params)
                if result is not None:
                    return result
            except Exception:
                continue
        return []

    # ── Symbol verification ──────────────────────────────────────────

    def has_symbol(self, symbol: str) -> bool:
        """Check if a symbol is available on this exchange."""
        if not self._ready():
            return False
        try:
            markets = self.exchange.markets or self.exchange.load_markets()
            return symbol in markets
        except Exception:
            return False

    # ── Orders ──────────────────────────────────────────────────────

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ready():
            logger.warning(f"[{self.name}] create_order skipped — not connected.")
            return {}
        _params = self._futures_params() if market_type == "futures" else {}
        if params:
            _params.update(params)
        try:
            order = self.exchange.create_order(
                symbol, order_type, side, amount, price, _params)
            logger.info(
                f"[{self.name}] ORDER {side.upper()} {amount} {symbol} "
                f"@ {price or 'MARKET'} | id={order.get('id')}")
            return order
        except Exception as e:
            if _is_timestamp_error(e):
                self._sync_time()
                try:
                    order = self.exchange.create_order(
                        symbol, order_type, side, amount, price, _params)
                    return order
                except Exception as e2:
                    logger.error(f"[{self.name}] create_order {symbol} (retry): {e2}")
                    raise e2
            logger.error(f"[{self.name}] create_order {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str,
                     market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.cancel_order(order_id, symbol, params=params)
        except Exception as e:
            logger.error(f"[{self.name}] cancel_order {order_id}: {e}")
            return {}

    def cancel_all_orders(self, symbol: str, market_type: str = "spot"):
        if not self._ready():
            return
        try:
            # Use self.fetch_open_orders (not exchange directly) to pass futures params
            for o in self.fetch_open_orders(symbol, market_type):
                self.cancel_order(o["id"], symbol, market_type)
            logger.info(f"[{self.name}] Cancelled all orders for {symbol}")
        except Exception as e:
            logger.error(f"[{self.name}] cancel_all_orders {symbol}: {e}")

    def fetch_open_orders(self, symbol: str,
                          market_type: str = "spot") -> list:
        if not self._ready():
            return []
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_open_orders(symbol, params=params)
        except Exception as e:
            logger.error(f"[{self.name}] fetch_open_orders: {e}")
            return []

    def set_leverage(self, symbol: str, leverage: int):
        if not self._ready():
            return
        try:
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"[{self.name}] Leverage set: {leverage}x for {symbol}")
        except Exception as e:
            logger.warning(f"[{self.name}] set_leverage: {e}")

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ready():
            return 0.0001
        try:
            markets = self.exchange.markets or self.exchange.load_markets()
            market  = markets.get(symbol, {})
            return market.get("limits", {}).get("amount", {}).get("min", 0.0001)
        except Exception:
            return 0.0001

    def get_amount_precision(self, symbol: str) -> float:
        """Return the step size for quantity rounding.
        E.g., MEXC futures SOL = 1.0 (whole contracts), Bitget SOL = 0.1."""
        if not self._ready():
            return 0.0001
        try:
            markets = self.exchange.markets or self.exchange.load_markets()
            market = markets.get(symbol, {})
            prec = market.get("precision", {})
            amount_prec = prec.get("amount")
            if amount_prec is not None:
                if isinstance(amount_prec, int):
                    return 10 ** (-amount_prec)
                elif isinstance(amount_prec, float):
                    return amount_prec
            cs = market.get("contractSize")
            if cs and cs > 0:
                return cs
            return 0.0001
        except Exception:
            return 0.0001

    def round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to exchange precision. Rounds UP for whole contracts."""
        import math
        step = self.get_amount_precision(symbol)
        if step <= 0:
            step = 0.0001
        if step >= 1.0:
            rounded = max(step, math.ceil(quantity / step) * step)
        else:
            rounded = math.floor(quantity / step) * step
        return round(rounded, 8)

    def round_price(self, symbol: str, price: float) -> float:
        """Round a price to exchange precision (for SL/TP trigger prices)."""
        if not self._ready():
            return round(price, 6)
        try:
            return float(self.exchange.price_to_precision(symbol, price))
        except Exception:
            pass
        # Fallback: read precision from market info
        try:
            markets = self.exchange.markets or self.exchange.load_markets()
            market = markets.get(symbol, {})
            prec = market.get("precision", {}).get("price")
            if prec is not None:
                if isinstance(prec, int):
                    return round(price, prec)
                elif isinstance(prec, float) and prec > 0:
                    import math
                    return round(math.floor(price / prec) * prec, 10)
        except Exception:
            pass
        return round(price, 6)

    # ── Transfer ────────────────────────────────────────────────────

    def transfer(self, amount: float, from_account: str = "spot",
                 to_account: str = "futures") -> bool:
        """Transfer funds between accounts. Override in subclasses."""
        logger.debug(f"[{self.name}] transfer not implemented")
        return False

    # ── Helpers ─────────────────────────────────────────────────────

    def _futures_params(self) -> dict:
        return {}

    @property
    def name(self) -> str:
        return self.__class__.__name__
