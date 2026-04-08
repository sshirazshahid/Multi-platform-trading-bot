"""
exchanges/bitget_client.py — Bitget connector (Spot + USDT-Margined Futures).

Handles:
  - One-Way mode: reduceOnly=True for closing (NOT tradeSide — that's two-way only)
  - Timeframe mapping: 4h→4Hutc for futures
  - Balance: tries multiple account type params
  - Leverage: requires marginCoin="USDT"
"""

import ccxt
from loguru import logger
from .base  import BaseExchange
from config import BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

_PLACEHOLDERS = {
    "", "none", "null",
    "your_bitget_api_key_here",
    "your_bitget_secret_key_here",
    "your_bitget_passphrase_here",
}


class BitgetClient(BaseExchange):

    # Default to ONE-WAY mode — Bitget accounts are one-way unless explicitly
    # set to hedge mode. This prevents the first order of every session from
    # failing with error 40774 "unilateral position type" before the retry kicks in.
    # NOTE: instance variable (not class variable) to avoid cross-profile contamination
    _is_oneway = True  # overridden per-instance in __init__

    _TF_MAP = {"4h": "4Hutc", "1d": "1Dutc", "12h": "12Hutc", "6h": "6Hutc"}

    def __init__(self, api_key: str = None, secret: str = None,
                 passphrase: str = None):
        self._connected  = False
        self._is_oneway  = True   # Instance variable — safe for multi-profile
        self._passphrase = (passphrase or BITGET_PASSPHRASE or "").strip()
        super().__init__(
            api_key = (api_key or BITGET_API_KEY    or "").strip(),
            secret  = (secret  or BITGET_SECRET_KEY or "").strip(),
            testnet = False,
        )

    def _init_exchange(self):
        key  = self.api_key.lower()
        sec  = self.secret.lower()
        phr  = self._passphrase.lower()

        if (key in _PLACEHOLDERS or sec in _PLACEHOLDERS
                or phr in _PLACEHOLDERS or not key or not sec or not phr):
            logger.info(
                "[Bitget] No API keys configured — Bitget skipped. "
                "Add BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE to .env")
            self.exchange   = None
            self._connected = False
            return

        try:
            self.exchange = ccxt.bitget({
                "apiKey":     self.api_key,
                "secret":     self.secret,
                "password":   self._passphrase,
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                },
                "enableRateLimit": True,
            })
            self.exchange.load_markets()
        except Exception as e:
            logger.error(f"[Bitget] load_markets failed: {e}")
            self.exchange   = None
            self._connected = False
            return

        try:
            self.exchange.load_time_difference()
            logger.debug("[Bitget] Clock synced.")
        except Exception:
            pass

        # Ensure One-Way position mode is set (prevents 40774 errors)
        try:
            self.exchange.set_position_mode(hedged=False, symbol=None)
            logger.debug("[Bitget] Position mode set to One-Way.")
        except Exception as e:
            # Already in one-way mode, or endpoint not available — safe to ignore
            if "already" not in str(e).lower() and "not modified" not in str(e).lower():
                logger.debug(f"[Bitget] set_position_mode: {e}")

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[Bitget] Connected and authenticated.")
        except Exception as e:
            logger.warning(f"[Bitget] Auth failed: {str(e)[:120]}")
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

    # ── transfer ──────────────────────────────────────────────────────

    def transfer(self, amount: float, from_account: str = "spot",
                 to_account: str = "futures") -> bool:
        """Transfer USDT between Bitget spot and futures (USDT-M) accounts.
        Bitget v2 API uses fromType/toType with specific account type strings."""
        if not self._ok():
            return False

        # Bitget v2 account types
        _ACC_MAP = {
            "spot": "spot",
            "futures": "usdt-futures",
            "mix_usdt": "usdt-futures",
        }
        from_type = _ACC_MAP.get(from_account, from_account)
        to_type   = _ACC_MAP.get(to_account, to_account)

        # Method 1: ccxt transfer with Bitget-specific params
        try:
            self.exchange.transfer("USDT", amount, from_type, to_type, {
                "fromAccountType": from_type,
                "toAccountType": to_type,
            })
            logger.info(f"[Bitget] Transferred {amount:.4f} USDT {from_account} -> {to_account}")
            return True
        except Exception as e1:
            logger.debug(f"[Bitget] Transfer method 1: {e1}")

        # Method 2: Use ccxt request() with Bitget v2 transfer endpoint
        try:
            self.exchange.request(
                "api/v2/spot/wallet/transfer",
                api="private",
                method="POST",
                params={
                    "fromType": from_type,
                    "toType": to_type,
                    "amount": str(amount),
                    "coin": "USDT",
                },
            )
            logger.info(f"[Bitget] Transferred {amount:.4f} USDT {from_account} -> {to_account} (v2)")
            return True
        except Exception as e2:
            logger.warning(f"[Bitget] Transfer failed: {e2}")
            return False

    # ── fetch_ohlcv ───────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                    limit: int = 100, market_type: str = "spot") -> list:
        if not self._ok():
            return []
        if market_type == "futures":
            self.switch_to_futures()
            tf = self._TF_MAP.get(timeframe, timeframe)
        else:
            self.switch_to_spot()
            tf = timeframe
        try:
            result = self.exchange.fetch_ohlcv(symbol, tf, limit=limit)
        except Exception as e:
            if tf != timeframe:
                try:
                    result = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                except Exception:
                    result = []
            else:
                logger.debug(f"[Bitget] fetch_ohlcv {symbol} {timeframe}: {e}")
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
            logger.debug(f"[Bitget] fetch_ticker {symbol}: {e}")
            result = {}
        finally:
            self.switch_to_spot()
        return result

    # ── fetch_balance ─────────────────────────────────────────────────

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}
        if market_type == "futures":
            self.switch_to_futures()
            for params in [{}, {"type": "swap"}, {"type": "umcbl"},
                           {"type": "mix"}, {"productType": "USDT-FUTURES"}]:
                try:
                    bal = self.exchange.fetch_balance(params) if params else self.exchange.fetch_balance()
                    self.switch_to_spot()
                    usdt = bal.get("USDT") or bal.get("free", {}).get("USDT")
                    if usdt:
                        return bal
                except Exception:
                    pass
            self.switch_to_spot()
            logger.debug("[Bitget] fetch_balance futures: all methods failed")
            return {}
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.debug(f"[Bitget] fetch_balance spot: {e}")
            return {}

    # ── fetch_order_book ──────────────────────────────────────────────

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

    # ── create_order (with One-Way mode: reduceOnly) ───────────────────

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ok():
            logger.warning("[Bitget] create_order skipped — not connected.")
            return {}
        if market_type == "futures":
            self.switch_to_futures()

        _params = dict(params or {})

        # For One-Way mode on FUTURES:
        # - Remove two-way params (positionSide, holdSide, tradeSide)
        # - KEEP reduceOnly as-is (that's how one-way mode closes positions)
        # - tradeSide="open"/"close" is ONLY valid in two-way (hedge) mode
        #   and causes error 40773 "Closed positions can only occur in two-way positions"
        if self._is_oneway and market_type == "futures":
            _params.pop("positionSide", None)
            _params.pop("holdSide", None)
            _params.pop("tradeSide", None)   # NOT valid in one-way mode
            # reduceOnly stays as-is: True for close, False/absent for open
            _params.setdefault("productType", "USDT-FUTURES")

        try:
            order = super().create_order(symbol, order_type, side, amount,
                                         price, _params, market_type)
            return order
        except Exception as e:
            err = str(e)
            if ("40774" in err or "unilateral" in err or "40773" in err):
                if not self._is_oneway:
                    self._is_oneway = True
                    logger.info("[Bitget] ONE-WAY mode detected. Retrying...")
                # Retry: one-way mode — no tradeSide, keep reduceOnly
                oneway_params = dict(_params)
                oneway_params.pop("positionSide", None)
                oneway_params.pop("holdSide", None)
                oneway_params.pop("tradeSide", None)   # NOT valid in one-way mode
                # reduceOnly stays as-is for close operations
                oneway_params["productType"] = "USDT-FUTURES"
                try:
                    # Call ccxt exchange directly to avoid base class re-adding params
                    order = self.exchange.create_order(
                        symbol, order_type, side, amount, price, oneway_params)
                    logger.info(
                        f"[Bitget] ORDER {side.upper()} {amount} {symbol} "
                        f"@ {price or 'MARKET'} | id={order.get('id')} (one-way retry)")
                    return order
                except Exception as e2:
                    raise e2
            raise
        finally:
            self.switch_to_spot()

    # ── set_leverage ──────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int):
        if not self._ok():
            return
        self.switch_to_futures()
        try:
            self.exchange.set_leverage(
                leverage, symbol,
                params={"marginCoin": "USDT"})
            logger.info(f"[Bitget] Leverage set: {leverage}x for {symbol}")
        except Exception as e:
            if "leverage not modified" in str(e).lower():
                logger.debug(f"[Bitget] Leverage already {leverage}x for {symbol}")
            else:
                logger.warning(f"[Bitget] set_leverage {symbol}: {e}")
        finally:
            self.switch_to_spot()

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    @property
    def name(self) -> str:
        return "Bitget"
