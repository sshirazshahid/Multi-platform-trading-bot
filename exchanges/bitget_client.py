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

from config import BITGET_API_KEY, BITGET_PASSPHRASE, BITGET_SECRET_KEY

from .base import BaseExchange

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
                    # Skip GET /api/v2/spot/public/coins during load_markets.
                    # ccxt signs this call when API keys are present, and
                    # Bitget intermittently returns an empty-body error
                    # (ccxt surfaces it as "bitget GET <url>" with no detail).
                    # We don't use currency metadata anywhere, so opting out
                    # of this call removes the failure mode entirely.
                    "fetchCurrencies":         False,
                },
                "enableRateLimit": True,
                "timeout":         20000,
            })
        except Exception as e:
            logger.error(f"[Bitget] ccxt.bitget() init failed: {e}")
            self.exchange   = None
            self._connected = False
            return

        # Sync clock FIRST — Bitget rejects signed requests whose timestamp
        # drifts from their server, and some of its "public" endpoints get
        # signed when API keys are set.
        try:
            self.exchange.load_time_difference()
            logger.debug(
                f"[Bitget] Clock synced. "
                f"Offset: {self.exchange.options.get('timeDifference', 0)}ms"
            )
        except Exception as e:
            logger.debug(f"[Bitget] Clock sync skipped: {e}")

        # Load markets with one retry — this call is where transient Bitget
        # gateway errors surface. On failure, log what ccxt actually saw.
        for attempt in (1, 2):
            try:
                self.exchange.load_markets(reload=(attempt == 2))
                break
            except Exception as e:
                detail = self._describe_ccxt_error(e)
                if attempt == 1:
                    logger.warning(
                        f"[Bitget] load_markets attempt 1 failed: {detail} — retrying"
                    )
                    continue
                logger.error(f"[Bitget] load_markets failed: {detail}")
                self.exchange   = None
                self._connected = False
                return

        # Ensure One-Way position mode is set (prevents 40774 errors)
        try:
            self.exchange.set_position_mode(hedged=False, symbol=None)
            logger.debug("[Bitget] Position mode set to One-Way.")
        except Exception as e:
            # Already in one-way mode, or endpoint not available — safe to ignore
            if "already" not in str(e).lower() and "not modified" not in str(e).lower():
                logger.warning(f"[Bitget] set_position_mode failed: {e}")

        try:
            self.exchange.fetch_balance()
            self._connected = True
            logger.info("[Bitget] Connected and authenticated.")
        except Exception as e:
            logger.warning(f"[Bitget] Auth failed: {str(e)[:120]}")
            self.exchange   = None
            self._connected = False

    def _describe_ccxt_error(self, e: Exception) -> str:
        """Expand bare ccxt errors with response body / status when available.

        ccxt sometimes stringifies a failure as just "bitget GET <url>" with
        no detail — typically when Bitget's gateway returns an empty body.
        This helper reaches into the ccxt exchange object for the last raw
        HTTP response so the log line actually tells us what happened.
        """
        base = f"{type(e).__name__}: {e}"
        try:
            last_body   = getattr(self.exchange, "last_http_response", None)
            getattr(self.exchange, "last_response_headers", None)
            last_req    = getattr(self.exchange, "last_request_url", None)
        except Exception:
            last_body = last_req = None
        extras = []
        if last_body:
            body_str = str(last_body)
            if body_str.strip():
                extras.append(f"body={body_str[:240]}")
        if last_req:
            extras.append(f"url={last_req}")
        return base + ("  [" + " | ".join(extras) + "]" if extras else "")

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

        Uses ccxt's native transfer() with *unified* account names; ccxt maps
        "swap" -> Bitget's "usdt_futures" internally. Do not pass the
        exchange-native string ("usdt-futures") or use the low-level
        request(api="private") path — both break on ccxt 4.5 (the latter
        raises KeyError 'r' because bitget.sign() expects `api` as a list)."""
        if not self._ok():
            return False

        # bot account vocabulary -> ccxt unified account names
        _UNIFIED = {
            "spot": "spot",
            "futures": "swap",
            "mix_usdt": "swap",
            "swap": "swap",
        }
        from_type = _UNIFIED.get(from_account, from_account)
        to_type   = _UNIFIED.get(to_account, to_account)

        try:
            self.exchange.transfer("USDT", amount, from_type, to_type)
            logger.info(f"[Bitget] Transferred {amount:.4f} USDT {from_account} -> {to_account}")
            return True
        except Exception as e:
            logger.warning(f"[Bitget] Transfer failed: {e}")
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
        else:
            self.switch_to_spot()
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
                    if usdt is not None:
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
                except Exception:
                    raise
            raise
        finally:
            self.switch_to_spot()

    # ── set_leverage ──────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> int:
        """Returns actually-applied leverage; 0 if request fails for a real
        reason (anything other than 'leverage not modified' = already set)."""
        if not self._ok():
            return 0
        self.switch_to_futures()
        try:
            self.exchange.set_leverage(
                leverage, symbol,
                params={"marginCoin": "USDT"})
            logger.info(f"[Bitget] Leverage set: {leverage}x for {symbol}")
            return leverage
        except Exception as e:
            if "leverage not modified" in str(e).lower():
                logger.debug(f"[Bitget] Leverage already {leverage}x for {symbol}")
                return leverage
            logger.warning(f"[Bitget] set_leverage {symbol}: {e}")
            # 2026-05-24 — Ladder fallback (mirrors BaseExchange.set_leverage).
            # Without the ladder, any cap rejection returns 0 → order_manager
            # treats as fatal abort. Preserve marginCoin on each retry.
            ladder = [x for x in (75, 50, 40, 25, 20, 15, 10, 5, 3, 2, 1)
                      if x < leverage]
            for lev in ladder:
                try:
                    self.exchange.set_leverage(
                        lev, symbol, params={"marginCoin": "USDT"})
                    logger.warning(
                        f"[Bitget] Leverage CLAMPED {leverage}x → {lev}x "
                        f"for {symbol} (exchange tier cap)")
                    return lev
                except Exception:
                    continue
            logger.error(
                f"[Bitget] Could not set ANY leverage for {symbol}; aborting")
            return 0
        finally:
            self.switch_to_spot()

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    def fetch_closed_pnl(self, since_ms: int = None,
                          symbol: str = None) -> list:
        """Bitget V2 position-history endpoint for USDT-margined futures."""
        if not self._ok():
            return []
        try:
            params = {"productType": "USDT-FUTURES", "limit": "100"}
            if since_ms:
                params["startTime"] = str(int(since_ms))
            if symbol:
                try:
                    params["symbol"] = self.exchange.market_id(symbol)
                except Exception:
                    params["symbol"] = symbol.replace("/", "").split(":")[0]
            # Prefer v2 history-position; fall back gracefully if not exposed.
            fn = getattr(self.exchange, "privateMixGetV2PositionHistoryPosition",
                         None) or getattr(self.exchange, "privateMixGetPositionHistoryPosition",
                         None)
            if fn is None:
                return []
            response = fn(params) or {}
            records = (response.get("data") or {}).get("list") or []
            if not records and isinstance(response.get("data"), list):
                records = response["data"]
            out = []
            for r in records:
                try:
                    pnl = float(r.get("netProfit", r.get("pnl", 0)) or 0)
                    if pnl == 0:
                        continue
                    raw_sym = str(r.get("symbol", "") or "")
                    if raw_sym.endswith("USDT"):
                        unified = f"{raw_sym[:-4]}/USDT:USDT"
                    else:
                        unified = raw_sym
                    hold_side = (r.get("holdSide", "") or "").lower()
                    side = "buy" if hold_side == "long" else "sell"
                    close_ms = int(r.get("utime", r.get("ctime", 0)) or 0)
                    out.append({
                        "exchange":     "bitget",
                        "symbol":       unified,
                        "side":         side,
                        "realized_pnl": pnl,
                        "close_time":   close_ms / 1000.0,
                        "trade_id":     str(r.get("positionId", "") or ""),
                        "entry_price":  float(r.get("openAvgPrice", 0) or 0),
                        "exit_price":   float(r.get("closeAvgPrice", 0) or 0),
                        "size":         float(r.get("openTotalPos", 0) or 0),
                        "leverage":     int(float(r.get("leverage", 1) or 1)),
                    })
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning(f"[Bitget] fetch_closed_pnl: {str(e)[:150]}")
            return []

    @property
    def name(self) -> str:
        return "Bitget"
