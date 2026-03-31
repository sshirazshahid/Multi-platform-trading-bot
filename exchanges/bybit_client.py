"""
exchanges/bybit_client.py — Bybit Spot + Unified Perpetuals connector.

Quirks handled:
  - Unified Trading Account: balance is in bal["total"]["USDT"], NOT bal["free"]["USDT"]
  - Perpetuals use "linear" category (USDT-margined), not "future"
  - set_leverage requires buyLeverage + sellLeverage as strings
  - Clock sync via load_time_difference()
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import BYBIT_API_KEY, BYBIT_SECRET_KEY

_PLACEHOLDERS = {"", "none", "null",
                 "your_bybit_api_key_here",
                 "your_bybit_secret_key_here"}


class BybitClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None):
        self._connected = False
        super().__init__(
            api_key = (api_key or BYBIT_API_KEY    or "").strip(),
            secret  = (secret  or BYBIT_SECRET_KEY or "").strip(),
            testnet = False,
        )

    def _init_exchange(self):
        key = self.api_key.lower()
        if key in _PLACEHOLDERS or not key:
            logger.info("[Bybit] No API keys — Bybit skipped.")
            self.exchange   = None
            self._connected = False
            return
        try:
            self.exchange = ccxt.bybit({
                "apiKey": self.api_key,
                "secret": self.secret,
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow":              20000,
                },
                "enableRateLimit": True,
            })
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[Bybit] load_markets failed: {e}")
            self.exchange = None; self._connected = False; return

        try:
            self.exchange.load_time_difference()
        except Exception:
            pass

        try:
            # Unified Account — always use accountType=UNIFIED
            self.exchange.fetch_balance({"accountType": "UNIFIED"})
            self._connected = True
            logger.info("[Bybit] Connected (Unified Account).")
        except Exception as e:
            logger.warning(f"[Bybit] Auth failed: {str(e)[:100]}")
            self.exchange = None; self._connected = False

    def _ok(self): return self._connected and self.exchange is not None

    def switch_to_futures(self):
        if self._ok(): self.exchange.options["defaultType"] = "linear"

    def switch_to_spot(self):
        if self._ok(): self.exchange.options["defaultType"] = "spot"

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100, market_type="spot"):
        if not self._ok(): return []
        if market_type == "futures": self.switch_to_futures()
        else:                        self.switch_to_spot()
        result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
        self.switch_to_spot()
        return result

    def fetch_ticker(self, symbol, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures": self.switch_to_futures()
        result = super().fetch_ticker(symbol, market_type)
        self.switch_to_spot()
        return result

    def fetch_balance(self, market_type: str = "spot") -> dict:
        """
        Bybit Unified Account: always call with accountType=UNIFIED.
        The response returns totalEquity in bal["total"]["USDT"].
        bal["free"]["USDT"] is 0 — do NOT use it for balance display.
        """
        if not self._ok(): return {}
        try:
            return self.exchange.fetch_balance({"accountType": "UNIFIED"})
        except Exception as e:
            logger.debug(f"[Bybit] fetch_balance unified: {e}")
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.debug(f"[Bybit] fetch_balance fallback: {e}")
            return {}

    def create_order(self, symbol, order_type, side, amount,
                     price=None, params=None, market_type="spot"):
        if not self._ok(): return {}
        if market_type == "futures": self.switch_to_futures()
        try:
            result = super().create_order(symbol, order_type, side, amount,
                                          price, params, market_type)
            self.switch_to_spot()
            return result
        except Exception as e:
            if "position side" in str(e).lower() or "positionIdx" in str(e):
                clean = {k: v for k, v in (params or {}).items()
                         if k not in ("positionSide", "reduceOnly")}
                try:
                    result = super().create_order(symbol, order_type, side, amount,
                                                  price, clean, market_type)
                    self.switch_to_spot()
                    return result
                except Exception as e2:
                    self.switch_to_spot(); raise e2
            self.switch_to_spot()
            raise

    def set_leverage(self, symbol, leverage):
        if not self._ok(): return
        self.switch_to_futures()
        try:
            self.exchange.set_leverage(leverage, symbol, {
                "buyLeverage":  str(leverage),
                "sellLeverage": str(leverage),
            })
        except Exception as e:
            if "leverage not modified" not in str(e).lower():
                logger.debug(f"[Bybit] set_leverage {symbol}: {e}")
        self.switch_to_spot()

    @property
    def name(self): return "Bybit"
