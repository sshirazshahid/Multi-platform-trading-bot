"""
exchanges/base.py — Abstract base class for all exchange connectors.

Key behaviours:
- Every method checks self._ready() before touching ccxt
- Binance -1021 timestamp errors trigger auto-sync + one retry
- Symbol-not-found errors are silenced (return empty, not an error log)
"""

from abc import ABC, abstractmethod
from typing import Optional
import random
import time as _time
import ccxt
from loguru import logger


# ── Retry configuration ────────────────────────────────────────────────
MAX_RETRIES       = 3       # total attempts (1 original + 2 retries)
BACKOFF_BASE_SEC  = 1.0     # base delay: 1s, 2s, 4s ...
BACKOFF_MAX_SEC   = 8.0     # cap delay
JITTER_FACTOR     = 0.3     # ±30% randomness on delay


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt ± jitter."""
    delay = min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_MAX_SEC)
    jitter = delay * JITTER_FACTOR * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


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
    "-1021",                          # Binance
    "Timestamp for this request",     # Binance
    "outside of the recvWindow",      # Binance
    "recvWindow",                     # Binance camelCase
    "recv_window",                    # Bybit underscore format
    "10002",                          # Bybit retCode for timestamp errors
    "timestamp",                      # Generic catch-all (case-sensitive avoids false positives)
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
        for attempt in range(MAX_RETRIES):
            try:
                params = self._futures_params() if market_type == "futures" else {}
                return self.exchange.fetch_ohlcv(
                    symbol, timeframe, limit=limit, params=params)
            except Exception as e:
                if _is_symbol_error(e):
                    logger.debug(f"[{self.name}] {symbol} not available — skipped")
                    return []
                if _is_timestamp_error(e):
                    self._sync_time()
                    continue  # retry after clock sync
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        f"[{self.name}] fetch_ohlcv {symbol}: transient error, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                if attempt == MAX_RETRIES - 1:
                    logger.warning(f"[{self.name}] fetch_ohlcv {symbol}: "
                                   f"failed after {MAX_RETRIES} attempts: {e}")
                else:
                    logger.warning(f"[{self.name}] fetch_ohlcv {symbol}: {e}")
                return []
        return []

    def fetch_ticker(self, symbol: str, market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        for attempt in range(MAX_RETRIES):
            try:
                params = self._futures_params() if market_type == "futures" else {}
                return self.exchange.fetch_ticker(symbol, params=params)
            except Exception as e:
                if _is_symbol_error(e):
                    logger.debug(f"[{self.name}] {symbol} not available — skipped")
                    return {}
                if _is_timestamp_error(e):
                    self._sync_time()
                    continue
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        f"[{self.name}] fetch_ticker {symbol}: transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                if attempt == MAX_RETRIES - 1:
                    logger.warning(f"[{self.name}] fetch_ticker {symbol}: "
                                   f"failed after {MAX_RETRIES} attempts: {e}")
                else:
                    logger.warning(f"[{self.name}] fetch_ticker {symbol}: {e}")
                return {}
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
        for attempt in range(MAX_RETRIES):
            try:
                params = self._futures_params() if market_type == "futures" else {}
                return self.exchange.fetch_balance(params=params)
            except Exception as e:
                if _is_timestamp_error(e):
                    self._sync_time()
                    continue
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        f"[{self.name}] fetch_balance: transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                logger.error(f"[{self.name}] fetch_balance: {e}")
                return {}
        return {}

    def fetch_positions(self, symbols: list = None) -> list:
        if not self._ready():
            return []
        try:
            result = self.exchange.fetch_positions(symbols) or []
            return result
        except Exception as e:
            logger.debug(f"[{self.name}] fetch_positions: {e}")
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
        import uuid as _uuid
        _params.setdefault("clientOrderId", _uuid.uuid4().hex[:24])
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                order = self.exchange.create_order(
                    symbol, order_type, side, amount, price, _params)
                logger.info(
                    f"[{self.name}] ORDER {side.upper()} {amount} {symbol} "
                    f"@ {price or 'MARKET'} | id={order.get('id')}")
                return order
            except Exception as e:
                last_err = e
                if _is_timestamp_error(e):
                    self._sync_time()
                    continue
                # Network timeouts on market orders are UNSAFE to retry —
                # the order may have been filled; retrying doubles position size.
                if order_type == "market" and _is_transient_error(e):
                    logger.warning(
                        f"[{self.name}] create_order {symbol}: network error on MARKET order "
                        f"— NOT retrying to avoid duplicate fill: {str(e)[:80]}")
                    raise
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    # Regenerate clientOrderId for the retry: if the prior
                    # attempt actually reached the venue before the error,
                    # the ID is reserved (Bybit 110072 OrderLinkedID is
                    # duplicate) and reusing it guarantees the retry fails.
                    _params["clientOrderId"] = _uuid.uuid4().hex[:24]
                    if "newClientOrderId" in _params:
                        _params["newClientOrderId"] = _params["clientOrderId"]
                    logger.warning(
                        f"[{self.name}] create_order {symbol}: transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                logger.error(f"[{self.name}] create_order {symbol}: {e}")
                raise
        logger.error(f"[{self.name}] create_order {symbol}: failed after {MAX_RETRIES} attempts")
        raise last_err

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
            # 2026-04-20: Binance USD-M separates regular and algo (conditional)
            # order books. A plain fetch_open_orders returns only regular
            # orders, so STOP_MARKET / TAKE_PROFIT_MARKET SL/TP orders survive
            # cancel_all_orders and can fire after a close → naked reverse.
            # Second pass with stop=True reaches the algo endpoint. Safe on
            # other venues (Bybit/Bitget either support the flag or return
            # empty).
            if market_type == "futures":
                try:
                    algo = self.exchange.fetch_open_orders(
                        symbol, params={"stop": True}) or []
                    for o in algo:
                        try:
                            self.exchange.cancel_order(
                                o["id"], symbol, params={"stop": True})
                        except Exception as _ce:
                            logger.debug(
                                f"[{self.name}] algo cancel {o.get('id')}: {_ce}")
                except Exception as _ae:
                    logger.debug(f"[{self.name}] algo fetch skipped: {_ae}")
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

    def set_leverage(self, symbol: str, leverage: int) -> int:
        """Set leverage for symbol on this exchange.

        Returns the actually-applied leverage. Exchanges cap per-symbol
        leverage by notional tier (e.g. Binance allows 125x on BTC at low
        notional but only 25x on AXS at $20K notional). When the requested
        value is rejected, this probes market metadata for the symbol's
        max and walks down a ladder until one succeeds. The caller MUST
        use the returned value (not the requested one) to size the
        position, otherwise margin math diverges from reality.

        Returns 0 if the exchange isn't ready or every attempt failed.
        """
        if not self._ready():
            return 0
        try:
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"[{self.name}] Leverage set: {leverage}x for {symbol}")
            return leverage
        except Exception as e:
            logger.warning(
                f"[{self.name}] set_leverage({leverage}x) rejected for {symbol}: {e}")

        market_max = None
        try:
            markets = self.exchange.markets or self.exchange.load_markets()
            lim = (markets.get(symbol, {}) or {}).get("limits", {}).get("leverage", {})
            if lim.get("max"):
                market_max = int(lim["max"])
        except Exception:
            pass

        ladder = [75, 50, 40, 25, 20, 15, 10, 5, 3, 2, 1]
        ladder = [x for x in ladder if x < leverage]
        if market_max and market_max < leverage:
            ladder = [market_max] + [x for x in ladder if x < market_max]

        for lev in ladder:
            try:
                self.exchange.set_leverage(lev, symbol)
                logger.warning(
                    f"[{self.name}] Leverage CLAMPED {leverage}x → {lev}x "
                    f"for {symbol} (exchange tier cap)")
                return lev
            except Exception:
                continue

        logger.error(
            f"[{self.name}] Could not set ANY leverage for {symbol}; aborting trade")
        return 0

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
        E.g., Bitget SOL = 0.1 (fractional contracts)."""
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
        """Round quantity to exchange precision. Rounds DOWN to avoid over-exposure."""
        import math
        step = self.get_amount_precision(symbol)
        if step <= 0:
            step = 0.0001
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

    # ── Trade history ─────────────────────────────────────────────

    def fetch_my_trades(self, symbol: str, since: int = None,
                        limit: int = 100, market_type: str = "spot") -> list:
        """Fetch user's trade history for a symbol via ccxt."""
        if not self._ready():
            return []
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.fetch_my_trades(
                symbol, since=since, limit=limit, params=params)
        except Exception as e:
            logger.debug(f"[{self.name}] fetch_my_trades {symbol}: {e}")
            return []

    def fetch_closed_pnl(self, since_ms: int = None,
                          symbol: str = None) -> list:
        """Return normalized realized-PnL records from futures history.

        Each record: {exchange, symbol, side, realized_pnl, close_time,
                      entry_price, exit_price, size, leverage, trade_id}.
        close_time is unix seconds.

        Default (base) implementation returns empty; subclasses override
        with exchange-specific endpoints. Used by PositionTracker to
        reconcile manually-closed trades so they show in today's stats.
        """
        return []

    # ── Helpers ─────────────────────────────────────────────────────

    def _futures_params(self) -> dict:
        return {}

    @property
    def name(self) -> str:
        return self.__class__.__name__
