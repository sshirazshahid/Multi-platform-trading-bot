"""
exchanges/binance_client.py — Binance connector (Spot + USD-M Futures).

Pakistan note: Set BINANCE_TESTNET=false in .env — testnet is geo-blocked.
DRY_RUN=true still prevents real trades on the live endpoint.
"""

import threading

import ccxt
from loguru import logger

from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_TESTNET

from .base import BaseExchange

_PLACEHOLDERS = {"", "your_binance_api_key_here", "your_binance_secret_key_here"}


class BinanceClient(BaseExchange):

    # Geo-block circuit breaker — once 451 is detected, stop all API calls
    _geo_blocked = False

    # Default to ONE-WAY mode — per-instance to avoid multi-profile contamination
    _is_oneway = True  # class default; overridden per-instance in __init__

    def __init__(self, api_key: str = None, secret: str = None,
                 testnet: bool = None):
        self._connected = False
        self._is_oneway = True  # Instance variable — safe for multi-profile
        # 2026-05-24 — Reentrant lock guarding self.exchange.options["defaultType"].
        # The bot has multiple concurrent threads hitting this client:
        # _sltp_monitor_loop (10s daemon), schedule.run_pending portfolio
        # cycle (5min), and a ThreadPoolExecutor(max_workers=8) for SL/TP
        # checks. Without serialization, thread A can set "future" then
        # thread B sets "spot" before A's ccxt call fires → A's call goes
        # to spot, returning wrong prices/balances/order routing. RLock
        # is reentrant so any nested switch-using call within the same
        # thread will not self-deadlock.
        self._defaultType_lock = threading.RLock()
        super().__init__(
            api_key = (api_key or BINANCE_API_KEY  or "").strip(),
            secret  = (secret  or BINANCE_SECRET_KEY or "").strip(),
            testnet = testnet if testnet is not None else BINANCE_TESTNET,
        )

    def _init_exchange(self):
        if self.api_key.lower() in _PLACEHOLDERS or \
           self.secret.lower() in _PLACEHOLDERS:
            logger.info("[Binance] No API keys configured — Binance skipped.")
            self.exchange   = None
            self._connected = False
            return

        options = {
            "defaultType":             "spot",
            "adjustForTimeDifference": True,   # auto-sync clock
            "recvWindow":              60000,  # 60s — tolerates heavy clock drift
        }

        if self.testnet:
            logger.warning(
                "[Binance] TESTNET=true — geo-blocked in Pakistan (error 451). "
                "Set BINANCE_TESTNET=false in .env. "
                "DRY_RUN=true prevents real trades on the live endpoint."
            )
            options["testnet"] = True

        self.exchange = ccxt.binance({
            "apiKey":          self.api_key,
            "secret":          self.secret,
            "timeout":         30000,   # 30s hard timeout — prevents scheduler hangs
            "options":         options,
            "enableRateLimit": True,
        })

        if self.testnet:
            self.exchange.set_sandbox_mode(True)

        try:
            self.exchange.load_markets()
        except Exception as e:
            if self._check_geo_block(e):
                self._connected = False
                return
            self._connected = False
            # 2026-08-22: log the exception TYPE, not just str(e). ccxt renders
            # several distinct failures as a bare "binance GET <url>" with no
            # body -- RequestTimeout, ExchangeNotAvailable and NetworkError are
            # indistinguishable from the message alone. That cost a whole
            # diagnosis pass: measured in isolation this exact call succeeds
            # 8/8 in 6-8s, while in-process it failed 89 times today, 123 on
            # 08-21 and 0 on 08-19 -- and the in-bot failures return in ~1s,
            # which is neither a success nor the 30s timeout. The exception
            # type is the one field that separates those hypotheses, and it was
            # being thrown away.
            logger.error(
                f"[Binance] load_markets failed: {type(e).__name__}: {e} "
                f"| repr={repr(e)[:300]}"
            )
            return

        # ── Fix "Timestamp outside recvWindow" errors ──────────────────
        # Synchronises our local clock offset with Binance's server time.
        # This is the proper fix for -1021 timestamp errors caused by
        # Windows clock drift (common on OneDrive / sleep/wake cycles).
        try:
            self.exchange.load_time_difference()
            logger.debug(
                f"[Binance] Clock synced. "
                f"Offset: {self.exchange.options.get('timeDifference', 0)}ms"
            )
        except Exception as e:
            logger.debug(f"[Binance] Clock sync skipped: {e}")

        self._connected = True
        logger.info(f"[Binance] Connected | testnet={self.testnet}")

    def _ok(self) -> bool:
        return self._connected and self.exchange is not None and not BinanceClient._geo_blocked

    @staticmethod
    def _check_geo_block(e: Exception) -> bool:
        """Detect Binance 451 geo-block and disable all calls for the session."""
        err = str(e)
        if "451" in err and "restricted location" in err:
            if not BinanceClient._geo_blocked:
                BinanceClient._geo_blocked = True
                logger.error(
                    "[Binance] GEO-BLOCKED (HTTP 451) — Binance is unavailable "
                    "from this location. All Binance calls disabled for this session. "
                    "Use a VPN or remove Binance from config to silence this.")
            return True
        return False

    def _resync_clock(self):
        """Re-sync clock with Binance — called automatically on timestamp errors."""
        try:
            self.exchange.load_time_difference()
            logger.info("[Binance] Clock re-synced after timestamp error.")
        except Exception:
            pass

    def switch_to_futures(self):
        if self._ok():
            self.exchange.options["defaultType"] = "future"

    def switch_to_spot(self):
        if self._ok():
            self.exchange.options["defaultType"] = "spot"

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
                if "-1021" in str(e):
                    self._resync_clock()
                    try:
                        result = super().fetch_ohlcv(symbol, timeframe, limit, market_type)
                    except Exception:
                        result = []
                else:
                    result = []
            finally:
                self.switch_to_spot()
        return result

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
                if "-1021" in str(e):
                    self._resync_clock()
                    try:
                        result = super().fetch_ticker(symbol, market_type)
                    except Exception:
                        result = {}
                else:
                    result = {}
            finally:
                self.switch_to_spot()
        return result

    def fetch_mark_index(self, symbol: str, market_type: str = "futures") -> dict:
        if not self._ok():
            return {"mark": None, "index": None, "ts": None}
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            else:
                self.switch_to_spot()
            try:
                return super().fetch_mark_index(symbol, market_type)
            finally:
                self.switch_to_spot()

    def fetch_balance(self, market_type: str = "spot") -> dict:
        if not self._ok():
            return {}
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            try:
                bal = super().fetch_balance(market_type)
            except Exception as e:
                if "-1021" in str(e):
                    self._resync_clock()
                    try:
                        bal = super().fetch_balance(market_type)
                    except Exception:
                        bal = {}
                else:
                    bal = {}
            finally:
                self.switch_to_spot()
        return bal

    def fetch_order_book(self, symbol: str, limit: int = 20,
                         market_type: str = "spot") -> dict:
        if not self._ok():
            return {"bids": [], "asks": []}
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            try:
                return super().fetch_order_book(symbol, limit, market_type)
            finally:
                self.switch_to_spot()

    def fetch_open_orders(self, symbol: str,
                          market_type: str = "spot") -> list:
        if not self._ok():
            return []
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()
            try:
                return super().fetch_open_orders(symbol, market_type)
            finally:
                self.switch_to_spot()

    def create_order(self, symbol: str, order_type: str, side: str,
                     amount: float, price: float = None,
                     params: dict = None, market_type: str = "spot"):
        if not self._ok():
            logger.warning("[Binance] create_order skipped — not connected.")
            return {}
        with self._defaultType_lock:
            if market_type == "futures":
                self.switch_to_futures()

            # Strip positionSide if Binance is in One-Way mode
            _params = dict(params or {})
            if self._is_oneway and "positionSide" in _params:
                del _params["positionSide"]

            try:
                order = super().create_order(symbol, order_type, side, amount,
                                             price, _params, market_type)
                return order
            except Exception as e:
                err = str(e)
                if ("-4061" in err or "position side does not match" in err) and not self._is_oneway:
                    self._is_oneway = True
                    logger.info("[Binance] ONE-WAY mode detected. Retrying...")
                    clean = {k: v for k, v in _params.items() if k != "positionSide"}
                    try:
                        order = super().create_order(symbol, order_type, side, amount,
                                                     price, clean, market_type)
                        return order
                    except Exception:
                        raise
                raise
            finally:
                self.switch_to_spot()

    def set_leverage(self, symbol: str, leverage: int) -> int:
        """Returns actually-applied leverage; 0 if exchange not ready."""
        if not self._ok():
            return 0
        with self._defaultType_lock:
            self.switch_to_futures()
            try:
                return super().set_leverage(symbol, leverage)
            finally:
                self.switch_to_spot()

    def transfer(self, amount: float, from_account: str = "spot",
                 to_account: str = "futures") -> bool:
        """Transfer USDT between spot and USD-M futures via Universal Transfer API.
        Uses sapi/v1/asset/transfer (MAIN_UMFUTURE / UMFUTURE_MAIN)."""
        if not self._ok():
            return False

        _TYPE_MAP = {
            ("spot", "futures"):    "MAIN_UMFUTURE",
            ("futures", "spot"):    "UMFUTURE_MAIN",
            ("spot", "margin"):     "MAIN_MARGIN",
            ("margin", "spot"):     "MARGIN_MAIN",
        }
        xfer_type = _TYPE_MAP.get((from_account, to_account))
        if not xfer_type:
            logger.warning(f"[Binance] Unknown transfer route: {from_account}→{to_account}")
            return False

        # Method 1: Binance Universal Transfer (sapi)
        try:
            self.exchange.sapiPostAssetTransfer({
                "type": xfer_type, "asset": "USDT", "amount": str(amount),
            })
            logger.info(
                f"[Binance] Transferred {amount:.4f} USDT "
                f"{from_account} → {to_account} (sapi)")
            return True
        except Exception as e1:
            logger.debug(f"[Binance] sapi transfer: {e1}")

        # Method 2: ccxt generic transfer (maps to same endpoint internally)
        _CCXT_ACC = {"spot": "spot", "futures": "future"}
        try:
            self.exchange.transfer(
                "USDT", amount,
                _CCXT_ACC.get(from_account, from_account),
                _CCXT_ACC.get(to_account, to_account))
            logger.info(
                f"[Binance] Transferred {amount:.4f} USDT "
                f"{from_account} → {to_account} (ccxt)")
            return True
        except Exception as e2:
            logger.warning(f"[Binance] Transfer failed: {e2}")
            return False

    def get_min_order_size(self, symbol: str) -> float:
        if not self._ok():
            return 0.0001
        return super().get_min_order_size(symbol)

    def fetch_closed_pnl(self, since_ms: int = None,
                          symbol: str = None) -> list:
        """Binance Futures realized-PnL from the income ledger.
        Endpoint: /fapi/v1/income?incomeType=REALIZED_PNL.
        """
        if not self._ok():
            return []
        try:
            params = {"incomeType": "REALIZED_PNL", "limit": 1000}
            if since_ms:
                params["startTime"] = int(since_ms)
            if symbol:
                try:
                    params["symbol"] = self.exchange.market_id(symbol)
                except Exception:
                    params["symbol"] = symbol.replace("/", "").split(":")[0]
            records = self.exchange.fapiPrivateGetIncome(params) or []
            out = []
            for r in records:
                try:
                    pnl = float(r.get("income", 0) or 0)
                    if pnl == 0:
                        continue
                    raw_sym = str(r.get("symbol", "") or "")
                    if raw_sym.endswith("USDT"):
                        unified = f"{raw_sym[:-4]}/USDT:USDT"
                    else:
                        unified = raw_sym
                    out.append({
                        "exchange":     "binance",
                        "symbol":       unified,
                        "side":         None,  # income ledger doesn't carry side
                        "realized_pnl": pnl,
                        "close_time":   int(r.get("time", 0) or 0) / 1000.0,
                        "trade_id":     str(r.get("tradeId", "") or r.get("tranId", "")),
                        "entry_price":  0.0,
                        "exit_price":   0.0,
                        "size":         0.0,
                        "leverage":     1,
                    })
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning(f"[Binance] fetch_closed_pnl: {str(e)[:150]}")
            return []

    @property
    def name(self) -> str:
        return "Binance"
