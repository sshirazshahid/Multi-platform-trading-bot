"""
exchanges/mexc_client.py — MEXC connector (Spot + Futures).

Skips all operations when placeholder keys are detected or auth fails.

MEXC API quirks handled here (all bypassing super() to avoid ERROR logs):
  - fetch_ticker: v3 spot ticker endpoint unreliable — falls back to OHLCV
  - fetch_ohlcv:  MEXC uses non-standard interval strings and endpoints
                  that differ between spot (60m) and futures (Hour4 etc.)
                  Failures are logged at DEBUG level only, never ERROR.
"""

import ccxt
from loguru import logger
from .base import BaseExchange
from config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_TESTNET

_PLACEHOLDERS = {
    "", "none", "null",
    "your_mexc_api_key_here",
    "your_mexc_secret_key_here",
}

# MEXC spot API uses different interval strings than standard ccxt
# Map standard ccxt timeframes to MEXC v3 spot intervals
# MEXC v3 spot API valid intervals: 1m, 5m, 15m, 30m, 60m, 4h, 8h, 1d, 1W, 1M
# "240m" is INVALID — MEXC uses "4h" directly
_MEXC_SPOT_INTERVALS = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "60m",
    "2h":  "4h",     # No 2h on MEXC — use 4h as fallback
    "4h":  "4h",     # MEXC uses "4h" not "240m"
    "8h":  "8h",
    "1d":  "1d",
    "1w":  "1W",
    "1M":  "1M",
}


class MEXCClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None,
                 testnet: bool = None):
        self._connected = False
        super().__init__(
            api_key = (api_key or MEXC_API_KEY   or "").strip(),
            secret  = (secret  or MEXC_SECRET_KEY or "").strip(),
            testnet = testnet if testnet is not None else MEXC_TESTNET,
        )

    def _init_exchange(self):
        key = self.api_key.lower().strip()
        sec = self.secret.lower().strip()

        if key in _PLACEHOLDERS or sec in _PLACEHOLDERS or not key or not sec:
            logger.info(
                "[MEXC] No API keys configured — MEXC will be skipped. "
                "Add keys via Option 8 in the launcher."
            )
            self.exchange   = None
            self._connected = False
            return

        try:
            self.exchange = ccxt.mexc({
                "apiKey":  self.api_key,
                "secret":  self.secret,
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow":              60000,
                },
                "enableRateLimit": True,
            })
            try:
                self.exchange.load_time_difference()
            except Exception:
                pass
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[MEXC] Failed to initialise: {e}")
            self.exchange   = None
            self._connected = False
            return

        # MEXC futures is geo-blocked from Pakistan
        self._futures_blocked = True

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[MEXC] Connected (SPOT ONLY — futures geo-blocked).")
        except Exception as e:
            logger.warning(
                f"[MEXC] Authentication failed: {str(e)[:120]} "
                "— check your MEXC API key and secret."
            )
            self.exchange   = None
            self._connected = False

    def _ok(self) -> bool:
        return self._connected and self.exchange is not None

    def switch_to_futures(self):
        if self._ok():
            self.exchange.options["defaultType"] = "swap"

    def switch_to_spot(self):
        if self._ok():
            self.exchange.options["defaultType"] = "spot"

    # ── fetch_ohlcv: fully custom — bypasses base class ERROR log ─────
    # MEXC uses non-standard interval strings and separate endpoints for
    # spot vs futures. All failures are logged at DEBUG level only.

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    limit: int = 100, market_type: str = "spot") -> list:
        if not self._ok():
            return []

        if market_type == "futures":
            return self._fetch_ohlcv_futures(symbol, timeframe, limit)
        else:
            return self._fetch_ohlcv_spot(symbol, timeframe, limit)

    def _fetch_ohlcv_spot(self, symbol: str, timeframe: str,
                          limit: int) -> list:
        """Fetch spot OHLCV using MEXC's v3 spot klines endpoint."""
        self.switch_to_spot()

        # Map timeframe to MEXC's interval string
        interval = _MEXC_SPOT_INTERVALS.get(timeframe, timeframe)

        # Attempt 1: standard ccxt call with mapped interval
        try:
            data = self.exchange.fetch_ohlcv(symbol, interval, limit=limit)
            if data and len(data) >= 2:
                return data
        except Exception as e:
            logger.debug(f"[MEXC] fetch_ohlcv spot {symbol} attempt 1: {e}")

        # Attempt 2: try with original timeframe string as-is
        if interval != timeframe:
            try:
                data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                if data and len(data) >= 2:
                    return data
            except Exception as e:
                logger.debug(f"[MEXC] fetch_ohlcv spot {symbol} attempt 2: {e}")

        logger.debug(
            f"[MEXC] fetch_ohlcv spot {symbol} {timeframe} — "
            f"no data available, skipping."
        )
        return []

    def _fetch_ohlcv_futures(self, symbol: str, timeframe: str,
                              limit: int) -> list:
        """Fetch futures OHLCV using MEXC's swap endpoint."""
        if getattr(self, '_futures_blocked', False):
            return []
        self.switch_to_futures()

        try:
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            self.switch_to_spot()
            if data and len(data) >= 2:
                return data
        except Exception as e:
            logger.debug(
                f"[MEXC] fetch_ohlcv futures {symbol} {timeframe}: {e}"
            )

        self.switch_to_spot()
        return []

    # ── fetch_ticker: fully custom — no super() call ─────────────────
    # Primary: normal ticker call. Fallback: last OHLCV candle close.
    # All failures at DEBUG level only.

    def fetch_ticker(self, symbol: str, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}

        if market_type == "futures":
            self.switch_to_futures()

        try:
            result = self.exchange.fetch_ticker(symbol)
            if result and (result.get("last") or result.get("close")):
                return result
        except Exception as e:
            logger.debug(f"[MEXC] fetch_ticker {symbol} primary: {e}")

        # Fallback: last 1m OHLCV candle close price
        try:
            self.switch_to_spot()
            candles = self.exchange.fetch_ohlcv(symbol, "1m", limit=1)
            if candles:
                price = candles[-1][4]
                logger.debug(
                    f"[MEXC] fetch_ticker {symbol}: OHLCV fallback price={price}"
                )
                return {"last": price, "close": price, "symbol": symbol}
        except Exception as e:
            logger.debug(f"[MEXC] fetch_ticker {symbol} fallback: {e}")
        finally:
            self.switch_to_spot()

        return {}

    # ── All other methods use base class (with _ready() guard) ────────

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}
        if market_type == "futures":
            if getattr(self, '_futures_blocked', False):
                return {}
            self.switch_to_futures()
            try:
                bal = self.exchange.fetch_balance()
                self.switch_to_spot()
                return bal
            except Exception as e:
                logger.debug(f"[MEXC] fetch_balance futures: {e}")
                self.switch_to_spot()
                return {}
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.debug(f"[MEXC] fetch_balance spot attempt 1: {e}")
            # Retry once — MEXC spot API can be flaky
            import time as _time
            _time.sleep(1)
            try:
                return self.exchange.fetch_balance()
            except Exception as e2:
                logger.debug(f"[MEXC] fetch_balance spot attempt 2: {e2}")
                return {}

    def fetch_order_book(self, symbol: str, limit: int = 20,
                         market_type: str = "spot") -> dict:
        if not self._ok():
            return {"bids": [], "asks": []}
        return super().fetch_order_book(symbol, limit, market_type)

    def fetch_open_orders(self, symbol: str,
                          market_type: str = "spot") -> list:
        if not self._ok():
            return []
        return super().fetch_open_orders(symbol, market_type)

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ok():
            logger.warning("[MEXC] create_order skipped — not connected.")
            return {}
        if market_type == "futures":
            self.switch_to_futures()

        # MEXC futures may be geo-blocked (403 from Akamai CDN)
        if market_type == "futures" and hasattr(self, '_futures_blocked') and self._futures_blocked:
            logger.debug("[MEXC] Futures API geo-blocked \u2014 skipping order")
            self.switch_to_spot()
            return {}

        # MEXC spot market BUY requires price to calculate cost
        if (market_type == "spot" and order_type == "market"
                and side == "buy" and not price):
            try:
                t = self.exchange.fetch_ticker(symbol)
                price = float(t.get("last") or t.get("close") or 0)
            except Exception:
                pass

        try:
            order = super().create_order(symbol, order_type, side, amount,
                                         price, params, market_type)
            return order
        except Exception as e:
            err = str(e)
            if "403" in err or "Access Denied" in err or "Forbidden" in err:
                self._futures_blocked = True
                logger.warning(
                    "[MEXC] Futures API BLOCKED (403/geo-restricted). "
                    "Use a VPN or trade MEXC spot only.")
                return {}
            raise
        finally:
            self.switch_to_spot()

    def set_leverage(self, symbol: str, leverage: int):
        if not self._ok() or getattr(self, '_futures_blocked', False):
            return
        self.switch_to_futures()
        try:
            super().set_leverage(symbol, leverage)
        finally:
            self.switch_to_spot()

    def transfer(self, amount: float, from_account: str = "spot",
                 to_account: str = "futures") -> bool:
        """Transfer USDT between spot and futures accounts."""
        if not self._ok():
            return False
        # MEXC futures is geo-blocked — no point attempting transfers
        if getattr(self, '_futures_blocked', False):
            logger.debug("[MEXC] Transfer skipped — futures geo-blocked")
            return False
        try:
            self.exchange.transfer("USDT", amount, from_account, to_account)
            logger.info(f"[MEXC] Transferred {amount:.4f} USDT {from_account} \u2192 {to_account}")
            return True
        except Exception as e:
            if "403" in str(e) or "Forbidden" in str(e):
                self._futures_blocked = True
            logger.warning(f"[MEXC] Transfer failed: {e}")
            return False

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    @property
    def name(self) -> str:
        return "MEXC"
