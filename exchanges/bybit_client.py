"""
exchanges/bybit_client.py — Bybit connector (Spot + Unified Perpetuals).

Bybit API quirks handled here:
  - Spot uses "spot" category; Perpetuals use "linear" (USDT-margined)
  - set_leverage uses Bybit's v5 format: {category, symbol, buyLeverage, sellLeverage}
  - fetch_balance returns "unifiedMarginStatus" format under v5 — unified account
  - Timestamp sync via load_time_difference()
  - Bybit uses "linear" for futures defaultType (not "future" like Binance)

DRY_RUN=true prevents real orders — all paper trades go through DirectExecutor.
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import BYBIT_API_KEY, BYBIT_SECRET_KEY

_PLACEHOLDERS = {
    "", "none", "null",
    "your_bybit_api_key_here",
    "your_bybit_secret_key_here",
}


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
        sec = self.secret.lower()

        if key in _PLACEHOLDERS or sec in _PLACEHOLDERS or not key or not sec:
            logger.info(
                "[Bybit] No API keys configured — Bybit skipped. "
                "Add BYBIT_API_KEY and BYBIT_SECRET_KEY to .env"
            )
            self.exchange   = None
            self._connected = False
            return

        try:
            self.exchange = ccxt.bybit({
                "apiKey":  self.api_key,
                "secret":  self.secret,
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow":              20000,
                    "brokerId":                "",
                },
                "enableRateLimit": True,
            })
            # load_markets can fail on /v5/asset/coin-query-info (rate limit / IP)
            # Retry once after a short delay
            import time as _t
            for attempt in range(2):
                try:
                    self.exchange.load_markets()
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.warning(f"[Bybit] load_markets attempt 1 failed, retrying in 3s: {str(e)[:100]}")
                        _t.sleep(3)
                    else:
                        raise
        except Exception as e:
            logger.error(f"[Bybit] load_markets failed: {e}")
            self.exchange   = None
            self._connected = False
            return

        try:
            self.exchange.load_time_difference()
            logger.debug("[Bybit] Clock synced.")
        except Exception as e:
            logger.debug(f"[Bybit] Clock sync skipped: {e}")

        # Verify auth with a lightweight balance check
        try:
            self.exchange.fetch_balance({"accountType": "UNIFIED"})
            self._connected = True
            logger.info("[Bybit] Connected and authenticated.")
        except Exception as e:
            logger.warning(
                f"[Bybit] Authentication failed: {str(e)[:120]} "
                "— check your Bybit API key, secret, and IP whitelist."
            )
            self.exchange   = None
            self._connected = False

    def _ok(self) -> bool:
        return self._connected and self.exchange is not None

    def switch_to_futures(self):
        """Bybit perpetuals use 'linear' type (USDT-margined)."""
        if self._ok():
            self.exchange.options["defaultType"] = "linear"

    def switch_to_spot(self):
        if self._ok():
            self.exchange.options["defaultType"] = "spot"

    # ── fetch_ohlcv ───────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    limit: int = 100, market_type: str = "spot") -> list:
        if not self._ok():
            return []
        if market_type == "futures":
            self.switch_to_futures()
        else:
            self.switch_to_spot()
        try:
            result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
        except Exception as e:
            logger.debug(f"[Bybit] fetch_ohlcv {symbol} {timeframe}: {e}")
            result = []
        finally:
            self.switch_to_spot()
        return result

    # ── fetch_ticker ──────────────────────────────────────────────────

    def fetch_ticker(self, symbol: str, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}
        if market_type == "futures":
            self.switch_to_futures()
        try:
            result = super().fetch_ticker(symbol, market_type)
        except Exception as e:
            logger.debug(f"[Bybit] fetch_ticker {symbol}: {e}")
            result = {}
        finally:
            self.switch_to_spot()
        return result

    # ── fetch_balance ─────────────────────────────────────────────────

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}
        # Bybit Unified Trading Account covers both spot and derivatives
        try:
            bal = self.exchange.fetch_balance({"accountType": "UNIFIED"})
            return bal
        except Exception as e:
            logger.debug(f"[Bybit] fetch_balance unified: {e}")
        # Fallback: try without accountType
        try:
            bal = self.exchange.fetch_balance()
            return bal
        except Exception as e:
            logger.debug(f"[Bybit] fetch_balance fallback: {e}")
            return {}

    # ── fetch_order_book ──────────────────────────────────────────────

    def fetch_order_book(self, symbol: str, limit: int = 20,
                         market_type: str = "spot") -> dict:
        if not self._ok():
            return {"bids": [], "asks": []}
        return super().fetch_order_book(symbol, limit, market_type)

    # ── fetch_open_orders ─────────────────────────────────────────────

    def fetch_open_orders(self, symbol: str,
                          market_type: str = "spot") -> list:
        if not self._ok():
            return []
        return super().fetch_open_orders(symbol, market_type)

    # ── create_order ──────────────────────────────────────────────────

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ok():
            logger.warning("[Bybit] create_order skipped — not connected.")
            return {}
        if market_type == "futures":
            self.switch_to_futures()
        try:
            order = super().create_order(symbol, order_type, side, amount,
                                         price, params, market_type)
            return order
        except Exception as e:
            err = str(e)
            # Handle One-Way mode (positionSide / positionIdx errors)
            if "position side" in err.lower() or "positionIdx" in err.lower() or "position mode" in err.lower():
                logger.info("[Bybit] ONE-WAY mode detected. Retrying...")
                # Keep reduceOnly! Stripping it turns close orders into new position opens
                clean = {k: v for k, v in (params or {}).items()
                         if k not in ("positionSide",)}
                try:
                    order = super().create_order(symbol, order_type, side, amount,
                                                 price, clean, market_type)
                    return order
                except Exception as e2:
                    raise e2
            raise
        finally:
            self.switch_to_spot()

    # ── set_leverage ──────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int):
        """Bybit v5 requires buyLeverage and sellLeverage as strings."""
        if not self._ok():
            return
        self.switch_to_futures()
        try:
            self.exchange.set_leverage(
                leverage, symbol,
                params={
                    "buyLeverage":  str(leverage),
                    "sellLeverage": str(leverage),
                }
            )
            logger.info(f"[Bybit] Leverage set: {leverage}x for {symbol}")
        except Exception as e:
            # Leverage already set at this level — not an error
            if "leverage not modified" in str(e).lower():
                logger.debug(f"[Bybit] Leverage already {leverage}x for {symbol}")
            else:
                logger.warning(f"[Bybit] set_leverage {symbol}: {e}")
        finally:
            self.switch_to_spot()

    # ── get_min_order_size ────────────────────────────────────────────

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    @property
    def name(self) -> str:
        return "Bybit"
