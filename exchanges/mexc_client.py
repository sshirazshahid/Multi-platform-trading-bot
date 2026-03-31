"""
exchanges/mexc_client.py — MEXC Spot + Swap connector.

Quirks handled:
  - Futures API geo-blocked in some regions (403) → _futures_blocked flag
  - MEXC swap uses "swap" defaultType
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import MEXC_API_KEY, MEXC_SECRET_KEY

_PLACEHOLDERS = {"", "none", "null",
                 "your_mexc_api_key_here",
                 "your_mexc_secret_key_here"}


class MEXCClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None):
        self._connected       = False
        self._futures_blocked = False
        super().__init__(
            api_key = (api_key or MEXC_API_KEY    or "").strip(),
            secret  = (secret  or MEXC_SECRET_KEY or "").strip(),
            testnet = False,
        )

    def _init_exchange(self):
        key = self.api_key.lower()
        if key in _PLACEHOLDERS or not key:
            logger.info("[MEXC] No API keys — MEXC skipped.")
            self.exchange = None; self._connected = False; return
        try:
            self.exchange = ccxt.mexc({
                "apiKey": self.api_key,
                "secret": self.secret,
                "options": {"defaultType": "spot"},
                "enableRateLimit": True,
            })
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[MEXC] load_markets failed: {e}")
            self.exchange = None; self._connected = False; return

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[MEXC] Connected.")
        except Exception as e:
            logger.warning(f"[MEXC] Auth failed: {str(e)[:100]}")
            self.exchange = None; self._connected = False

    def _ok(self): return self._connected and self.exchange is not None

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        if not self._ok(): return []
        if market_type == "futures":
            if self._futures_blocked: return []
            self.exchange.options["defaultType"] = "swap"
        else:
            self.exchange.options["defaultType"] = "spot"
        result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_ticker(self, symbol, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures" and self._futures_blocked: return {}
        if market_type == "futures":
            self.exchange.options["defaultType"] = "swap"
        result = super().fetch_ticker(symbol, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_balance(self, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures":
            if self._futures_blocked: return {}
            self.exchange.options["defaultType"] = "swap"
        result = super().fetch_balance(market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def create_order(self, symbol, order_type, side, amount,
                     price=None, params=None, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures":
            if self._futures_blocked:
                logger.warning("[MEXC] Futures blocked — skipping order.")
                return {}
            self.exchange.options["defaultType"] = "swap"
        try:
            result = super().create_order(symbol, order_type, side, amount,
                                          price, params, market_type)
            self.exchange.options["defaultType"] = "spot"
            return result
        except Exception as e:
            if "403" in str(e) or "forbidden" in str(e).lower():
                self._futures_blocked = True
                logger.warning("[MEXC] Futures geo-blocked — disabling futures.")
            self.exchange.options["defaultType"] = "spot"
            raise

    @property
    def name(self): return "MEXC"
