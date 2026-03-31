"""
exchanges/binance_client.py — Binance Spot + USDT-Margined Futures connector.

Quirks handled:
  - Clock drift → -1021 error → auto-sync via load_time_difference()
  - Testnet support (BINANCE_TESTNET=true)
  - defaultType switches between "spot" and "future"
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_TESTNET

_PLACEHOLDERS = {"", "none", "null", "your_binance_api_key_here",
                 "your_binance_secret_key_here"}


class BinanceClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None):
        self._connected = False
        super().__init__(
            api_key = (api_key or BINANCE_API_KEY    or "").strip(),
            secret  = (secret  or BINANCE_SECRET_KEY or "").strip(),
            testnet = BINANCE_TESTNET,
        )

    def _init_exchange(self):
        key = self.api_key.lower()
        if key in _PLACEHOLDERS or not key:
            logger.info("[Binance] No API keys — Binance skipped.")
            self.exchange   = None
            self._connected = False
            return
        try:
            self.exchange = ccxt.binance({
                "apiKey": self.api_key,
                "secret": self.secret,
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                },
                "enableRateLimit": True,
            })
            if self.testnet:
                self.exchange.set_sandbox_mode(True)
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[Binance] load_markets failed: {e}")
            self.exchange = None; self._connected = False; return

        try:
            self.exchange.load_time_difference()
        except Exception:
            pass

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[Binance] Connected.")
        except Exception as e:
            logger.warning(f"[Binance] Auth failed: {str(e)[:100]}")
            self.exchange = None; self._connected = False

    def _ok(self): return self._connected and self.exchange is not None

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        if not self._ok(): return []
        self.exchange.options["defaultType"] = "future" if market_type == "futures" else "spot"
        result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_ticker(self, symbol, market_type="spot"):
        if not self._ok(): return {}
        self.exchange.options["defaultType"] = "future" if market_type == "futures" else "spot"
        result = super().fetch_ticker(symbol, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_balance(self, market_type="spot"):
        if not self._ok(): return {}
        self.exchange.options["defaultType"] = "future" if market_type == "futures" else "spot"
        result = super().fetch_balance(market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def create_order(self, symbol, order_type, side, amount,
                     price=None, params=None, market_type="spot"):
        if not self._ok(): return {}
        self.exchange.options["defaultType"] = "future" if market_type == "futures" else "spot"
        try:
            result = super().create_order(symbol, order_type, side, amount,
                                          price, params, market_type)
            self.exchange.options["defaultType"] = "spot"
            return result
        except Exception:
            self.exchange.options["defaultType"] = "spot"
            raise

    def set_leverage(self, symbol, leverage):
        if not self._ok(): return
        self.exchange.options["defaultType"] = "future"
        try:
            self.exchange.set_leverage(leverage, symbol,
                                       {"marginMode": "isolated"})
        except Exception as e:
            if "no need to change" not in str(e).lower():
                logger.debug(f"[Binance] set_leverage {symbol}: {e}")
        self.exchange.options["defaultType"] = "spot"

    def _sync_time(self):
        try:
            self.exchange.load_time_difference()
        except Exception:
            pass

    @property
    def name(self): return "Binance"
