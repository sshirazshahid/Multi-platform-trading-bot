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

import threading

import ccxt
from loguru import logger

from config import BYBIT_API_KEY, BYBIT_SECRET_KEY

from .base import BaseExchange

_PLACEHOLDERS = {
    "", "none", "null",
    "your_bybit_api_key_here",
    "your_bybit_secret_key_here",
}


class BybitClient(BaseExchange):

    def __init__(self, api_key: str = None, secret: str = None):
        self._connected = False
        # 2026-06-04 — Reentrant lock guarding self.exchange.options["defaultType"]
        # (mirrors BinanceClient). Concurrent threads — the 10s SL/TP daemon, the
        # 5min portfolio cycle, and the MCP-brain ThreadPoolExecutor fanning
        # fetch_ohlcv/fetch_ticker across spot+futures on ONE client — otherwise
        # stomp each other's defaultType between the switch and the ccxt call,
        # routing a call to the wrong market (wrong candles/prices/order routing).
        # RLock is reentrant, so a nested switch-using call on the same thread
        # cannot self-deadlock.
        self._defaultType_lock = threading.RLock()
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
                "timeout": 30000,   # 30s hard timeout — prevents scheduler hangs
                "options": {
                    "defaultType":             "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow":              20000,
                    "brokerId":                "",
                    # 2026-04-16: Disable fetchCurrencies — it hits
                    # /v5/asset/coin/query-info which is aggressively
                    # rate-limited and requires auth. Not needed for trading.
                    "fetchCurrencies":         False,
                },
                "enableRateLimit": True,
            })
            # load_markets retry with backoff
            import time as _t
            for attempt in range(3):
                try:
                    self.exchange.load_markets()
                    break
                except Exception as e:
                    if attempt < 2:
                        delay = 3 * (attempt + 1)
                        logger.warning(
                            f"[Bybit] load_markets attempt {attempt+1} failed, "
                            f"retrying in {delay}s: {str(e)[:100]}")
                        _t.sleep(delay)
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
        with self._defaultType_lock:
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
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            else:
                self.switch_to_spot()
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
        # Bybit Unified Trading Account covers both spot and derivatives.
        # 2026-04-16: Use BaseExchange retry/sync loop for timestamp recovery.
        from exchanges.base import (
            MAX_RETRIES,
            _backoff_delay,
            _is_timestamp_error,
            _is_transient_error,
        )
        for attempt in range(MAX_RETRIES):
            try:
                return self.exchange.fetch_balance({"accountType": "UNIFIED"})
            except Exception as e:
                if _is_timestamp_error(e):
                    logger.warning("[Bybit] Timestamp error on balance — re-syncing clock")
                    self._sync_time()
                    continue
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        f"[Bybit] fetch_balance transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    import time as _t
                    _t.sleep(delay)
                    continue
                logger.debug(f"[Bybit] fetch_balance: {e}")
                # Fallback: try without accountType
                try:
                    return self.exchange.fetch_balance()
                except Exception as e2:
                    logger.debug(f"[Bybit] fetch_balance fallback: {e2}")
                    return {}
        return {}

    # ── transfer ──────────────────────────────────────────────────────

    # Bot account names that all resolve to the ONE Unified Trading wallet.
    _UTA_POOLED = {"spot", "futures", "swap", "contract", "unified"}

    def transfer(self, amount: float, from_account: str = "spot",
                 to_account: str = "futures") -> bool:
        """Move USDT between Bybit wallets.

        2026-05-30: this account is a Unified Trading Account (UTA 2.0,
        verified unifiedMarginStatus=5). On a UTA, spot and derivatives share
        ONE margin wallet — there is NO spot<->futures transfer (the UI offers
        none, and the legacy spot<->contract transfer endpoint is gone). A
        spot<->futures move is therefore a NO-OP: the funds are already
        available to both sides. We return True WITHOUT an API call, so the
        capital-allocator profit-sweep neither errors nor churns the transfer
        circuit-breaker. (Bybit migrated all accounts to UTA; the prior
        ccxt transfer("USDT", "contract", "spot") call would just fail here.)

        A genuine cross-wallet move on UTA is Unified<->Funding — a separate
        operation the bot does not currently use; add it explicitly if needed.
        """
        if not self._ok():
            return False

        a, b = (from_account or "").lower(), (to_account or "").lower()
        if a in self._UTA_POOLED and b in self._UTA_POOLED:
            logger.info(
                f"[Bybit] UTA: {from_account}->{to_account} needs no transfer "
                f"(spot & derivatives share one unified wallet) — no-op.")
            return True

        logger.warning(
            f"[Bybit] Unsupported transfer route {from_account}->{to_account} "
            f"on a Unified account (only Unified<->Funding is a real move).")
        return False

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
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            try:
                order = super().create_order(symbol, order_type, side, amount,
                                             price, params, market_type)
                return order
            except Exception as e:
                err = str(e)
                # Handle One-Way mode (positionSide / positionIdx errors)
                if "110025" in err or "110043" in err or "position side" in err.lower() or "positionIdx" in err.lower() or "position mode" in err.lower():
                    logger.info("[Bybit] ONE-WAY mode detected. Retrying...")
                    # Keep reduceOnly! Stripping it turns close orders into new position opens
                    clean = {k: v for k, v in (params or {}).items()
                             if k not in ("positionSide",)}
                    try:
                        order = super().create_order(symbol, order_type, side, amount,
                                                     price, clean, market_type)
                        return order
                    except Exception:
                        raise
                # 2026-04-12: Handle 110007 "ab not enough for new order" — the
                # sizing layer believed more margin was available than the unified
                # account actually had free. Retry with progressively smaller
                # amount (70%, 50%, 30%) so we don't spin on the same failed order
                # while the balance-extract fix propagates.
                if "110007" in err or "ab not enough" in err.lower():
                    skip_retry = (params or {}).get("reduceOnly") is True
                    if not skip_retry:
                        for scale in (0.70, 0.50, 0.30):
                            shrunk = amount * scale
                            logger.warning(
                                f"[Bybit] 110007 insufficient margin — retrying "
                                f"{symbol} at {scale*100:.0f}% size ({shrunk:.6g})")
                            try:
                                order = super().create_order(
                                    symbol, order_type, side, shrunk,
                                    price, params, market_type)
                                logger.info(
                                    f"[Bybit] downsized order filled at "
                                    f"{scale*100:.0f}% ({shrunk:.6g} {symbol})")
                                return order
                            except Exception as e3:
                                if "110007" not in str(e3) and "ab not enough" not in str(e3).lower():
                                    raise
                                continue
                        logger.error(
                            f"[Bybit] 110007 unrecoverable after 3 downsize "
                            f"retries on {symbol}. Bybit reports insufficient "
                            f"margin even at 30%. Likely root cause: "
                            f"balance-extract using totalEquity instead of "
                            f"totalAvailableBalance. Restart bot to apply fix.")
                raise
            finally:
                self.switch_to_spot()

    # ── set_leverage ──────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> int:
        """Bybit v5 requires buyLeverage and sellLeverage as strings.

        Returns the actually-applied leverage. 0 if exchange not ready or
        request fails for a reason other than "already at this level"
        (which is success). The order_manager rescales position size by
        applied/requested to preserve margin.
        """
        if not self._ok():
            return 0
        with self._defaultType_lock:
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
                return leverage
            except Exception as e:
                # Leverage already set at this level — not an error
                if "leverage not modified" in str(e).lower():
                    logger.debug(f"[Bybit] Leverage already {leverage}x for {symbol}")
                    return leverage
                logger.warning(f"[Bybit] set_leverage {symbol}: {e}")
                # 2026-05-24 — Ladder fallback (mirrors BaseExchange.set_leverage).
                # Bybit caps per-symbol leverage by notional tier; without the
                # ladder, any cap rejection returns 0, which order_manager treats
                # as a fatal abort and skips the trade. Preserve venue-specific
                # buyLeverage/sellLeverage params on each retry.
                ladder = [x for x in (75, 50, 40, 25, 20, 15, 10, 5, 3, 2, 1)
                          if x < leverage]
                for lev in ladder:
                    try:
                        self.exchange.set_leverage(
                            lev, symbol,
                            params={
                                "buyLeverage":  str(lev),
                                "sellLeverage": str(lev),
                            }
                        )
                        logger.warning(
                            f"[Bybit] Leverage CLAMPED {leverage}x → {lev}x "
                            f"for {symbol} (exchange tier cap)")
                        return lev
                    except Exception:
                        continue
                logger.error(
                    f"[Bybit] Could not set ANY leverage for {symbol}; aborting")
                return 0
            finally:
                self.switch_to_spot()

    # ── cancel_all_orders override — handles conditional stop orders ──

    def cancel_all_orders(self, symbol: str, market_type: str = "spot"):
        """Cancel ALL orders (regular + conditional/stop) for a symbol.

        Why an override:
          The base `cancel_all_orders` uses ccxt's fetch_open_orders with
          `params={"stop": True}` for the algo path. On Bybit V5 unified
          this does NOT return conditional stop orders — they live in a
          separate ledger queried by `orderFilter='StopOrder'`. Result:
          before this override, a position close left its SL/TP attached
          stop orders ORPHANED on Bybit. After 5 days the per-symbol limit
          (~10 stop orders) was hit, causing retCode=110009 on every new
          entry's SL placement → fail-closed cascade → fee bleed.

          Live incident at 2026-05-02 08:03 UTC: 24 orphans accumulated
          (8 on DOGEUSDT alone) → DOGE entry failed SL placement → bot
          fail-closed → ghost-reconciler re-imported orphan → loop.
          See scripts/cleanup_orphan_stop_orders.py for the manual repair.

        Implementation:
          1. Call base.cancel_all_orders for regular orders (limit/market
             that haven't filled yet).
          2. For futures (linear), additionally query Bybit's StopOrder
             filter and cancel each conditional order found for the symbol.
        """
        # First cancel regular orders via base
        try:
            super().cancel_all_orders(symbol, market_type)
        except Exception as e:
            logger.debug(f"[Bybit] base cancel_all_orders {symbol}: {e}")

        # Then handle conditional stop orders (linear futures only)
        if market_type != "futures":
            return
        try:
            native = symbol.replace("/", "").replace(":USDT", "")
            if not native.endswith("USDT"):
                native = native + "USDT"
            resp = self.exchange.privateGetV5OrderRealtime({
                "category": "linear",
                "orderFilter": "StopOrder",
                "symbol": native,
            })
            stops = resp.get("result", {}).get("list", [])
            cancelled = 0
            for o in stops:
                oid = o.get("orderId")
                if not oid:
                    continue
                try:
                    self.exchange.privatePostV5OrderCancel({
                        "category": "linear",
                        "symbol": native,
                        "orderId": oid,
                    })
                    cancelled += 1
                except Exception as ce:
                    logger.debug(f"[Bybit] cancel stop {oid[:8]}: {str(ce)[:80]}")
            if cancelled > 0:
                logger.info(
                    f"[Bybit] Cancelled {cancelled} conditional stop order(s) "
                    f"for {symbol}")
        except Exception as e:
            logger.debug(f"[Bybit] stop-order cleanup {symbol}: {str(e)[:120]}")

    # ── get_min_order_size ────────────────────────────────────────────

    def fetch_positions(self, symbols: list = None) -> list:
        """Bybit V5 requires category=linear AND either symbol or settleCoin.

        When `symbols` is empty/None we default settleCoin=USDT to enumerate
        every USDT-margined perpetual position. Without that parameter the
        API returns retCode=10001 "Missing some parameters ... symbol or
        settleCoin".
        """
        if not self._ok():
            return []
        from exchanges.base import _is_timestamp_error
        params = {"category": "linear"}
        if not symbols:
            params["settleCoin"] = "USDT"
        for attempt in range(2):
            try:
                return self.exchange.fetch_positions(symbols, params=params) or []
            except Exception as e:
                if _is_timestamp_error(e) and attempt == 0:
                    logger.warning("[Bybit] Timestamp error on positions — re-syncing")
                    self._sync_time()
                    continue
                # 2026-04-16: Raised debug->warning so silent position fetch
                # failures (e.g. auth revoked, IP not whitelisted) surface.
                logger.warning(f"[Bybit] fetch_positions failed: {str(e)[:150]}")
                return []
        return []

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    def fetch_closed_pnl(self, since_ms: int = None,
                          symbol: str = None) -> list:
        """Bybit V5 closed-PnL endpoint for USDT-margined linear perps."""
        if not self._ok():
            return []
        try:
            params = {"category": "linear", "limit": 100}
            if since_ms:
                params["startTime"] = int(since_ms)
            if symbol:
                try:
                    params["symbol"] = self.exchange.market_id(symbol)
                except Exception:
                    params["symbol"] = symbol.replace("/", "").split(":")[0]
            response = self.exchange.privateGetV5PositionClosedPnl(params) or {}
            records = ((response.get("result") or {}).get("list") or [])
            out = []
            for r in records:
                try:
                    pnl = float(r.get("closedPnl", 0) or 0)
                    if pnl == 0:
                        continue
                    raw_sym = str(r.get("symbol", "") or "")
                    if raw_sym.endswith("USDT"):
                        unified = f"{raw_sym[:-4]}/USDT:USDT"
                    else:
                        unified = raw_sym
                    # Bybit's "side" on closed-PnL is the CLOSING order side.
                    # A closing "Buy" means the position was SHORT; "Sell" → LONG.
                    close_side = (r.get("side", "") or "").lower()
                    orig_side  = "sell" if close_side == "buy" else "buy"
                    out.append({
                        "exchange":     "bybit",
                        "symbol":       unified,
                        "side":         orig_side,
                        "realized_pnl": pnl,
                        "close_time":   int(r.get("updatedTime", 0) or 0) / 1000.0,
                        "trade_id":     str(r.get("orderId", "") or ""),
                        "entry_price":  float(r.get("avgEntryPrice", 0) or 0),
                        "exit_price":   float(r.get("avgExitPrice", 0) or 0),
                        "size":         float(r.get("qty", 0) or 0),
                        "leverage":     int(float(r.get("leverage", 1) or 1)),
                    })
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning(f"[Bybit] fetch_closed_pnl: {str(e)[:150]}")
            return []

    @property
    def name(self) -> str:
        return "Bybit"
