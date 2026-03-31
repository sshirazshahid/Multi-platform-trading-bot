"""
exchanges/bitget_client.py — Bitget Spot + USDT-Margined Futures connector.

Quirks handled:
  - Requires Passphrase in addition to API key + secret
  - Futures balance via params={"type": "umcbl"}
  - Futures use "swap" defaultType
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

_PLACEHOLDERS = {"", "none", "null",
                 "your_bitget_api_key_here",
                 "your_bitget_secret_key_here",
                 "your_bitget_passphrase_here"}


class BitgetClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None,
                 passphrase: str = None):
        self._connected = False
        self._passphrase = (passphrase or BITGET_PASSPHRASE or "").strip()
        super().__init__(
            api_key = (api_key or BITGET_API_KEY    or "").strip(),
            secret  = (secret  or BITGET_SECRET_KEY or "").strip(),
            testnet = False,
        )

    def _init_exchange(self):
        key = self.api_key.lower()
        if key in _PLACEHOLDERS or not key:
            logger.info("[Bitget] No API keys — Bitget skipped.")
            self.exchange = None; self._connected = False; return
        try:
            self.exchange = ccxt.bitget({
                "apiKey":     self.api_key,
                "secret":     self.secret,
                "password":   self._passphrase,
                "options":    {"defaultType": "spot"},
                "enableRateLimit": True,
            })
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[Bitget] load_markets failed: {e}")
            self.exchange = None; self._connected = False; return

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[Bitget] Connected.")
        except Exception as e:
            logger.warning(f"[Bitget] Auth failed: {str(e)[:100]}")
            self.exchange = None; self._connected = False

    def _ok(self): return self._connected and self.exchange is not None

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        if not self._ok(): return []
        self.exchange.options["defaultType"] = "swap" if market_type == "futures" else "spot"
        result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_ticker(self, symbol, market_type="spot"):
        if not self._ok(): return {}
        self.exchange.options["defaultType"] = "swap" if market_type == "futures" else "spot"
        result = super().fetch_ticker(symbol, market_type)
        self.exchange.options["defaultType"] = "spot"
        return result

    def fetch_balance(self, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures":
            try:
                return self.exchange.fetch_balance({"type": "umcbl"})
            except Exception as e:
                logger.debug(f"[Bitget] futures balance: {e}")
                return {}
        return super().fetch_balance(market_type)

    def create_order(self, symbol, order_type, side, amount,
                     price=None, params=None, market_type="spot"):
        if not self._ok(): return {}
        self.exchange.options["defaultType"] = "swap" if market_type == "futures" else "spot"
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
        self.exchange.options["defaultType"] = "swap"
        try:
            self.exchange.set_leverage(leverage, symbol,
                                       {"marginCoin": "USDT"})
        except Exception as e:
            logger.debug(f"[Bitget] set_leverage {symbol}: {e}")
        self.exchange.options["defaultType"] = "spot"

    @property
    def name(self): return "Bitget"
