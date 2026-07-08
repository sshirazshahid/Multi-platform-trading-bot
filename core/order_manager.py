"""
core/order_manager.py — Order lifecycle with:
  - Fee-aware PnL (entry + exit fees deducted from net profit)
  - Virtual $1000 paper wallet in DRY RUN mode
  - Auto spot↔futures transfer in LIVE mode
  - Fallback to spot when futures permission denied
  - Blacklist / trailing stop / compliance / risk integrations
"""

import json
import threading
import time
import uuid
from pathlib import Path

from loguru import logger

from config import DRY_RUN, RISK
from core.blacklist_manager import BlacklistManager
from core.compliance_logger import ComplianceLogger
from core.kelly_sizer import KellySizer
from core.position_tracker import Position, PositionTracker
from core.post_mortem import PostMortem
from core.risk_manager import RiskManager, load_age_cutoffs
from core.sim_execution import SimExecutionModel
from core.smart_executor import SmartExecutor
from core.trailing_stop_manager import TrailingStopManager
from core.virtual_wallet import VirtualWallet
from exchanges.base import BaseExchange
from utils.notifier import TelegramNotifier

# Permission-denied error patterns
_PERM_ERRORS = (
    "-2015", "permissions for action", "permission denied",
    "not allowed", "api-key", "IP restriction",
    "unauthorized", "forbidden",
)

# Position mode error — means exchange is in One-Way Mode, not Hedge Mode
_POSITION_MODE_ERRORS = (
    "-4061", "position side does not match",
    "positionSide", "position mode",
    "40774", "unilateral position",
    "unilateral",
    "40773", "two-way positions",   # Bitget: tradeSide close is two-way only
)

# Errors that mean "skip THIS pair, but other pairs are fine"
_SKIP_PAIR_ERRORS = (
    "-4411", "TradFi-Perps agreement",
    "agreement contract", "sign agreement",
    "not supported", "symbol not found",
    "does not have market symbol",
)


def _is_permission_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s.lower() in msg for s in _PERM_ERRORS)


def _should_fire_partial_tp(position, price: float, partial_tp_config: dict):
    """Pure decision function: should the partial-TP fire on this position at this price?

    Returns:
        (should_fire: bool, take_size: float, partial_level: float)

    Inputs:
        position: object with .side ('buy'/'sell'), .entry_price, .take_profit, .partial_taken
        price: current monitor-cycle price for the position
        partial_tp_config: the config.PARTIAL_TP dict

    Extracted from the inline check_sl_tp logic on 2026-05-19 to enable direct
    unit testing. Behavior is byte-equivalent to the original inline block.
    """
    if not partial_tp_config.get("enabled"):
        return (False, 0.0, 0.0)
    if getattr(position, "partial_taken", False):
        return (False, 0.0, 0.0)
    if not (position.take_profit and position.entry_price):
        return (False, 0.0, 0.0)

    take_at = partial_tp_config.get("first_take_at_pct", 0.5)
    take_sz = partial_tp_config.get("first_take_size", 0.5)

    if position.side == "buy":
        tp_dist = position.take_profit - position.entry_price
        partial_level = position.entry_price + tp_dist * take_at
        return (price >= partial_level, take_sz, partial_level)
    else:
        tp_dist = position.entry_price - position.take_profit
        partial_level = position.entry_price - tp_dist * take_at
        return (price <= partial_level, take_sz, partial_level)


def _mid_from_ticker(ticker: dict) -> float:
    """Extract mid price (bid+ask)/2 from a ccxt ticker dict.

    Returns 0.0 when bid or ask are missing. Attribution.record skips rows
    where either mid is 0, so an unparseable ticker degrades cleanly.
    """
    try:
        bid = float(ticker.get("bid") or 0)
        ask = float(ticker.get("ask") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
    except (TypeError, ValueError):
        pass
    return 0.0


def _is_position_mode_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _POSITION_MODE_ERRORS)


def _is_skip_pair_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _SKIP_PAIR_ERRORS)


def build_sl_tp_order_params(
    ex_name: str, side: str, oneway: bool,
    trigger_price: float, is_sl: bool,
    mark_trigger: bool = None,
) -> tuple[str, dict]:
    """Return (order_type, params) for an SL or TP conditional order.

    Separated out so it is trivially unit-testable. Per-exchange shape rules:
      Binance USD-M → STOP_MARKET / TAKE_PROFIT_MARKET + stopPrice + reduceOnly
      Bitget        → ONLY stopLossPrice (SL) or takeProfitPrice (TP); ccxt
                      rejects the combo with triggerPrice
                      ("createOrder() params can only contain one of
                      triggerPrice, stopLossPrice, takeProfitPrice,
                      trailingPercent")
      Bybit v5      → triggerPrice + stopLossPrice/takeProfitPrice +
                      explicit triggerDirection (ccxt only auto-infers
                      triggerDirection in the stopLossPrice-only branch)

    ``mark_trigger`` (C3, tpbot retrofit 2026-07-08): trigger on MARK price
    instead of the venue's last-price default — immune to single rogue
    last-price prints and synced with the liquidation engine. None reads
    config.SLTP_TRIGGER_MARK_PRICE (default ON). Per-venue params verified
    against ccxt 4.5.54 request builders: Binance workingType passthrough,
    Bybit request['triggerBy'/'slTriggerBy'/'tpTriggerBy'], Bitget
    request['triggerType'] (whose ccxt default is already mark_price — the
    explicit param pins it against ccxt upgrades). All three survive the
    client one-way retries, which strip only positionSide/holdSide/tradeSide.
    Flag OFF is byte-identical to the pre-C3 shapes.
    """
    if mark_trigger is None:
        import config as _cfg
        mark_trigger = bool(getattr(_cfg, "SLTP_TRIGGER_MARK_PRICE", True))
    ex_lower = ex_name.lower()

    if ex_lower == "binance":
        order_type = "STOP_MARKET" if is_sl else "TAKE_PROFIT_MARKET"
        params = {"stopPrice": trigger_price, "reduceOnly": True}
        if mark_trigger:
            params["workingType"] = "MARK_PRICE"
        if not oneway:
            params["positionSide"] = "LONG" if side == "buy" else "SHORT"
        return order_type, params

    if ex_lower == "bitget":
        params = {"stopLossPrice": trigger_price} if is_sl \
            else {"takeProfitPrice": trigger_price}
        if mark_trigger:
            params["triggerType"] = "mark_price"
        if oneway:
            params["reduceOnly"] = True
        else:
            params["positionSide"] = "LONG" if side == "buy" else "SHORT"
            params["reduceOnly"] = True
        return "market", params

    # Bybit v5 (default for everything else)
    params = {"triggerPrice": trigger_price}
    if is_sl:
        params["stopLossPrice"] = trigger_price
        params["triggerDirection"] = "below" if side == "buy" else "above"
        if mark_trigger:
            params["triggerBy"] = "MarkPrice"
            params["slTriggerBy"] = "MarkPrice"
    else:
        params["takeProfitPrice"] = trigger_price
        params["triggerDirection"] = "above" if side == "buy" else "below"
        if mark_trigger:
            params["triggerBy"] = "MarkPrice"
            params["tpTriggerBy"] = "MarkPrice"
    if oneway:
        params["reduceOnly"] = True
    else:
        params["positionSide"] = "LONG" if side == "buy" else "SHORT"
        params["reduceOnly"] = True
    return "market", params


class OrderManager:

    def __init__(self, tracker: PositionTracker, risk: RiskManager,
                 notifier: TelegramNotifier):
        self.tracker    = tracker
        self.risk       = risk
        self.notifier   = notifier
        self.dry_run    = DRY_RUN
        self.trailing   = TrailingStopManager()
        self.blacklist  = BlacklistManager()
        self.compliance = ComplianceLogger(dry_run=DRY_RUN)
        self.wallet     = VirtualWallet()
        self.post_mortem = PostMortem()
        self.kelly       = KellySizer()
        self.executor    = SmartExecutor()
        self.sim         = SimExecutionModel()  # DRY_RUN realism
        self.mcp_brain   = None  # Set by bot_engine after construction
        # Provenance (2026-06-12): last open_position rejection reason —
        # read by bot_engine when open_position returns None so the
        # rejection row carries the order-manager-level cause.
        self.last_open_reject: str | None = None
        # Exchange registry for paths (like _finalize_close) that don't
        # receive an exchange parameter. BotEngine populates this via
        # set_exchanges() after constructing its exchange dict.
        self._exchanges: dict = {}
        # Track exchanges where futures is disabled (don't retry)
        # Track exchanges using One-Way Mode (no positionSide param)
        # Bitget is ALWAYS one-way — pre-populate to avoid first-order 40774 error
        self._order_mode_path = Path("data/order_mode_state.json")
        saved = self._load_order_mode_state()
        self._futures_disabled = saved.get("futures_disabled", set())
        self._oneway_mode = saved.get("oneway_mode", {"bitget"})
        # Track positions where SL was already widened once (prevent infinite widening)
        self._sl_widened_path = Path("data/sl_widened.json")
        self._sl_widened = self._load_sl_widened()

        # Idempotency: track recently placed client order IDs to avoid duplicates
        self._recent_client_ids: set = set()
        self._recent_client_ids_max = 500

        # DRY_RUN funding accrual: last UTC hour we charged funding on.
        # Real exchanges settle funding at 00/08/16 UTC; we settle when the
        # current UTC hour first matches one of those after the last tick.
        self._last_funding_hour = -1

        # Close failure counter: {position_id: fail_count}
        # After 3 failures, force-close in tracker to break infinite loops
        self._close_fail_path = Path("data/close_fail_count.json")
        self._close_fail_count: dict = self._load_close_fail_count()

        # B7-P2 (audit 2026-06-21) — per-position close/SL-mutation lock.
        # One RLock per position id (RLock is MANDATORY: _replace_exchange_sl ->
        # _place_exchange_sl_tp fail-closed re-enters close_position on the SAME
        # thread; a plain Lock would self-deadlock the SL monitor). Default-OFF
        # (PER_POSITION_LOCK_ENABLED) — LIVE-only value, fully inert in PAPER.
        from config import PER_POSITION_LOCK_ENABLED as _ppl
        self._per_pos_lock_enabled = bool(_ppl)
        self._pos_locks: dict = {}
        self._pos_locks_guard = threading.Lock()
        # Acquire timeout (< the 10s SL/TP monitor cadence) so a stuck lock can
        # never freeze/stack the monitor; on timeout we proceed unserialized.
        self._pos_lock_timeout = 5.0

        if DRY_RUN:
            logger.warning(
                "[Orders] DRY RUN MODE — no real orders placed. "
                "Paper wallet starts at $"
                + str(int(self.wallet._start))
                + " USDT per exchange."
            )
            logger.info(self.wallet.statement())
        else:
            logger.warning("[Orders] LIVE MODE — real orders will be placed.")

    def set_exchanges(self, exchanges: dict) -> None:
        """Wire the BotEngine exchange registry. Used by _finalize_close
        to look up the exchange for funding-history fetches without having
        an exchange object passed in."""
        self._exchanges = dict(exchanges or {})

    def _exchange_for(self, name: str):
        """Return the exchange client for `name` (case-insensitive), or None."""
        if not name or not self._exchanges:
            return None
        key = str(name).lower()
        for k, v in self._exchanges.items():
            if str(k).lower() == key:
                return v
        return None

    # ── SL widened persistence ─────────────────────────────────────────

    def _load_order_mode_state(self) -> dict:
        """Load persisted _futures_disabled and _oneway_mode sets."""
        try:
            if self._order_mode_path.exists():
                data = json.loads(self._order_mode_path.read_text(encoding="utf-8"))
                return {
                    "futures_disabled": set(data.get("futures_disabled", [])),
                    "oneway_mode": set(data.get("oneway_mode", ["bitget"])),
                }
        except Exception:
            pass
        return {"futures_disabled": set(), "oneway_mode": {"bitget"}}

    def _save_order_mode_state(self):
        """Persist _futures_disabled and _oneway_mode so they survive restarts."""
        try:
            self._order_mode_path.parent.mkdir(parents=True, exist_ok=True)
            self._order_mode_path.write_text(json.dumps({
                "futures_disabled": list(self._futures_disabled),
                "oneway_mode": list(self._oneway_mode),
            }), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save order mode state: {e}")

    def _load_sl_widened(self) -> set:
        try:
            if self._sl_widened_path.exists():
                data = json.loads(self._sl_widened_path.read_text(encoding="utf-8"))
                return set(data)
        except Exception:
            pass
        return set()

    def _save_sl_widened(self):
        try:
            self._sl_widened_path.parent.mkdir(parents=True, exist_ok=True)
            self._sl_widened_path.write_text(
                json.dumps(list(self._sl_widened)), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save sl_widened: {e}")

    def cleanup_sl_widened(self):
        """Remove entries for positions that are no longer open."""
        open_ids = {p.id for p in self.tracker.get_open()}
        stale = self._sl_widened - open_ids
        if stale:
            self._sl_widened -= stale
            self._save_sl_widened()
            logger.debug(f"[Orders] Cleaned {len(stale)} stale sl_widened entries")

    # ── Close-fail counter persistence (2026-04-16) ──────────────────

    def _load_close_fail_count(self) -> dict:
        try:
            if self._close_fail_path.exists():
                data = json.loads(
                    self._close_fail_path.read_text(encoding="utf-8"))
                # Only keep entries for positions that still exist
                open_ids = {p.id for p in self.tracker.get_open()}
                restored = {k: v for k, v in data.items() if k in open_ids}
                if restored:
                    logger.info(
                        f"[Orders] Restored close_fail_count for "
                        f"{len(restored)} position(s)")
                return restored
        except Exception:
            pass
        return {}

    def _save_close_fail_count(self):
        try:
            self._close_fail_path.parent.mkdir(parents=True, exist_ok=True)
            self._close_fail_path.write_text(
                json.dumps(self._close_fail_count), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save close_fail_count: {e}")

    # ── Execution Safety: idempotency, price bands, post-order verify ──

    def _generate_client_order_id(self, exchange_name: str, symbol: str,
                                   side: str) -> str:
        """Generate a unique client order ID for idempotency.
        Sent to exchange as clientOrderId/clOrdID to prevent duplicates."""
        import hashlib
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:20]
        raw = f"{exchange_name}:{symbol}:{side}:{ts}:{uuid.uuid4().hex[:6]}"
        # Most exchanges accept 32-36 char alphanumeric IDs
        cid = "TB" + hashlib.md5(raw.encode()).hexdigest()[:30]
        # Track to prevent same-second duplicates
        if len(self._recent_client_ids) >= self._recent_client_ids_max:
            # Evict oldest half
            to_remove = list(self._recent_client_ids)[:self._recent_client_ids_max // 2]
            for r in to_remove:
                self._recent_client_ids.discard(r)
        self._recent_client_ids.add(cid)
        return cid

    def _fill_freshness_reason(self, ticker: dict, exchange) -> str | None:
        """Phase C: reason a fill should be rejected for a stale ticker, else None.

        Reuses ``core.feed_health.stale_fill_reason``. Fail-OPEN on a MISSING
        timestamp (mocked tickers / thin venues) so it never blocks a fill it
        cannot prove is stale — only a present, definitely-old timestamp rejects.
        """
        try:
            max_age = float(getattr(__import__("config"), "FILL_FRESHNESS_MAX_AGE_SEC", 300.0))
        except Exception:
            max_age = 300.0
        if max_age <= 0:
            return None
        ts_ms = (ticker or {}).get("timestamp")
        if not ts_ms:
            return None  # missing timestamp -> allow
        try:
            mark_ts = float(ts_ms) / 1000.0
        except (TypeError, ValueError):
            return None
        from core.feed_health import FeedFreshness, stale_fill_reason
        venue = str(getattr(exchange, "name", "") or "?")
        snap = FeedFreshness(venue=venue, mark_ts=mark_ts)
        import time as _t
        return stale_fill_reason(snap, _t.time(), max_age_sec=max_age, require=("mark",))

    def _check_price_band(self, symbol: str, side: str,
                          fill_price: float, exchange,
                          market_type: str) -> bool:
        """Validate that fill price is within ±5% of current market price.
        Returns True if price is within band, False if suspicious."""
        try:
            ticker = exchange.fetch_ticker(symbol, market_type)
            market_price = float(ticker.get("last") or ticker.get("close") or 0)
            if market_price <= 0:
                return True  # can't validate, allow
            deviation = abs(fill_price - market_price) / market_price
            if deviation > 0.05:  # >5% deviation
                logger.error(
                    f"[Orders] PRICE BAND REJECT: {symbol} {side.upper()} "
                    f"fill={fill_price:.6f} vs market={market_price:.6f} "
                    f"({deviation*100:.1f}% deviation > 5% limit)")
                return False
            return True
        except Exception:
            return True  # can't validate, allow

    def _verify_order_on_exchange(self, exchange, order_id: str,
                                  symbol: str,
                                  market_type: str = "spot") -> dict | None:
        """Immediately verify an order exists on exchange after placement.
        Returns order status dict or None if verification fails."""
        if not order_id:
            return None
        import time as _t
        for attempt in range(2):
            try:
                params = {}
                if market_type == "futures":
                    try:
                        fn = getattr(exchange, "_futures_params", None)
                        maybe = fn() if callable(fn) else {}
                        params = maybe if isinstance(maybe, dict) else {}
                    except Exception:
                        params = {}
                if params:
                    status = exchange.exchange.fetch_order(order_id, symbol, params)
                else:
                    status = exchange.exchange.fetch_order(order_id, symbol)
                if status and status.get("id"):
                    logger.debug(
                        f"[Orders] Order {order_id} verified: "
                        f"status={status.get('status')}, "
                        f"filled={status.get('filled')}")
                    return status
            except Exception as e:
                if attempt == 0:
                    _t.sleep(0.5)  # brief wait for order to propagate
                else:
                    # Area 2 (2026-05-20): demote Bybit's "fetchOrder() can only access an order
                    # within last 20 mins" warning to DEBUG — it's a Bybit API limitation, not
                    # a bot issue. Other unexpected errors still surface at WARNING.
                    _err_text = str(e).lower()
                    if "can only access an order" in _err_text:
                        logger.debug(
                            f"[Orders] Order verification skipped (Bybit 20-min limit) "
                            f"for {order_id}: {str(e)[:120]}")
                    else:
                        logger.warning(
                            f"[Orders] Order verification failed for {order_id}: {e}")
        return None

    # ── Balance helpers ───────────────────────────────────────────────

    def available_balance(self, exchange: BaseExchange,
                          market_type: str = "spot") -> float:
        """
        In DRY RUN: returns virtual wallet balance.
        In LIVE:    fetches real balance for spot or futures.
        """
        if self.dry_run:
            return self.wallet.balance(exchange.name)
        try:
            bal = exchange.fetch_balance(market_type)
            return self._extract_usdt(bal, exchange.name)
        except Exception as e:
            logger.debug(f"[Orders] balance fetch {market_type}: {e}")
        return 0.0

    def _extract_usdt(self, bal: dict, exchange_name: str = "") -> float:
        if not bal:
            return 0.0
        ex = exchange_name.lower() if exchange_name else ""

        # ── Bybit unified account: totalEquity in raw API response ──
        if ex == "bybit":
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [])
                if lst and isinstance(lst, list):
                    acct = lst[0] if lst else {}
                    for field in ("totalAvailableBalance", "totalWalletBalance", "totalEquity"):
                        val = acct.get(field)
                        if val is not None:
                            try:
                                v = float(val)
                                if v > 0:
                                    return v
                            except (TypeError, ValueError):
                                pass
                    # Check per-coin entries for USDT
                    coins = acct.get("coin", [])
                    if isinstance(coins, list):
                        for c in coins:
                            if c.get("coin") == "USDT":
                                for f2 in ("equity", "walletBalance"):
                                    val2 = c.get(f2)
                                    if val2 is not None:
                                        try:
                                            v2 = float(val2)
                                            if v2 > 0:
                                                return v2
                                        except (TypeError, ValueError):
                                            pass
            except Exception:
                pass

        # ── Standard: USDT.free → USDT.total → free.USDT → total.USDT ──
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("free", "total"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        total = bal.get("total", {})
        if isinstance(total, dict):
            val = total.get("USDT")
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
        free = bal.get("free", {})
        if isinstance(free, dict):
            val = free.get("USDT")
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    pass
        return 0.0

    def auto_transfer_for_trade(self, exchange: BaseExchange,
                                 market_type: str, needed: float) -> bool:
        """
        Auto-transfer USDT between spot and futures when needed.
        If trading futures but balance is in spot → transfer spot→futures.
        If trading spot but balance is in futures → transfer futures→spot.

        Spec §14: No auto-transfers to futures unless futures has positive
        expectancy in paper testing. In PAPER/OBSERVATION mode this is a no-op.
        """
        if self.dry_run:
            return True
        if not hasattr(exchange, 'transfer'):
            return False

        current = self.available_balance(exchange, market_type)
        if current >= needed:
            return True

        # 2026-04-29: Spec §14 expectancy-gate on spot→futures auto-transfer
        # was LIFTED per user directive "Go all in with the available USDT on
        # all exchanges". The original gate (commented below) blocked the
        # transfer unless any strategy in kelly_stats.json showed positive
        # expectancy — currently NONE do because most negative-expectancy
        # entries were driven by pre-fix bot bugs (SL placement failures,
        # ghost-sync closes, BREAKEVEN fail-closed). The user wants full
        # capital deployment now that those bugs are fixed.
        #
        # Restore the gate (paste the block from git history at this commit)
        # if you ever want to re-instate Spec §14:
        #   ks = json.loads(Path('data/kelly_stats.json').read_text())
        #   has_positive = any(...)  # see git blame for the closed form
        #   if not has_positive: return False
        #
        # Futures→spot remains always allowed (de-risking is never blocked).
        other_type = "futures" if market_type == "spot" else "spot"

        other_bal  = self.available_balance(exchange, other_type)

        if other_bal < 5:
            return False

        transfer_amt = min(needed - current + 5, other_bal * 0.8)
        transfer_amt = round(transfer_amt, 2)

        if transfer_amt < 1:
            return False

        logger.info(f"[Orders] Auto-transfer {transfer_amt:.2f} USDT "
                    f"{other_type} → {market_type} on {exchange.name}")
        return exchange.transfer(transfer_amt, other_type, market_type)

    # ── Open position ─────────────────────────────────────────────────

    @staticmethod
    def _interpret_execution_result(order, intended_size):
        """Map a SmartExecutor return into (outcome, fill_size).

        outcome:
          'filled'    -> a position exists; place SL/TP on fill_size.
          'no_fill'   -> nothing opened (skip / empty / missing id); register
                         nothing. Prevents the phantom-position bug where the
                         old check keyed only on order['id'] (a maker SKIP
                         returns the cancelled limit id, looking like a fill).
          'uncertain' -> cancel+verify both failed; do NOT register (the
                         ghost-reconciler adopts any real fill on the next cycle).

        Sizes to the ACTUAL fill whenever the venue/executor reports one.
        This handles partial maker fills, downsized exchange fills, and TWAP
        slices. If no fill/amount is reported, fall back to intended_size.
        """
        if not order:
            return ("no_fill", 0.0)
        status = (order.get("status") or "").lower()
        if status == "skipped_maker_only":
            return ("no_fill", 0.0)
        if status == "uncertain":
            return ("uncertain", 0.0)
        if not order.get("id"):
            return ("no_fill", 0.0)
        filled = float(order.get("filled") or 0)
        amount = float(order.get("amount") or 0)
        actual = filled if filled > 0 else amount
        if status == "partial_maker":
            return ("filled", actual) if actual > 0 else ("no_fill", 0.0)
        return ("filled", actual if actual > 0 else float(intended_size))

    @staticmethod
    def _aggregate_execution_results(results, intended_size):
        """Aggregate TWAP slice results into one order-like dict.

        Returns None for no fill, an ``uncertain`` dict when every slice is
        uncertain/no-fill and at least one is uncertain, or a synthetic filled
        order with total filled quantity and VWAP average.
        """
        if not results:
            return None

        slice_hint = float(intended_size) / max(1, len(results))
        total = 0.0
        notional = 0.0
        ids = []
        fill_types = []
        any_uncertain = False
        first = None

        for order in results:
            if not order:
                continue
            first = first or order
            outcome, fill_sz = OrderManager._interpret_execution_result(
                order, slice_hint)
            if outcome == "uncertain":
                any_uncertain = True
                continue
            if outcome != "filled" or fill_sz <= 0:
                continue
            px = float(order.get("average") or order.get("price") or 0)
            total += fill_sz
            if px > 0:
                notional += fill_sz * px
            if order.get("id"):
                ids.append(str(order.get("id")))
            if order.get("_fill_type"):
                fill_types.append(order.get("_fill_type"))

        if total > 0:
            avg = (notional / total) if notional > 0 else float(
                (first or {}).get("average") or (first or {}).get("price") or 0)
            if "taker" in fill_types:
                fill_type = "taker"
            elif "maker_partial" in fill_types or total < float(intended_size):
                fill_type = "maker_partial"
            elif "maker" in fill_types:
                fill_type = "maker"
            else:
                fill_type = None
            out = {
                "id": ids[0] if ids else f"TWAP-{uuid.uuid4().hex[:8]}",
                "status": "closed",
                "filled": total,
                "amount": total,
                "average": avg,
                "price": avg,
                "_twap_slice_ids": ids,
            }
            if fill_type:
                out["_fill_type"] = fill_type
            if any_uncertain:
                out["_executor_warning"] = "twap_partial_with_uncertain_slice"
            return out

        if any_uncertain:
            return {
                "status": "uncertain",
                "id": (first or {}).get("id"),
                "_executor_warning": "twap_uncertain",
            }
        return None

    def open_position(self, exchange: BaseExchange, symbol: str, side: str,
                      market_type: str, strategy: str, size: float,
                      sl: float, tp: float, leverage: int = 1,
                      order_type: str = "market", price: float = None,
                      candidate_id: int = None, mcp_score: float = None,
                      model_version: str = None,
                      decision_id: str | None = None):

        # Provenance: reset per attempt; every internal reject stashes a
        # reason here before returning None.
        self.last_open_reject = None

        if self.blacklist.is_blacklisted(symbol):
            logger.warning(f"[Orders] {symbol} blacklisted — skipped.")
            self.last_open_reject = "blacklisted"
            return None

        if not self.risk.can_trade(self.tracker.count_open()):
            self.last_open_reject = "risk_can_trade_block"
            return None

        # Kelly criterion: block trade if strategy has negative edge
        # If MCP Brain approved this coin, use lenient Kelly thresholds
        mcp_approved = False
        if self.mcp_brain:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                if mcp_dec.get("action") in ("BUY", "SELL"):
                    mcp_approved = True
            except Exception:
                pass
        blocked, block_reason = self.kelly.should_block_trade(
            strategy, mcp_approved=mcp_approved)
        if blocked:
            logger.warning(f"[Orders] KELLY BLOCK: {block_reason}")
            self.last_open_reject = "kelly_block"
            return None

        if size <= 0:
            logger.warning(f"[Orders] Invalid size {size} for {symbol}")
            self.last_open_reject = "invalid_size"
            return None

        # MINIMUM NOTIONAL gate
        _ep = price
        if not _ep:
            try:
                _t = exchange.fetch_ticker(symbol, market_type)
                _ep = float(_t.get("last") or _t.get("close") or 0)
            except Exception: _ep = 0
        if _ep > 0 and size * _ep < 5.0:
            logger.debug(f"[Orders] {symbol}: ${size * _ep:.2f} < $5 min — skip")
            self.last_open_reject = "below_min_notional"
            return None

        min_size = exchange.get_min_order_size(symbol)
        if size < min_size:
            # Some exchanges report min in contracts not coins.
            # Check by NOTIONAL VALUE (USD) instead of raw quantity.
            est_price = price or 0
            if not est_price:
                try:
                    t = exchange.fetch_ticker(symbol, market_type)
                    est_price = float(t.get("last") or t.get("close") or 0)
                except Exception:
                    pass
            notional = size * est_price if est_price else 0
            if notional >= 5.0:
                logger.debug(
                    f"[Orders] Size {size:.8f} < exchange min {min_size} "
                    f"but notional ${notional:.2f} OK — proceeding")
            else:
                logger.warning(
                    f"[Orders] Size {size:.8f} (${notional:.2f}) too small "
                    f"for {symbol} — skipping")
                self.last_open_reject = "size_below_exchange_min"
                return None

        # Check if futures is disabled on this exchange (permission error)
        ex_name_lower = exchange.name.lower()
        if market_type == "futures" and ex_name_lower in self._futures_disabled:
            if side == "sell":
                logger.debug(f"[Orders] Futures disabled on {exchange.name}, "
                             f"SHORT not possible on spot — skipping {symbol}")
                self.last_open_reject = "futures_disabled_short"
                return None
            # Fallback to spot for BUY signals
            logger.info(f"[Orders] Futures disabled on {exchange.name}, "
                        f"falling back to SPOT for {symbol}")
            market_type = "spot"
            leverage = 1
            # Convert futures symbol to spot (remove :USDT suffix)
            if ":USDT" in symbol:
                symbol = symbol.replace(":USDT", "")

        if market_type == "futures" and leverage > 1:
            safe_lev = self.risk.validate_leverage(leverage)
            if not self.dry_run:
                applied = exchange.set_leverage(symbol, safe_lev)
                # Defensive: any exchange override that pre-dates the
                # int-return contract returns None. Treat None as "assume
                # the requested leverage was applied" — preserves legacy
                # behavior and avoids the NoneType / int crash.
                if applied is None:
                    applied = safe_lev
                if applied == 0:
                    logger.warning(
                        f"[Orders] {symbol}: leverage setup failed entirely on "
                        f"{exchange.name}; aborting trade")
                    self.last_open_reject = "leverage_setup_failed"
                    return None
                if applied != safe_lev:
                    ratio = applied / safe_lev
                    size = size * ratio
                    logger.info(
                        f"[Orders] {symbol}: leverage clamped {safe_lev}x → "
                        f"{applied}x by exchange; size rescaled ×{ratio:.3f} "
                        f"to preserve margin (notional reduces accordingly)")
                    safe_lev = applied
            leverage = safe_lev

        # LIVE: Auto-transfer if needed
        if not self.dry_run and market_type == "futures":
            notional = size * (price or 0)
            if notional == 0:
                try:
                    t = exchange.fetch_ticker(symbol, market_type)
                    notional = size * float(t.get("last") or t.get("close") or 0)
                except Exception:
                    pass
            margin_needed = notional / max(leverage, 1) + 2  # +2 USDT buffer
            self.auto_transfer_for_trade(exchange, "futures", margin_needed)

        # MCP Brain SL/TP override REMOVED — spec §2 forbids AI from
        # widening stop losses. Deterministic ATR-based SL/TP is authoritative.

        # ── MCP Brain confidence → position size scaling ──
        # High-conviction signals get more capital, marginal ones get less
        _pre_boost_size = size
        if self.mcp_brain:
            try:
                base = symbol.split("/")[0].split(":")[0]
                mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                mcp_conf = mcp_dec.get("confidence", 0)
                layers = mcp_dec.get("layers_aligned", 0)
                if mcp_conf >= 0.85 and layers >= 4:
                    size *= 1.20  # 20% boost for high-conviction (4+ layers agree)
                    logger.info(
                        f"[MCP-SIZE] {symbol}: +20% size (conf={mcp_conf:.0%}, "
                        f"{layers}/5 layers)")
                elif mcp_conf < 0.60 and mcp_conf > 0:
                    size *= 0.80  # 20% reduction for marginal signals
                    logger.info(
                        f"[MCP-SIZE] {symbol}: -20% size (conf={mcp_conf:.0%})")
            except Exception:
                pass

        # ── Post-boost loss clamp — prevent MCP boost from exceeding $MAX_LOSS ──
        # bot_engine._within_loss_clamp() ran BEFORE this function was called,
        # so the +20% boost above can push expected loss from $2.00 to $2.40.
        # Re-check and clamp size back down if needed.
        if size > _pre_boost_size and sl > 0:
            try:
                from config import MAX_LOSS_PER_TRADE_USD
                _est_px = price
                if not _est_px:
                    try:
                        _t = exchange.fetch_ticker(symbol, market_type)
                        _est_px = float(_t.get("last") or _t.get("close") or 0)
                    except Exception:
                        _est_px = 0
                if _est_px > 0:
                    _sl_frac = abs(_est_px - sl) / _est_px
                    _exp_loss = size * _est_px * _sl_frac
                    if _exp_loss > MAX_LOSS_PER_TRADE_USD:
                        _max_sz = MAX_LOSS_PER_TRADE_USD / (_est_px * _sl_frac) if _sl_frac > 0 else size
                        logger.warning(
                            f"[MCP-SIZE] Re-clamped after boost: "
                            f"size {size:.8f} → {_max_sz:.8f} "
                            f"(loss ${_exp_loss:.2f} > ${MAX_LOSS_PER_TRADE_USD})")
                        size = _max_sz
            except (ImportError, Exception):
                pass

        # Get fill price + mid snapshot for attribution.
        # The mid is captured BEFORE applying sim slippage / smart-executor
        # crossing so attribution can decompose fill-vs-mid as `spread`.
        fill_price = price
        entry_mid = 0.0
        if not fill_price or order_type == "market":
            ticker     = exchange.fetch_ticker(symbol, market_type)
            fill_price = ticker.get("last") or ticker.get("close") or price
            entry_mid  = _mid_from_ticker(ticker)
            # Phase C fill-time freshness gate: reject on a DEFINITELY-stale
            # ticker timestamp; a MISSING timestamp is allowed (fail-open).
            _fresh_reason = self._fill_freshness_reason(ticker, exchange)
            if _fresh_reason:
                logger.warning(
                    f"[Orders] {symbol} fill rejected: stale feed ({_fresh_reason})")
                self.last_open_reject = f"stale_feed:{_fresh_reason}"
                return None
        if not fill_price:
            logger.error(f"[Orders] Cannot get price for {symbol}")
            self.last_open_reject = "no_price"
            return None
        fill_price = float(fill_price)

        # ── DRY_RUN realism: apply slippage + spread (2026-04-11) ──
        # In LIVE, SmartExecutor crosses the book and pays real slippage.
        # In DRY_RUN, paper used midpoint-ish ticker.last → systematically
        # better than any real fill. Apply directional spread + slippage so
        # paper pays what LIVE pays. See core/sim_execution.py for rationale.
        if self.dry_run:
            sim_fill = self.sim.paper_fill_price(
                exchange, symbol, side, market_type,
                base_price=fill_price, phase="open", size=size)
            if sim_fill > 0 and sim_fill != fill_price:
                logger.debug(
                    f"[SimExec] {symbol} {side} OPEN slip: "
                    f"{fill_price:.6g} → {sim_fill:.6g}")
                fill_price = sim_fill

        # ── Price Band Sanity Check ──
        if not self._check_price_band(symbol, side, fill_price, exchange, market_type):
            self.last_open_reject = "price_band_failed"
            return None

        # Round quantity to exchange precision (Bitget: 0.1)
        try:
            rounded_size = exchange.round_quantity(symbol, size)
            if rounded_size != size:
                notional_check = rounded_size * fill_price
                if notional_check < 5.0:
                    logger.warning(
                        f"[Orders] {symbol}: rounded qty {rounded_size} "
                        f"(${notional_check:.2f}) too small after rounding")
                    self.last_open_reject = "rounded_size_too_small"
                    return None
                logger.debug(
                    f"[Orders] {symbol}: qty {size:.8f} -> {rounded_size:.8f} "
                    f"(exchange precision)")
                size = rounded_size
        except Exception:
            pass

        # Build Position first so __post_init__ calculates entry_fee
        order_id = f"DRY-{uuid.uuid4().hex[:8]}" if self.dry_run else None
        # Phase 18 (2026-05-04): persist mcp_score / 100 as `confidence`
        # so calibrator.record() at close has data to learn from. Was the
        # second leg of the dead-code pattern (first: Phase 17 adaptive
        # sizing wired into a never-called method; second: calibrator
        # never received predicted_conf because positions.json never
        # stored it).
        _conf_in = mcp_score / 100.0 if mcp_score and mcp_score > 0 else 0.0
        pos = Position(
            id=order_id or uuid.uuid4().hex,
            exchange=exchange.name, symbol=symbol, side=side,
            market_type=market_type, strategy=strategy,
            entry_price=fill_price, size=size,
            stop_loss=sl, take_profit=tp,
            leverage=leverage, order_id=order_id,
            entry_mid=entry_mid,
            confidence=_conf_in,
        )

        # Execution fill type ('maker'|'taker'|'maker_partial'); None in dry-run
        # (no real fill) and tagged by the executor on the live path below.
        _fill_type: str | None = None

        if self.dry_run:
            ok = self.wallet.on_open(
                exchange.name, symbol, side,
                size, fill_price, pos.entry_fee, market_type,
                leverage=leverage,
            )
            if not ok:
                self.last_open_reject = "paper_wallet_reject"
                return None
            logger.info(
                f"[Orders] [DRY] {side.upper()} {size:.6f} {symbol} "
                f"@ {fill_price:.4f} | "
                f"entry_fee={pos.entry_fee:.4f} USDT | "
                f"SL={sl:.4f} TP={tp:.4f} | {strategy} | {market_type}"
            )
        else:
            try:
                params = {}
                if market_type == "futures":
                    # Use Hedge Mode params UNLESS we know this exchange is One-Way
                    if ex_name_lower not in self._oneway_mode:
                        params["positionSide"] = "LONG" if side == "buy" else "SHORT"

                # ── Idempotency: attach client order ID ──
                client_oid = self._generate_client_order_id(
                    exchange.name, symbol, side)
                params["clientOrderId"] = client_oid
                params["newClientOrderId"] = client_oid  # Binance alias

                # ── Smart Execution: spread check + limit order + fallback ──
                spread_info = self.executor.check_spread(
                    exchange, symbol, market_type)
                if not spread_info["ok"]:
                    logger.info(
                        f"[Orders] {symbol}: spread too wide "
                        f"({spread_info['spread_pct']*100:.3f}%) — skip")
                    self.last_open_reject = "spread_too_wide"
                    return None
                # Prefer the order-book mid over the ticker mid for LIVE —
                # check_spread fetches a fresh top-of-book snapshot.
                _book_mid = float(spread_info.get("mid") or 0)
                if _book_mid > 0:
                    pos.entry_mid = _book_mid

                notional_usd = size * fill_price
                if self.executor.should_use_twap(notional_usd):
                    # TWAP for large orders
                    results = self.executor.execute_twap(
                        exchange, symbol, side, size,
                        market_type, params)
                    order = self._aggregate_execution_results(results, size)
                else:
                    # Limit order with market fallback
                    order = self.executor.execute_limit_with_fallback(
                        exchange, symbol, side, size,
                        market_type, params)

                _outcome, _fill_sz = self._interpret_execution_result(order, size)
                if _outcome != "filled":
                    if _outcome == "uncertain":
                        logger.warning(
                            f"[Orders] {symbol}: executor UNCERTAIN (cancel+verify both "
                            f"failed) — NOT registering a position; ghost-reconciler will "
                            f"adopt any real fill next cycle. order={order}")
                    else:
                        logger.info(
                            f"[Orders] {symbol}: no fill "
                            f"({(order or {}).get('status') or 'empty'}) — no position opened")
                    self.last_open_reject = "no_fill"
                    return None
                if 0 < _fill_sz < size:
                    logger.warning(
                        f"[Orders] {symbol}: partial fill {_fill_sz}/{size} — sizing "
                        f"position + SL/TP to the actual fill.")
                    size = _fill_sz
                    pos.size = _fill_sz
                pos.order_id   = order.get("id")
                pos.id         = pos.order_id or pos.id
                _fill_type     = order.get("_fill_type")
                fill_price     = order.get("average") or order.get("price") or fill_price
                pos.entry_price = float(fill_price)
                # Recalculate entry_fee based on actual fill price.
                # 2026-06-11: venue+fill-aware — LIVE maker fills were
                # previously booked at Binance taker rate.
                from core.position_tracker import _fee_rate
                pos.entry_fee = pos.size * pos.entry_price * _fee_rate(
                    pos.market_type, pos.exchange,
                    "maker" if _fill_type in ("maker", "maker_partial")
                    else "taker")
                pos.total_fees = pos.entry_fee + pos.exit_fee

                # ── Post-order verification ──
                verified = self._verify_order_on_exchange(
                    exchange, pos.order_id, symbol, market_type)
                if verified:
                    actual_fill = verified.get("average") or verified.get("price")
                    if actual_fill:
                        pos.entry_price = float(actual_fill)
                        fill_price = pos.entry_price

                logger.info(
                    f"[Orders] LIVE ORDER: {side.upper()} {size:.6f} {symbol} "
                    f"@ {fill_price:.4f} | "
                    f"entry_fee={pos.entry_fee:.4f} USDT | "
                    f"id={pos.id} | cid={client_oid[:12]}.. | {strategy}"
                )
            except Exception as e:
                # Skip pair errors (e.g., Binance TradFi agreement for XAU/XAG)
                if _is_skip_pair_error(e):
                    logger.warning(
                        f"[Orders] {symbol} skipped on {exchange.name}: "
                        f"{str(e)[:100]}")
                    # Auto-blacklist to prevent repeated warnings each cycle
                    self.blacklist.add(symbol, reason=f"skip_pair:{str(e)[:60]}")
                    self.last_open_reject = "skip_pair_error"
                    return None
                # Handle position mode mismatch — retry without positionSide
                if _is_position_mode_error(e) and market_type == "futures":
                    self._oneway_mode.add(ex_name_lower)
                    self._save_order_mode_state()
                    logger.info(
                        f"[Orders] {exchange.name} is in ONE-WAY mode. "
                        f"Retrying without positionSide...")
                    try:
                        order = self.executor.execute_limit_with_fallback(
                            exchange, symbol, side, size,
                            market_type, {},
                        )
                        _o2, _f2 = self._interpret_execution_result(order, size)
                        if _o2 != "filled":
                            logger.info(
                                f"[Orders] {symbol}: one-way retry no fill "
                                f"({(order or {}).get('status') or 'empty'}) — skipping")
                            self.last_open_reject = "no_fill_oneway_retry"
                            return None
                        if 0 < _f2 < size:
                            logger.warning(
                                f"[Orders] {symbol}: one-way partial fill {_f2}/{size} "
                                f"— sizing position + SL/TP to the actual fill.")
                            size = _f2
                            pos.size = _f2
                        pos.order_id    = order.get("id")
                        pos.id          = pos.order_id or pos.id
                        fill_price      = order.get("average") or order.get("price") or fill_price
                        pos.entry_price = float(fill_price)
                        # Recalculate entry_fee based on actual fill price
                        from core.position_tracker import _fee_rate as _fr
                        pos.entry_fee = pos.size * pos.entry_price * _fr(pos.market_type)
                        pos.total_fees = pos.entry_fee + pos.exit_fee
                        logger.info(
                            f"[Orders] LIVE ORDER (one-way): {side.upper()} {size:.6f} {symbol} "
                            f"@ {fill_price:.4f} | id={pos.id} | {strategy}")
                    except Exception as e2:
                        logger.error(f"[Orders] Order failed (one-way retry): {e2}")
                        self.last_open_reject = "order_failed_oneway_retry"
                        return None
                elif _is_permission_error(e) and market_type == "futures":
                    self._futures_disabled.add(ex_name_lower)
                    self._save_order_mode_state()
                    logger.warning(
                        f"[Orders] Futures PERMISSION DENIED on {exchange.name} "
                        f"— falling back to SPOT for buy signals.")
                    if side == "buy":
                        spot_symbol = symbol.replace(":USDT", "")
                        # E-7: forward ALL provenance kwargs — this retry
                        # previously dropped candidate_id/mcp_score/
                        # model_version (pre-existing data loss).
                        return self.open_position(
                            exchange, spot_symbol, "buy", "spot",
                            strategy, size, sl, tp, leverage=1,
                            candidate_id=candidate_id, mcp_score=mcp_score,
                            model_version=model_version,
                            decision_id=decision_id)
                    self.last_open_reject = "futures_permission_denied"
                    return None
                else:
                    logger.error(f"[Orders] Order failed on {exchange.name}: {e}")
                    self.notifier.error(
                        f"Order FAILED: {symbol} {side.upper()}\n{str(e)[:200]}")
                    self.last_open_reject = "order_failed"
                    return None

        self.tracker.add(pos)

        # Tag scalp positions for downstream routing (time wall, stale close,
        # trailing skip). Derived from TP distance vs entry; no caller changes
        # needed. Also stores tp_pct so the time-wall fallback check can match.
        try:
            from config import SCALP_MODE as _SM_tag
            if _SM_tag.get("enabled", False) and fill_price > 0 and tp > 0:
                _tp_dist_pct = abs(tp - fill_price) / fill_price * 100.0
                _scalp_tp_pct = _SM_tag.get("tp_pct", 1.8)
                if abs(_tp_dist_pct - _scalp_tp_pct) < 0.5:
                    pos._scalp = True
                    pos.tp_pct = round(_tp_dist_pct, 3)
        except Exception:
            pass

        # Daily open counter — feeds RiskManager.can_trade()'s per-day cap.
        # Wired here (and not earlier) so failed orders don't burn the
        # day's quota.
        try:
            self.risk.note_trade_opened()
        except Exception as _ne:
            logger.debug(f"[Risk] note_trade_opened skipped: {_ne}")

        # Warehouse trade-open row (spec §4, §6) — MUST run BEFORE
        # _place_exchange_sl_tp so the fail-closed path has a row to patch
        # via record_trade_close → trade_id_by_key. Previously this write
        # lived in bot_engine._execute_open AFTER open_position returned,
        # so every SL-placement failure silently lost its trade row.
        try:
            from config import OPERATING_MODE as _mode
            from core.warehouse import get_warehouse
            get_warehouse().record_trade_open(
                exchange=exchange.name.lower(),
                symbol=symbol,
                side=side,
                ts_entry=float(getattr(pos, "open_time", time.time())),
                entry_px=float(getattr(pos, "entry_price", fill_price)),
                size=float(getattr(pos, "size", size)),
                leverage=int(leverage),
                candidate_id=candidate_id if (candidate_id or 0) > 0 else None,
                market_type=market_type,
                strategy_family=str(strategy) if strategy else "unknown",
                fee=float(getattr(pos, "entry_fee", 0.0)),
                mode=_mode,
                mcp_score=float(mcp_score) if mcp_score is not None else None,
                model_version=str(model_version) if model_version else None,
                fill_type=_fill_type,
                decision_id=str(decision_id) if decision_id else None,
            )
        except Exception as _we:
            # 2026-05-25 — Bug 3 fix: was logger.debug, so a transient
            # SQLite lock / disk error here produced a stuck-OPEN warehouse
            # row with ZERO operator visibility (the close path logs the
            # resulting trade_id MISS at warning, but you couldn't correlate
            # it without debug logging). Promote to warning for symmetry.
            logger.warning(
                f"[Warehouse] record_trade_open FAILED "
                f"(row will be untrackable at close): {_we}")

        # Place SL/TP orders on the exchange for real protection.
        # 2026-04-16 (post-audit): use pos.size (the tracker's canonical size,
        # which reflects any rounding applied at entry), not the pre-round
        # `size` variable. Otherwise SL/TP are placed for the wrong quantity
        # and the exchange rejects them on partial fills.
        if not self.dry_run and market_type == "futures":
            self._place_exchange_sl_tp(exchange, pos, sl, tp, side, symbol, pos.size, market_type)

        self.compliance.log_trade(
            exchange.name, symbol, side, size, fill_price,
            strategy, order_id=pos.id, reason="entry"
        )
        # Gather rich context for email notification
        _bal = 0.0
        _open_n = None
        _exposure = None
        try:
            if self.dry_run:
                _bal = float(self.wallet.total_balance() or 0.0)
            _open_n = self.tracker.count_open()
            _exposure = sum(
                float(p.size) * float(p.entry_price)
                for p in self.tracker.get_open()
            )
        except Exception:
            pass
        _risk_usd = None
        _rr = None
        try:
            if side == "buy":
                sl_pct = (float(fill_price) - float(sl)) / float(fill_price)
                tp_pct = (float(tp) - float(fill_price)) / float(fill_price)
            else:
                sl_pct = (float(sl) - float(fill_price)) / float(fill_price)
                tp_pct = (float(fill_price) - float(tp)) / float(fill_price)
            _risk_usd = float(size) * float(fill_price) * sl_pct
            _rr = (tp_pct / sl_pct) if sl_pct > 0 else None
        except Exception:
            pass
        # Notifier wrapped: any rendering / SMTP failure here must NOT lose
        # the open position. The position is already created + persisted at
        # this point; the notifier is purely informational.
        try:
            self.notifier.trade_opened(
                exchange.name, symbol, side, fill_price,
                size, sl, tp, strategy, market_type,
                leverage=leverage,
                rr_ratio=_rr,
                risk_usd=_risk_usd,
                balance=_bal if _bal > 0 else None,
                open_positions=_open_n,
                total_exposure=_exposure,
            )
        except Exception as _ne:
            logger.warning(f"[Orders] trade_opened notifier failed: {_ne}")
        return pos

    def _place_exchange_sl_tp(self, exchange, pos, sl, tp, side, symbol, size, market_type):
        """Attempt to place SL/TP conditional orders on the exchange.

        2026-04-20 FIX (-4120 root cause): Binance USD-M rejects
        type="market" + stopLossPrice/takeProfitPrice params on /fapi/v1/order
        (error -4120 "use Algo Order API"). Route Binance through its native
        STOP_MARKET / TAKE_PROFIT_MARKET types with `stopPrice`. Bybit/Bitget
        keep the ccxt unified path (triggerPrice + stopLossPrice).

        2026-05-21 FIX (Bybit 110092 / Bitget 45122 noise): pre-flight check —
        when current mark has already crossed the SL threshold (e.g. short SL
        at 0.1117 with mark at 0.1117), the exchange would correctly reject
        the placement because the SL would trigger immediately. The fail-closed
        path then market-closed the position with an EMERGENCY log + 5 debug
        spam lines. The position would have closed via SL anyway, so we now
        detect this pre-flight, log INFO, and close normally with reason
        "sl_crossed_at_placement". A second guard in the except handler
        catches the rare race past the pre-flight (price moves between check
        and create_order) and downgrades those specific reject codes to INFO.

        Fail-closed policy: if SL placement fails for any other reason, the
        position is closed immediately at market. No naked live positions.
        """
        ex_lower = exchange.name.lower()
        close_side = "sell" if side == "buy" else "buy"
        oneway = ex_lower in self._oneway_mode

        # Round prices to exchange precision (fixes Bitget 40808 "checkBDScale" error)
        sl_rounded = exchange.round_price(symbol, sl)
        tp_rounded = exchange.round_price(symbol, tp)
        size_rounded = exchange.round_quantity(symbol, size)

        # 2026-05-21 pre-flight: mark has already crossed SL → exchange would
        # reject (Bybit 110092 / Bitget 45122). The SL would have triggered
        # anyway; close normally rather than fail-closed-EMERGENCY.
        sl_already_crossed = False
        try:
            tkr = exchange.fetch_ticker(symbol, market_type)
            last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
        except Exception:
            last = 0.0
        if last > 0:
            side_l = (side or "").lower()
            if side_l == "buy" and sl_rounded >= last:
                sl_already_crossed = True
            elif side_l == "sell" and sl_rounded <= last:
                sl_already_crossed = True
        if sl_already_crossed:
            logger.info(
                f"[Orders] SL pre-flight: {symbol} {side.upper()} SL "
                f"{sl_rounded:.6g} already on wrong side of mark {last:.6g} "
                f"— closing {pos.id[:8]} normally (would-have-triggered).")
            pos._exchange_sl = False
            pos._exchange_tp = False
            try:
                self.close_position(exchange, pos, reason="sl_crossed_at_placement")
            except Exception as close_err:
                logger.error(
                    f"[Orders] close_position failed after SL pre-flight for "
                    f"{symbol}: {close_err}")
            return

        # Stop-Loss — spec §14: failed SL must trigger emergency handling.
        # 2026-04-20: fail-closed. If SL cannot be attached, close the
        # position at market instead of leaving it naked.
        try:
            sl_type, sl_params = build_sl_tp_order_params(
                ex_lower, side, oneway, sl_rounded, is_sl=True)
            _sl_res = exchange.create_order(
                symbol, sl_type, close_side, size_rounded, None, sl_params,
                market_type=market_type)
            if not _sl_res:
                # 2026-07-07: a not-connected client returns {} WITHOUT
                # raising — treating that as success marked naked positions
                # as exchange-protected and suppressed the local SL fallback.
                # Raise into the fail-closed path below instead.
                raise RuntimeError(
                    "create_order returned an empty result for the SL order "
                    "(client not connected?)")
            pos._exchange_sl = True
            logger.info(
                f"[Orders] SL order on {exchange.name}: {symbol} "
                f"@ {sl_rounded} (type={sl_type})")
        except Exception as e:
            # 2026-05-21: catch the rare race past the pre-flight — mark moved
            # between fetch_ticker and create_order. Bybit 110092 and Bitget
            # 45122 both mean "SL on wrong side of mark"; close normally.
            err_str = str(e)
            is_crossed_race = (
                "110092" in err_str
                or "45122" in err_str
                or "stop loss price please" in err_str.lower()
                or "expect rising" in err_str.lower()
                or "expect falling" in err_str.lower()
            )
            if is_crossed_race:
                logger.info(
                    f"[Orders] SL race-rejection on {exchange.name} {symbol}: "
                    f"{err_str[:160]} — closing {pos.id[:8]} normally "
                    f"(would-have-triggered).")
                pos._exchange_sl = False
                pos._exchange_tp = False
                try:
                    self.close_position(
                        exchange, pos, reason="sl_crossed_during_placement")
                except Exception as close_err:
                    logger.error(
                        f"[Orders] close_position failed after SL race-reject "
                        f"for {symbol}: {close_err}")
                return
            logger.error(
                f"[Orders] EMERGENCY: SL order FAILED on {exchange.name} {symbol}: {e} "
                f"— closing position {pos.id[:8]} at market (fail-closed policy).")
            # Capture ccxt's last HTTP round-trip so the next occurrence of
            # -4120 (or any repeat rejection) leaves enough evidence on disk
            # to diagnose without a second live failure. Raw body bypasses
            # ccxt's error-string truncation.
            try:
                raw = getattr(exchange, "exchange", None)
                req_url    = getattr(raw, "last_request_url", None)
                req_body   = getattr(raw, "last_request_body", None)
                req_hdrs   = getattr(raw, "last_request_headers", None)
                resp_body  = getattr(raw, "last_http_response", None)
                resp_hdrs  = getattr(raw, "last_response_headers", None)
                opts_type  = None
                try:
                    opts_type = raw.options.get("defaultType") if raw else None
                except Exception:
                    pass
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | exchange={exchange.name} symbol={symbol} "
                    f"order_type={sl_type} close_side={close_side} size={size_rounded} "
                    f"sl_params={sl_params} defaultType={opts_type}")
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | request_url={req_url} "
                    f"request_body={req_body}")
                logger.error(
                    f"[Orders] SL-FAIL DEBUG | response_body={resp_body}")
                # Full headers only at debug level — they contain auth/signature echoes.
                logger.debug(
                    f"[Orders] SL-FAIL DEBUG hdrs | req={req_hdrs} resp={resp_hdrs}")
            except Exception as dbg_err:
                logger.warning(f"[Orders] SL-FAIL debug capture itself failed: {dbg_err}")
            try:
                self.notifier.send(
                    f"EMERGENCY: SL placement FAILED for {symbol} on {exchange.name}. "
                    f"Closing position {pos.id[:8]} at market. Reason: {e}")
            except Exception:
                pass
            pos._sl_failed = True
            # Fail-closed: close position immediately at market.
            try:
                self.close_position(exchange, pos, reason="sl_placement_failed")
            except Exception as close_err:
                logger.critical(
                    f"[Orders] CRITICAL: fail-closed close_position also failed "
                    f"for {symbol} — position is naked and tracker may be stale: "
                    f"{close_err}")
                try:
                    self.notifier.send(
                        f"CRITICAL: {symbol} on {exchange.name} is NAKED and "
                        f"auto-close failed. MANUAL INTERVENTION REQUIRED. "
                        f"Reason: {close_err}")
                except Exception:
                    pass
            # Do not attempt TP placement; position is (or should be) closed.
            return

        # Take-Profit
        try:
            tp_type, tp_params = build_sl_tp_order_params(
                ex_lower, side, oneway, tp_rounded, is_sl=False)
            _tp_res = exchange.create_order(
                symbol, tp_type, close_side, size_rounded, None, tp_params,
                market_type=market_type)
            if not _tp_res:
                # 2026-07-07: same not-connected {} pattern as the SL above —
                # raise into the handler below (local monitoring covers TP).
                raise RuntimeError(
                    "create_order returned an empty result for the TP order "
                    "(client not connected?)")
            pos._exchange_tp = True
            logger.info(
                f"[Orders] TP order on {exchange.name}: {symbol} "
                f"@ {tp_rounded} (type={tp_type})")
        except Exception as e:
            # Clear the flag so _exchange_handles_sltp evaluates False and the
            # polled SL/TP price-trigger + B6 planned-first path cover the dropped
            # TP until _reconcile_missing_tp restores the exchange TP (audit
            # 2026-06-21 B7). On the fresh-entry path the attr may be unset, so
            # this is not always already-False.
            pos._exchange_tp = False
            logger.warning(
                f"[Orders] TP order FAILED on {exchange.name} {symbol}: {e} "
                f"— local monitoring active (SL is protected)")

    @staticmethod
    def _cap_stop_fill(sim_px: float, trigger_px: float, close_side: str) -> float:
        """Bound a simulated STOP fill so it can never be BETTER than the
        trigger level (2026-07-07). paper_fill_price derives fills from the
        live book at poll time; on a wick-through-and-recover the book has
        already bounced, but a live stop-market would have filled at (or
        beyond) the trigger during the move. Selling to close a long: lower
        is worse. Buying to close a short: higher is worse."""
        if trigger_px <= 0 or sim_px <= 0:
            return sim_px
        if str(close_side).lower() == "sell":
            return min(sim_px, trigger_px)
        return max(sim_px, trigger_px)

    def _replace_exchange_sl(self, exchange: BaseExchange, pos: Position) -> None:
        """B7-P2 per-position serialization wrapper (default-OFF). Serializes
        SL-mutation with close/other SL-moves on the same id via the shared
        per-id RLock; timeout-acquire with proceed-on-timeout so it can never
        freeze the SL monitor. RLock re-entry covers the fail-closed re-call
        into close_position inside _place_exchange_sl_tp."""
        if not getattr(self, "_per_pos_lock_enabled", False):
            return self._replace_exchange_sl_impl(exchange, pos)
        lk = self._position_lock(pos.id)
        acquired = lk.acquire(timeout=self._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] _replace_exchange_sl _position_lock TIMEOUT for "
                f"{pos.id} after 5s — proceeding WITHOUT serialization")
        try:
            return self._replace_exchange_sl_impl(exchange, pos)
        finally:
            if acquired:
                lk.release()

    def _replace_exchange_sl_impl(self, exchange: BaseExchange, pos: Position) -> None:
        """Cancel the existing SL/TP conditional orders on the exchange and
        re-place them at the current ``pos.stop_loss`` / ``pos.take_profit``
        for the current ``pos.size``.

        2026-04-25 (post-audit): pre-flight check added — if the target SL
        sits on the wrong side of current price (would be exchange-rejected,
        e.g. Bitget code 40917) the cancel-then-place cycle would strip the
        working SL and fail-closed would market-close an otherwise-healthy
        position. Skip the move when invalid; the existing SL keeps running.

        2026-04-19 (Fix A2/A3): the trailing-advance and partial-TP-breakeven
        paths only mutate ``pos.stop_loss`` in the tracker. In CONTROLLED_LIVE
        the exchange keeps running the original (wider) SL because
        ``_exchange_sl=True`` silences local SL monitoring — so the tighter
        SL was a ghost. This helper mirrors ``bot_engine._replace_exchange_sl``
        so the order-manager's internal loops can sync too.
        """
        if self.dry_run:
            return  # paper mode: no exchange order to cancel/re-place
        if not pos or pos.market_type != "futures":
            return  # spot has no exchange-side SL/TP
        if not pos.stop_loss or pos.stop_loss <= 0:
            return

        # Pre-flight: refuse SL placements the exchange would reject for
        # being on the wrong side of current price. See the 2026-04-25 note
        # above — this prevents fail-closed from killing healthy positions
        # during a normal pullback through a tightened/breakeven SL level.
        try:
            tkr = exchange.fetch_ticker(pos.symbol, pos.market_type)
            last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
        except Exception as e:
            logger.debug(f"[Orders] Replace-SL mark check unavailable for {pos.symbol}: {e}")
            last = 0.0
        if last > 0:
            side = (pos.side or "").lower()
            if side == "buy" and pos.stop_loss >= last:
                logger.info(
                    f"[Orders] Replace-SL SKIP {pos.symbol} BUY: target SL "
                    f"{pos.stop_loss:.6g} >= last {last:.6g} (would be "
                    f"exchange-rejected); keeping current exchange SL")
                return
            if side == "sell" and pos.stop_loss <= last:
                logger.info(
                    f"[Orders] Replace-SL SKIP {pos.symbol} SELL: target SL "
                    f"{pos.stop_loss:.6g} <= last {last:.6g} (would be "
                    f"exchange-rejected); keeping current exchange SL")
                return

        # Cancel ALL conditional orders on the symbol. We do not track the
        # specific SL/TP order IDs at placement time; the symbol scope is
        # narrow enough for one position.
        try:
            if hasattr(exchange, "cancel_all_orders"):
                exchange.cancel_all_orders(pos.symbol, pos.market_type)
            else:
                exchange.exchange.cancel_all_orders(pos.symbol)
        except Exception as e:
            logger.warning(
                f"[Orders] Replace-SL cancel_all_orders failed for "
                f"{pos.symbol}: {str(e)[:120]} — proceeding with re-place")

        # Clear flags so a placement failure falls through to local monitoring
        pos._exchange_sl = False
        pos._exchange_tp = False

        try:
            self._place_exchange_sl_tp(
                exchange, pos, pos.stop_loss, pos.take_profit,
                pos.side, pos.symbol, pos.size, pos.market_type)
            logger.info(
                f"[Orders] Exchange SL synced: {pos.symbol} "
                f"SL={pos.stop_loss:.6g} size={pos.size:.8g}")
        except Exception as e:
            logger.error(
                f"[Orders] Replace-SL re-place failed for {pos.symbol}: "
                f"{str(e)[:150]}")

    def _reconcile_missing_sl(self, exchange: BaseExchange, pos: Position) -> bool:
        """Re-establish a missing exchange-side SL on a live futures position.

        2026-06-20: ``_sl_failed`` was set at placement (see
        ``_place_exchange_sl_tp`` fail-closed path) but never read again — a
        position whose SL placement failed (and whose fail-closed market-close
        also failed, leaving it open) survived only on the slower polled-SL
        checks in ``check_sl_tp`` and never regained exchange-side protection.
        This re-attempts placement once per minute per position. Local polled
        SL still guards the position in the meantime, so this only *restores*
        the always-on, wick-accurate exchange stop; it is not the sole rail.

        Returns True when the position has (or regains) exchange-side SL.
        """
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return True
        if not getattr(pos, "_sl_failed", False):
            return True
        if getattr(pos, "_exchange_sl", False):
            pos._sl_failed = False  # already protected; clear the stale flag
            return True
        import time as _t
        if _t.time() - getattr(pos, "_sl_retry_ts", 0.0) < 60:
            return False  # throttle: don't hammer the venue every cycle
        pos._sl_retry_ts = _t.time()
        logger.warning(
            f"[Orders] SL reconcile: re-attempting exchange SL for "
            f"{pos.symbol} {pos.id[:8]} (naked since placement failure)")
        try:
            self._replace_exchange_sl(exchange, pos)
        except Exception as e:
            logger.error(
                f"[Orders] SL reconcile failed for {pos.symbol}: {str(e)[:120]}")
        if getattr(pos, "_exchange_sl", False):
            pos._sl_failed = False
            logger.info(
                f"[Orders] SL reconcile OK: exchange SL restored for {pos.symbol}")
            return True
        return False

    def _reconcile_missing_tp(self, exchange: BaseExchange, pos: Position) -> bool:
        """Re-establish a missing exchange-side TP on a live futures position.

        Gap (audit 2026-06-21 B7): once ``_exchange_handles_sltp`` is True the
        polled TP check (and the B6 planned-first block) are suppressed — the
        exchange is expected to own the TP. But the venue can drop the planned-TP
        conditional (e.g. a venue-side cancel on a partial fill) while
        ``_exchange_tp`` stays True, orphaning the planned TP so the winner only
        exits via trailing/age. (The companion placement-failure case is handled
        at entry: a failed TP create_order now clears ``_exchange_tp`` so the
        polled/B6 path covers it.) This re-asserts the already-planned
        ``pos.take_profit`` at most once/minute/position when ``_exchange_tp``
        claims True but no profit-side conditional is actually resting on the venue.

        Live/futures-only; a no-op in PAPER and on spot. Returns True when a TP is
        (or regains being) present, or when reconciliation does not apply.
        """
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return True
        if not getattr(pos, "_exchange_tp", False):
            return True  # exchange not meant to own the TP -> polled path covers it
        if not pos.take_profit or float(pos.take_profit) <= 0:
            return True
        if not pos.entry_price or float(pos.entry_price) <= 0:
            return True  # can't classify profit/loss side without a valid entry
        import time as _t
        if _t.time() - getattr(pos, "_tp_retry_ts", 0.0) < 60:
            return True  # throttle: at most once/minute/position
        pos._tp_retry_ts = _t.time()

        # fetch_open_orders NEVER raises (exchanges/base.py:378-387 returns [] on
        # error AND when not-ready), so an EMPTY list is INCONCLUSIVE — never treat
        # it as proof the TP is gone, or a transient API failure would churn a
        # cancel-all + re-place every cycle. Only act on a NON-EMPTY list that has
        # a loss-side SL conditional but NO profit-side TP conditional.
        open_orders = exchange.fetch_open_orders(pos.symbol, pos.market_type)
        if not open_orders:
            return True  # inconclusive (error or genuinely empty) -> no-op
        ep = float(pos.entry_price)
        side = (pos.side or "").lower()
        tp_present = sl_present = False
        for o in open_orders:
            info = o.get("info", {}) or {}
            trig = (o.get("triggerPrice") or o.get("stopPrice")
                    or info.get("triggerPrice") or info.get("stopPrice"))
            try:
                trig = float(trig) if trig is not None else None
            except (TypeError, ValueError):
                trig = None
            if not trig or trig <= 0:
                continue
            profit_side = (trig > ep) if side == "buy" else (trig < ep)
            if profit_side:
                tp_present = True
            else:
                sl_present = True
        if tp_present or not sl_present:
            # TP already resting, OR no SL-side conditional either (inconclusive
            # shape) — nothing to safely act on.
            return True

        # Orphaned exchange TP (SL resting, no profit-side conditional). Do NOT
        # cancel the WORKING SL to re-place both (that primitive,
        # _replace_exchange_sl, cancel_all_orders first => a brief naked-SL window
        # if the SL re-place then fails). Instead DOWNGRADE to local polled
        # enforcement: clear _exchange_tp so _exchange_handles_sltp evaluates False
        # and the polled SL/TP price-trigger + B6 planned-first path watch the
        # planned TP from here. Same safe fallback as a failed TP placement at
        # entry; never touches the SL. (Trade-off: polled TP loses wick accuracy
        # vs an exchange conditional — acceptable for a rare orphan, and strictly
        # safer than risking the working SL. This is the audit's "re-validate
        # _exchange_tp before suppressing the polled check" option.)
        logger.warning(
            f"[Orders] TP reconcile: exchange TP missing for {pos.symbol} "
            f"{pos.id[:8]} (SL present, no profit-side conditional) — downgrading "
            f"to polled TP enforcement (planned TP {float(pos.take_profit):.6g})")
        pos._exchange_tp = False
        return True

    # ── SL/TP liveness verifiers (C1, tpbot retrofit 2026-07-08) ─────────
    # position_tracker._patch0_instrument_ghost has called these on every
    # profitable ghost since 2026-05-19, but they were never implemented —
    # the AttributeError was silently swallowed, so the ghost-reroute gate
    # (Patch #1) collected would_reroute=unknown telemetry the whole time.

    def _classify_resting_conditionals(self, exchange: BaseExchange,
                                       pos: Position) -> tuple:
        """Classify resting conditional orders on the venue for ``pos``.

        Same side convention as ``_reconcile_missing_tp``: a trigger above
        entry is profit-side for buys and loss-side for sells (mirrored).

        Returns ``(sl_present, tp_present, conclusive)``. ``conclusive`` is
        False when the venue answer cannot be trusted: empty open-orders
        list (exchanges/base.py returns [] on error AND when not-ready) or
        an unusable entry price. Callers must fail closed on inconclusive.
        """
        try:
            ep = float(pos.entry_price or 0)
        except (TypeError, ValueError):
            ep = 0.0
        if ep <= 0:
            return False, False, False
        try:
            open_orders = exchange.fetch_open_orders(pos.symbol, pos.market_type)
        except Exception as e:  # nonconforming client; base.py normally returns []
            logger.warning(
                f"[Orders] conditional classify: fetch_open_orders raised for "
                f"{pos.symbol} on {getattr(exchange, 'name', '?')}: {str(e)[:120]}")
            return False, False, False
        if not open_orders:
            return False, False, False  # inconclusive — error or genuinely empty
        side = (pos.side or "").lower()
        tp_present = sl_present = False
        for o in open_orders:
            info = o.get("info", {}) or {}
            trig = (o.get("triggerPrice") or o.get("stopPrice")
                    or info.get("triggerPrice") or info.get("stopPrice"))
            try:
                trig = float(trig) if trig is not None else None
            except (TypeError, ValueError):
                trig = None
            if not trig or trig <= 0:
                continue
            profit_side = (trig > ep) if side == "buy" else (trig < ep)
            if profit_side:
                tp_present = True
            else:
                sl_present = True
        return sl_present, tp_present, True

    def verify_exchange_sl_alive(self, exchange: BaseExchange,
                                 pos: Position) -> bool:
        """True only when a loss-side conditional is VERIFIED resting on the
        venue for ``pos``. Fail-closed: PAPER (no venue orders exist), spot
        (no exchange-side protection by design), fetch failures and the
        inconclusive empty-list shape all return False — consumers (the
        ghost-reroute instrumentation) must never credit unverified
        protection. Read-only: never places or cancels anything."""
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return False
        sl_present, _tp, conclusive = self._classify_resting_conditionals(
            exchange, pos)
        venue = str(getattr(exchange, "name", "?")).lower()
        logger.debug(
            f"[Orders] verify_sl_alive {pos.symbol}@{venue}: "
            f"sl={sl_present} conclusive={conclusive}")
        return bool(conclusive and sl_present)

    def verify_exchange_tp_alive(self, exchange: BaseExchange,
                                 pos: Position) -> bool:
        """True only when a profit-side conditional is VERIFIED resting on
        the venue for ``pos``. Same fail-closed semantics as
        ``verify_exchange_sl_alive``. Read-only."""
        if self.dry_run or getattr(pos, "market_type", None) != "futures":
            return False
        _sl, tp_present, conclusive = self._classify_resting_conditionals(
            exchange, pos)
        venue = str(getattr(exchange, "name", "?")).lower()
        logger.debug(
            f"[Orders] verify_tp_alive {pos.symbol}@{venue}: "
            f"tp={tp_present} conclusive={conclusive}")
        return bool(conclusive and tp_present)

    # ── Close position ────────────────────────────────────────────────

    def partial_close_position(self, exchange: BaseExchange, position: Position,
                              close_pct: float, reason: str, price: float):
        """Close a fraction of a position and move SL to breakeven on remainder."""
        partial_size = position.size * close_pct
        # C2 (tpbot retrofit 2026-07-08): quantize to the venue lot step BEFORE
        # the notional check, the order AND the tracker decrement. An unrounded
        # fraction sent to a step-constrained contract (Bitget 0.1-step) fills
        # at the venue-floored size while the tracker decrements the raw
        # fraction — position.size drifts from the venue on every partial.
        # Best-effort: fakes/stubs without lot metadata keep the raw fraction
        # (paper realism unchanged); a fraction that floors to zero is refused.
        try:
            _rq = float(exchange.round_quantity(position.symbol, partial_size))
            partial_size = _rq if _rq > 0 else 0.0
        except Exception:
            pass
        if partial_size <= 0 or partial_size * price < 5.0:
            return False

        close_side = "sell" if position.side == "buy" else "buy"

        if self.dry_run:
            sim_px = self.sim.paper_fill_price(
                exchange, position.symbol, close_side,
                position.market_type, base_price=price, phase="close",
                size=partial_size)
            if sim_px > 0:
                price = sim_px
            logger.info(
                f"[Orders] [DRY] PARTIAL CLOSE {position.symbol} "
                f"{close_pct:.0%} ({partial_size:.8f}) @ {price:.4f} | {reason}")
        else:
            # 2026-05-24 — Futures partial close must carry reduceOnly=True.
            # Without it, if position size races (e.g. exchange-side SL/TP
            # fires between the read and this order), the partial close
            # becomes an opposite-direction entry on Bybit/Bitget instead of
            # reducing the position. Every other close path in this file
            # passes reduceOnly; the partial path was the gap.
            _params: dict = {}
            if position.market_type == "futures":
                _params["reduceOnly"] = True
                # Hedge-mode (not one-way) futures need positionSide so ccxt can disambiguate the
                # leg — mirrors close_position. One-way venues (e.g. Bitget) keep reduceOnly only.
                if exchange.name.lower() not in self._oneway_mode:
                    _params["positionSide"] = "LONG" if position.side == "buy" else "SHORT"
            try:
                exchange.create_order(
                    position.symbol, "market", close_side, partial_size,
                    params=_params,
                    market_type=position.market_type)
            except Exception as e:
                logger.error(f"[Orders] Partial close failed: {e}")
                return False

        position.size -= partial_size
        position.partial_taken = True

        # Compute the partial fill's economics ONCE (mode-agnostic) and record
        # it on the Position so _finalize_close books the WHOLE trade — not just
        # the runner leg (audit 2026-06-04: the taken fraction only ever reached
        # the paper wallet, so the warehouse trade row under-counted realized PnL
        # and biased WR / realized-R downward). book_partial_exit is additive and
        # disjoint from the final (reduced-size) close — NO double-count.
        p_gross = 0.0
        p_exit_fee = 0.0
        try:
            p_gross = ((price - position.entry_price) if position.side == "buy"
                       else (position.entry_price - price)) * partial_size
            try:
                from core.position_tracker import _fee_rate
                p_exit_fee = partial_size * price * _fee_rate(position.market_type)
            except Exception:
                p_exit_fee = partial_size * price * (
                    0.0005 if position.market_type == "futures" else 0.001)
            position.book_partial_exit(partial_size, price, p_gross - p_exit_fee)
        except Exception as _be:
            logger.debug(f"[Orders] book_partial_exit skipped: {_be}")

        # Book the closed fraction in the PAPER wallet (audit 2026-06-03). Without this,
        # partial_close never returned the taken fraction's margin + profit to the paper
        # balance — only _finalize_close booked anything, and on the now-REDUCED size — so
        # every partial TP silently leaked the taken fraction's margin + profit from the
        # paper wallet, biasing the displayed balance/PnL DOWNWARD. The taken fraction is
        # disjoint from the final close (reduced size), so this is additive with NO
        # double-count. Paper-only (live partials settle on the real exchange).
        _is_paper = position.paper_trade if hasattr(position, "paper_trade") else self.dry_run
        if _is_paper:
            try:
                self.wallet.on_close(
                    position.exchange, position.symbol, position.side,
                    partial_size, price, position.entry_price,
                    p_exit_fee, p_gross,
                    position.market_type, leverage=position.leverage,
                )
            except Exception as we:
                logger.debug(f"[Wallet] partial on_close skipped: {we}")

        # 2026-06-11 (owner): the daily-loss breaker must see the partial leg
        # WHEN it banks — _finalize_close records only the runner's pos.pnl,
        # which excludes realized_partial_pnl, so banked partial profits were
        # invisible to the loss budget (breaker erred conservative).
        # is_win=None on purpose: a partial is not a completed trade — Spec §12
        # streaks, recent-results and Kelly history update once, at
        # _finalize_close, on the whole-trade outcome. Mode-agnostic, like
        # book_partial_exit above (live partials realize PnL too).
        try:
            self.risk.record_trade_pnl(
                p_gross - p_exit_fee, self.risk._start_balance or 0,
                is_win=None,
            )
        except Exception as _rp:
            logger.debug(f"[Risk] partial record_trade_pnl skipped: {_rp}")

        try:
            from config import PARTIAL_TP
            if PARTIAL_TP.get("move_sl_to_breakeven", True):
                position.stop_loss = position.entry_price
                logger.info(f"[Orders] SL moved to breakeven {position.entry_price:.4f}")
        except ImportError:
            position.stop_loss = position.entry_price

        with self.tracker._lock:
            self.tracker._save()

        # 2026-04-19 (Fix A3): if the exchange is holding the SL, the local
        # breakeven move is a ghost until we cancel the old (wider, full-size)
        # SL/TP and re-place at entry for the remaining size. Without this,
        # the exchange keeps running the original SL while the tracker thinks
        # breakeven is locked in.
        if (not self.dry_run
                and getattr(position, "_exchange_sl", False)
                and position.market_type == "futures"):
            try:
                self._replace_exchange_sl(exchange, position)
            except Exception as e:
                logger.warning(
                    f"[Orders] Partial-TP breakeven SL re-place failed "
                    f"for {position.symbol}: {str(e)[:120]}")
        return True

    def _position_lock(self, pid: str) -> threading.RLock:
        """Return the one shared RLock for this position id (created on first
        use under the guard). RLock — not Lock — because the fail-closed path
        _replace_exchange_sl -> _place_exchange_sl_tp -> close_position re-enters
        on the SAME thread for the SAME id (proof 2026-06-21). The guard holds
        ONLY for the dict get-or-create; the per-id lock is acquired by the
        caller OUTSIDE the guard so the guard never nests with anything."""
        with self._pos_locks_guard:
            lk = self._pos_locks.get(pid)
            if lk is None:
                lk = threading.RLock()
                self._pos_locks[pid] = lk
            return lk

    @staticmethod
    def _close_order_confirmed(order) -> bool:
        """Return True only when a close order response proves venue receipt/fill."""
        if not isinstance(order, dict):
            return False
        status = str(order.get("status") or "").lower()
        if status in {"rejected", "failed", "canceled", "cancelled"}:
            return False
        if order.get("id"):
            return True
        try:
            if float(order.get("filled") or 0) > 0:
                return True
        except Exception:
            pass
        return status in {"closed", "filled"}

    def close_position(self, exchange: BaseExchange, position: Position,
                       reason: str, price: float = None,
                       order_type: str = "market",
                       decision_id: str | None = None):
        """B7-P2 per-position serialization wrapper (default-ON via
        PER_POSITION_LOCK_ENABLED). When ON, serializes close + SL-mutation on
        the same position id and no-ops the race loser via tracker.is_position_open.
        Timeout-acquire (5s < the 10s monitor cadence) and fail closed on
        timeout so close/SL mutation never proceeds without serialization."""
        if not getattr(self, "_per_pos_lock_enabled", False):
            return self._close_position_impl(
                exchange, position, reason, price, order_type, decision_id)
        lk = self._position_lock(position.id)
        acquired = lk.acquire(timeout=self._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] close_position _position_lock TIMEOUT for "
                f"{position.id} after 5s — refusing unsynchronized close")
            return None
        try:
            if not self.tracker.is_position_open(position.id):
                logger.info(
                    f"[Orders] close race no-op: {position.id[:8]} already closed")
                return None
            return self._close_position_impl(
                exchange, position, reason, price, order_type, decision_id)
        finally:
            if acquired:
                lk.release()

    def _close_position_impl(self, exchange: BaseExchange, position: Position,
                       reason: str, price: float = None,
                       order_type: str = "market",
                       decision_id: str | None = None):

        # Provenance: stash the CLOSE decision's id on the position so
        # _finalize_close (fired via PositionTracker.on_close on EVERY close
        # path) can thread it into record_trade_close(exit_decision_id=...).
        # Deterministic exits (SL/TP/trailing) pass nothing → NULL (plan 0E).
        if decision_id:
            position._exit_decision_id = decision_id

        close_side = "sell" if position.side == "buy" else "buy"

        # Capture exit mid for attribution BEFORE any sim slippage runs.
        # If the caller passed `price`, fetch the ticker just for mid.
        exit_mid = 0.0
        ticker = None
        if not price:
            ticker = exchange.fetch_ticker(position.symbol, position.market_type)
            price  = ticker.get("last") or ticker.get("close")
        else:
            try:
                ticker = exchange.fetch_ticker(position.symbol, position.market_type)
            except Exception:
                ticker = None
        if ticker:
            exit_mid = _mid_from_ticker(ticker)
        if not price:
            logger.error(f"[Orders] Cannot get close price for {position.symbol}")
            return None
        price = float(price)
        if exit_mid > 0:
            position.exit_mid = exit_mid

        # ── DRY_RUN realism: apply slippage on close (2026-04-11) ──
        # The caller's `price` is either a ticker.last (monitor poll) or an
        # SL/TP trigger level. Either way, in LIVE a market close eats the
        # spread in the opposite direction plus slippage. Stop-losses get
        # the wider `pct_stop_loss` because market orders fill worse during
        # the fast moves that trigger SLs.
        _is_paper = position.paper_trade if hasattr(position, 'paper_trade') else self.dry_run
        if _is_paper:
            phase = "stop" if reason in ("stop_loss", "trailing_stop") else "close"
            sim_px = self.sim.paper_fill_price(
                exchange, position.symbol, close_side,
                position.market_type, base_price=price, phase=phase,
                size=position.size)
            if sim_px > 0:
                if phase == "stop":
                    # 2026-07-07: paper_fill_price re-prices at the CURRENT
                    # book — on a dip-and-recover wick that booked the
                    # recovered price instead of the trigger a live stop-market
                    # would have filled at (30d avg: fills 47-85bps BETTER than
                    # the stop). A stop fill must never beat its trigger level.
                    sim_px = self._cap_stop_fill(sim_px, price, close_side)
                if sim_px != price:
                    logger.debug(
                        f"[SimExec] {position.symbol} CLOSE ({reason}) slip: "
                        f"{price:.6g} → {sim_px:.6g}")
                    price = sim_px

        # Route based on the POSITION's origin, not current mode.
        # A paper_trade position was never placed on the exchange — sending
        # a reduce-only close would fail with "position is zero".
        if _is_paper:
            logger.info(
                f"[Orders] [DRY] CLOSE {position.symbol} "
                f"@ {price:.4f} | reason={reason}"
            )
        else:
            ex_lower = exchange.name.lower()
            order_placed = False

            # Build close params — One-Way mode needs special handling per exchange
            params = {}
            if position.market_type == "futures":
                if ex_lower in self._oneway_mode:
                    # Bitget one-way: needs reduceOnly so client converts to tradeSide="close"
                    # Other one-way (Binance): no positionSide, no reduceOnly
                    if getattr(exchange, '_is_oneway', False):
                        params["reduceOnly"] = True
                else:
                    params["positionSide"] = (
                        "LONG" if position.side == "buy" else "SHORT")
                    params["reduceOnly"] = True

            try:
                close_order = exchange.create_order(
                    position.symbol, order_type, close_side,
                    position.size,
                    price if order_type == "limit" else None,
                    params=params, market_type=position.market_type)
                if not self._close_order_confirmed(close_order):
                    raise RuntimeError(f"close order not confirmed: {close_order}")
                order_placed = True
                # Use actual fill price if available
                fill = close_order.get("average") or close_order.get("price")
                if fill:
                    price = float(fill)
            except Exception as e:
                err = str(e)
                # Already closed/liquidated on exchange — mark as closed in tracker
                _already_closed = (
                    "No position" in err or "22002" in err
                    or "110017" in err
                    or "position is zero" in err.lower()
                    or "position does not exist" in err.lower()
                    or "order not found" in err.lower()
                )
                if _already_closed:
                    logger.info(
                        f"[Orders] {position.symbol} already closed on exchange "
                        f"— syncing tracker")
                    order_placed = True  # Proceed to close in tracker
                # "reduceonly not required" — Binance One-Way mode
                elif "reduceonly" in err.lower() or "-1106" in err:
                    self._oneway_mode.add(ex_lower)
                    self._save_order_mode_state()
                    # 2026-04-20: Before retrying without reduceOnly, verify the
                    # position still exists on the exchange. Without this guard
                    # the retry opens a NAKED REVERSE position when the target
                    # is already closed (e.g. TP/SL fired, stale list caller).
                    # Root cause of the ALGO naked-short cascade on 2026-04-20.
                    _still_open = False
                    try:
                        _live = exchange.fetch_positions([position.symbol]) or []
                        for _p in _live:
                            _sz = abs(float(_p.get("contracts") or _p.get("contractSize") or 0))
                            if _sz > 0:
                                _still_open = True
                                break
                    except Exception as _fe:
                        logger.warning(
                            f"[Orders] fetch_positions failed during close-retry "
                            f"guard for {position.symbol}: {_fe} — "
                            f"treating as closed to avoid naked reverse")
                    if not _still_open:
                        logger.info(
                            f"[Orders] {position.symbol} already flat on exchange "
                            f"— skipping naked-reverse retry (fail-closed guard)")
                        order_placed = True
                    else:
                        logger.info(f"[Orders] {exchange.name} One-Way mode — retrying close without reduceOnly")
                        try:
                            retry_order = exchange.create_order(
                                position.symbol, order_type, close_side,
                                position.size,
                                price if order_type == "limit" else None,
                                params={}, market_type=position.market_type)
                            if not self._close_order_confirmed(retry_order):
                                raise RuntimeError(
                                    f"retry close order not confirmed: {retry_order}")
                            order_placed = True
                        except Exception as e2:
                            err2 = str(e2).lower()
                            if "no position" in err2 or "22002" in err2 or "does not exist" in err2 \
                                    or "110017" in err2 or "position is zero" in err2:
                                order_placed = True
                            else:
                                logger.error(f"[Orders] Close failed (retry): {e2}")
                # Position mode mismatch (Bitget 40773/40774 / Binance positionSide)
                elif (_is_position_mode_error(e) or "unilateral" in err.lower()) \
                        and position.market_type == "futures":
                    self._oneway_mode.add(ex_lower)
                    self._save_order_mode_state()
                    # One-way mode: reduceOnly=True tells the exchange this is a close
                    _ow_close = {"reduceOnly": True}
                    try:
                        retry_order = exchange.create_order(
                            position.symbol, order_type, close_side,
                            position.size, None,
                            params=_ow_close, market_type=position.market_type)
                        if not self._close_order_confirmed(retry_order):
                            raise RuntimeError(
                                f"one-way close order not confirmed: {retry_order}")
                        order_placed = True
                    except Exception as e2:
                        err2 = str(e2).lower()
                        if "no position" in err2 or "22002" in err2 or "does not exist" in err2 \
                                or "110017" in err2 or "position is zero" in err2:
                            order_placed = True
                        else:
                            logger.error(f"[Orders] Close failed (one-way): {e2}")
                else:
                    logger.error(f"[Orders] Close failed for {position.id}: {e}")

            if not order_placed:
                # Track close failures. Do NOT local-close after repeated
                # failures: if the venue close is unconfirmed, hiding the
                # position locally is more dangerous than keeping it visible.
                fc = self._close_fail_count.get(position.id, 0) + 1
                self._close_fail_count[position.id] = fc
                self._save_close_fail_count()
                if fc >= 3:
                    logger.critical(
                        f"[Orders] CLOSE FAILED {fc}x for {position.symbol} "
                        f"{position.side.upper()} id={position.id}: keeping "
                        f"tracker OPEN until exchange close is confirmed. "
                        f"Manual reconciliation required.")
                return None

        # Success — clear any failure counter
        if position.id in self._close_fail_count:
            self._close_fail_count.pop(position.id, None)
            self._save_close_fail_count()

        # 2026-05-02 fix (orphan stop-order accumulation):
        # When a position closes, the OTHER conditional order (TP if SL
        # hit, SL if TP hit, both if market close) becomes an orphan.
        # Pre-fix the close path never cancelled these. Over 5 days, 24
        # orphans accumulated on Bybit, hitting the per-symbol stop-order
        # limit and triggering fail-closed cascades on new entries.
        # Now we cancel all open orders for the symbol after close.
        # cancel_all_orders is best-effort — failure here doesn't break
        # the close. Bybit-specific path handles its conditional ledger
        # via the override in bybit_client.cancel_all_orders.
        if not _is_paper:
            try:
                exchange.cancel_all_orders(position.symbol, position.market_type)
            except Exception as e:
                logger.debug(
                    f"[Orders] post-close cancel_all_orders {position.symbol}: "
                    f"{str(e)[:120]}")

        # tracker.close() now invokes self._finalize_close via its on_close
        # hook (wired in BotEngine.__init__). All post-close work — wallet,
        # risk, Spec §12 streaks, compliance, blacklist, trailing cleanup,
        # warehouse update, post-mortem, notifier — happens there so ghost-
        # closed positions get the same treatment as normal exits.
        closed = self.tracker.close(position.id, price, reason)
        if not closed:
            return None
        return closed

    @property
    def calibrator(self):
        """Lazy-load ProbabilityCalibrator singleton (Phase 18, 2026-05-04).

        Used by _finalize_close to feed (predicted_conf, actual_win) outcomes
        so the isotonic calibration can fit on real data. Was previously
        instantiated only in LearningEngine but never received valid input
        because positions.json never persisted `confidence`.
        """
        cached = getattr(self, "_calibrator_instance", None)
        if cached is not None:
            return cached
        try:
            from core.probability_calibrator import ProbabilityCalibrator
            self._calibrator_instance = ProbabilityCalibrator()
        except Exception:
            self._calibrator_instance = None
        return self._calibrator_instance

    def _finalize_close(self, pos: Position, price: float, reason: str) -> None:
        """Post-close hooks fired by PositionTracker.on_close after EVERY
        close (normal exit, ghost-sync, ghost-reconciled, ghost_force_close,
        STALE, AGE_LIMIT, fail-closed). Centralising here is the fix for the
        2026-04-26 finding that ghost-closed positions were silently bypassing
        warehouse, daily_pnl, Spec §12 streak counters, blacklist, trailing
        cleanup, post-mortem and notifier — meaning a -$0.64 ghost loss
        never reached any safety rail.

        ``pos`` is the closed Position (entry fields preserved, exit fields
        populated). ``price`` is the actual exit price the tracker recorded.
        """
        # B7-P2: evict this id's per-position lock to bound dict growth. Runs
        # here (via tracker.on_close) AFTER tracker.close has already popped the
        # id from _open, so any waiter that re-creates a fresh lock for this id
        # will find is_position_open()==False and no-op. Guard the dict op only;
        # never pop inside close_position's own locked region.
        if getattr(self, "_per_pos_lock_enabled", False):
            with self._pos_locks_guard:
                self._pos_locks.pop(pos.id, None)
        # Phase 29 (2026-05-05) — record stop_loss exits for the post-SL
        # cooldown / StoplossGuard layers in RiskManager. Runs FIRST so
        # ledger is updated even if downstream notifier/warehouse code
        # raises. freqtrade pattern: revenge re-entry on a freshly-stopped
        # pair-side is the dominant pattern in the audit's $-78 stop_loss
        # bleed; cooldown breaks it.
        if reason == "stop_loss":
            try:
                self.risk.note_sl_hit(pos.symbol, pos.side)
            except Exception as _e:
                logger.debug(f"[Risk29] note_sl_hit failed: {_e}")
        # CLAUDE.md §4: structured markdown journal of the exit (best-effort, every
        # close path funnels through here, so one wire captures SL/TP/trailing/flip).
        try:
            from core import journal as _journal
            _journal.log_action(
                "CLOSE", pos.symbol, getattr(pos, "side", ""),
                f"reason={reason} pnl=${(pos.pnl or 0.0):+.2f} "
                f"strat={getattr(pos, 'strategy', '')}")
        except Exception:
            pass
        is_win  = (pos.pnl or 0) > 0
        # 2026-05-24 — Was `pos.pnl_pct or 0.0` which converts None → 0.0
        # and trips the _SPEC12_SCRATCH_PCT (0.5%) gate in
        # record_trade_result, silently neutralizing real losses on rows
        # without a computed pnl_pct (e.g. ghost-reclassified SL fills
        # with missing entry context). record_trade_result accepts None
        # and treats it correctly.
        pnl_pct = pos.pnl_pct
        close_side = "sell" if pos.side == "buy" else "buy"
        _is_paper = pos.paper_trade if hasattr(pos, "paper_trade") else self.dry_run

        if _is_paper:
            try:
                self.wallet.on_close(
                    pos.exchange, pos.symbol, pos.side,
                    pos.size, price, pos.entry_price,
                    pos.exit_fee, pos.gross_pnl,
                    pos.market_type, leverage=pos.leverage,
                )
            except Exception as we:
                logger.debug(f"[Wallet] on_close skipped: {we}")

        try:
            self.risk.record_trade_pnl(
                pos.pnl, self.risk._start_balance or 0,
                is_win=is_win, pnl_pct=pnl_pct,
            )
        except Exception as re:
            logger.debug(f"[Risk] record_trade_pnl skipped: {re}")
        # Spec §12 pause policy — needs per-symbol + per-family streaks.
        # Pass pnl_pct + reason so RiskManager can neutralise scratches and
        # infrastructure exits (STALE/AGE_LIMIT/ghost_force_close/etc.) that
        # would otherwise false-trip the 5-consec-loss halt.
        try:
            self.risk.record_trade_result(
                symbol=pos.symbol,
                family=pos.strategy or "unknown",
                is_win=is_win,
                pnl_usd=float(pos.pnl or 0.0),
                pnl_pct=pnl_pct,
                reason=reason,
            )
        except Exception as _rte:
            logger.debug(f"[Risk/Spec12] record_trade_result skipped: {_rte}")
        try:
            self.compliance.log_trade(
                pos.exchange, pos.symbol, close_side,
                pos.size, price, pos.strategy,
                pnl=pos.pnl, reason=reason, order_id=pos.id,
            )
        except Exception as ce:
            logger.debug(f"[Compliance] log_trade skipped: {ce}")

        if is_win:
            self.blacklist.record_win(pos.symbol, pos.side)
        elif reason == "stop_loss":
            self.blacklist.record_stop_loss(pos.symbol, pos.side)

        self.trailing.remove(pos.id)
        self._sl_widened.discard(pos.id)
        self._save_sl_widened()

        # Warehouse trade-close update (spec §4, §6) — locate the row by
        # idempotency key and patch in exit fields + r_multiple.
        #
        # 2026-05-21: split into three try/except layers so that:
        #   (1) trade_id_by_key miss surfaces as a WARNING with full context,
        #       since it is the canonical leak signal (orphan OPEN warehouse
        #       rows accumulate when this lookup silently returns None).
        #   (2) record_trade_close failures warn separately.
        #   (3) best-effort calibrator/attribution stay at debug — those are
        #       not load-bearing for the close ledger.
        tid = None
        try:
            from core.warehouse import get_warehouse
            wh = get_warehouse()
            tid = wh.trade_id_by_key(
                exchange=pos.exchange.lower(),
                symbol=pos.symbol,
                ts_entry=float(getattr(pos, "open_time", 0)),
                side=pos.side,
            )
        except Exception as _we:
            logger.warning(
                f"[Warehouse] trade_id_by_key raised for "
                f"{pos.exchange}:{pos.symbol} {pos.side} "
                f"open_time={getattr(pos, 'open_time', 0)}: {_we}"
            )

        if tid is None:
            logger.warning(
                f"[Warehouse] trade_id lookup MISS — warehouse row will stay OPEN. "
                f"exchange={pos.exchange.lower()} symbol={pos.symbol} "
                f"side={pos.side} open_time={getattr(pos, 'open_time', 0)} "
                f"close_reason={reason}. "
                f"Run scripts/backfill_warehouse_closes.py to repair."
            )

        if tid:
            xp = float(price or 0)
            # r_multiple against the IMMUTABLE entry stop (not the trailed/BE
            # stop) and blended over any partial fills (audit 2026-06-04: a
            # BE-moved stop NULLed/inflated R). r_multiple has no ACTIVE live
            # reader, so fixing it is measurement-only.
            #
            # realized_pnl is DELIBERATELY left as the RUNNER leg: it feeds the
            # live recent_expectancy entry gate + health/mcp/promotion readers,
            # so shifting it would silently change gating. The partial-TP
            # fraction is booked SEPARATELY in partial_realized_pnl; whole-trade
            # $ = realized_pnl + partial_realized_pnl. Recalibrating the gate to
            # whole-trade economics is a separate owner decision.
            # Defensive fallback keeps legacy behavior if the Position predates
            # the booking-completeness methods.
            try:
                eff_exit = pos.effective_exit_price(xp)
                r_mult = pos.r_multiple(eff_exit)
                entry_stop_px = pos.entry_risk_stop()
                partial_pnl = float(getattr(pos, "realized_partial_pnl", 0.0) or 0.0)
            except Exception:
                r_mult = None
                entry_stop_px = None
                partial_pnl = 0.0
            ts_exit = float(getattr(pos, "close_time", 0) or time.time())
            try:
                wh.record_trade_close(
                    trade_id=tid,
                    ts_exit=ts_exit,
                    exit_px=xp,
                    realized_pnl=float(pos.pnl or 0.0),
                    r_multiple=round(r_mult, 3) if r_mult is not None else None,
                    hold_sec=ts_exit - float(getattr(pos, "open_time", ts_exit)),
                    exit_reason=reason,
                    fee=float(getattr(pos, "total_fees", 0.0)),
                    entry_stop_px=round(entry_stop_px, 8) if entry_stop_px else None,
                    partial_realized_pnl=round(partial_pnl, 8) if partial_pnl else None,
                    exit_decision_id=getattr(pos, "_exit_decision_id", None),
                )
            except Exception as _wce:
                logger.warning(
                    f"[Warehouse] record_trade_close failed for tid={tid} "
                    f"{pos.symbol} {pos.side}: {_wce}"
                )

        if tid:
            try:
                # Phase 18 (2026-05-04): feed the ProbabilityCalibrator with
                # this trade's (predicted_conf, actual_win) so the isotonic
                # regression can fit on real data. Was dead until now —
                # calibrator's record() received no input because position
                # confidence was never persisted. Sister fix to Phase 17
                # adaptive sizing wire-fix. Skip rows with no confidence
                # (legacy / non-Claude-portfolio strategies).
                _conf = float(getattr(pos, "confidence", 0.0) or 0.0)
                if _conf > 0 and self.calibrator is not None:
                    try:
                        self.calibrator.record(
                            predicted_conf=_conf,
                            actual_win=is_win,
                            strategy=str(pos.strategy or "all"),
                        )
                    except Exception as _ce:
                        logger.debug(f"[Calibrator] record skipped: {_ce}")

                # ── Forward attribution (Phase 2 / Task A) ────────────
                # Decompose realized_pnl into alpha/spread/slippage/funding/fees
                # and persist to the attribution table. Skip rows with zero
                # mids (ghost-sync, pre-Task-A positions, ticker failures) —
                # those produce a misleading zero-cost decomposition.
                try:
                    if (float(getattr(pos, "entry_mid", 0.0) or 0.0) > 0.0
                            and float(getattr(pos, "exit_mid", 0.0) or 0.0) > 0.0):
                        from core.attribution import Trade as _AttrTrade
                        from core.attribution import record as _attr_record

                        # LIVE funding accrual: query exchange for funding
                        # payments over the hold window. Paper positions
                        # already accumulated `funding_paid` in
                        # accrue_paper_funding. Never block close on a
                        # funding-API hiccup.
                        funding_paid = float(getattr(pos, "funding_paid", 0.0) or 0.0)
                        if not _is_paper and funding_paid == 0.0:
                            try:
                                ex_obj = self._exchange_for(pos.exchange)
                                if ex_obj is not None:
                                    open_ms = int(float(getattr(pos, "open_time", 0)) * 1000)
                                    rows = ex_obj.exchange.fetch_funding_history(
                                        pos.symbol, since=open_ms)
                                    close_ms = int(ts_exit * 1000)
                                    # A2 audit: fetchFundingHistory amount is
                                    # ALREADY signed by the venue (side-aware);
                                    # re-applying a manual side sign inverted the
                                    # paper 'positive=paid' convention. Use direct.
                                    funding_paid = sum(
                                        float(r.get("amount") or 0.0)
                                        for r in (rows or [])
                                        if open_ms <= int(r.get("timestamp") or 0) <= close_ms
                                        and (r.get("symbol") in (pos.symbol, None)
                                             or r.get("info", {}).get("symbol") == pos.symbol)
                                    )
                            except Exception as _fe:
                                logger.debug(
                                    f"[Attribution] funding fetch skipped "
                                    f"({pos.symbol}): {_fe}")

                        # Slippage: paper shows the sim-applied slippage as a
                        # diagnostic; live's spread already captures fill-vs-mid.
                        slippage = 0.0

                        _attr_record(wh, trade_id=tid, trade=_AttrTrade(
                            side=pos.side,
                            size=float(pos.size or 0.0),
                            entry_mid=float(pos.entry_mid),
                            entry_fill=float(pos.entry_price or 0.0),
                            exit_mid=float(pos.exit_mid),
                            exit_fill=float(price or 0.0),
                            funding_paid=float(funding_paid),
                            fees=float(getattr(pos, "total_fees", 0.0) or 0.0),
                            slippage_modeled=float(slippage),
                        ))
                    else:
                        logger.debug(
                            f"[Attribution] {pos.symbol} skipped "
                            f"(entry_mid={getattr(pos, 'entry_mid', 0)}, "
                            f"exit_mid={getattr(pos, 'exit_mid', 0)})")
                except Exception as _ae:
                    logger.debug(f"[Attribution] record skipped: {_ae}")
            except Exception as _post_we:
                # Catches calibrator/attribution failures outside their own
                # inner try/excepts. record_trade_close has its own handler
                # above and is not in this scope.
                logger.debug(f"[Warehouse] post-close best-effort step failed: {_post_we}")

        # Post-mortem analysis
        try:
            self.post_mortem.analyze_trade({
                "symbol": pos.symbol, "exchange": pos.exchange,
                "side": pos.side, "market_type": pos.market_type,
                "strategy": pos.strategy,
                "entry_price": pos.entry_price, "close_price": price,
                "pnl": pos.pnl, "pnl_pct": pnl_pct,
                "close_reason": reason, "leverage": pos.leverage,
                "open_time": getattr(pos, "open_time", 0),
                "close_time": getattr(pos, "close_time", 0),
            })
        except Exception as pm_err:
            logger.debug(f"[PostMortem] {pm_err}")

        # Gather rich context for email notification
        _duration = None
        try:
            _duration = float(getattr(pos, "close_time", 0) or 0) \
                      - float(getattr(pos, "open_time", 0) or 0)
        except Exception:
            pass
        _bal_after = None
        _daily_pnl = None
        _daily_trades = None
        _daily_wr = None
        _open_n = None
        try:
            _open_n = self.tracker.count_open()
            if self.dry_run:
                _bal_after = float(self.wallet.total_balance() or 0.0)
            # Today's trade stats — use local-calendar midnight so email
            # "Daily PnL / WR" matches the dashboard's "Today" panel exactly.
            # Old code used (now - 86400) which is a rolling 24h window:
            # at 07:51AM it reached back to yesterday 07:51AM and blended
            # today+yesterday trades, making the email diverge from the
            # dashboard by up to 2× the daily PnL.
            import datetime as _dt
            _today = _dt.date.today()
            _midnight = _dt.datetime(
                _today.year, _today.month, _today.day, 0, 0, 0
            )
            day_start = int(_midnight.timestamp())
            # Also exclude pure-import reconcile closures (reconciled_no_context,
            # reconciled_from_exchange) which are position-tracker sync events,
            # not real capital decisions. This matches _is_real_trade in dashboard.
            _IMPORT_REASONS = frozenset({
                "reconciled_no_context",
                "reconciled_from_exchange",
            })
            todays = [
                p for p in self.tracker._closed
                if (float(getattr(p, "close_time", 0) or 0) >= day_start
                    and (getattr(p, "close_reason", "") or "") not in _IMPORT_REASONS)
            ]
            _daily_trades = len(todays)
            _daily_pnl = sum(float(p.pnl or 0) for p in todays)
            if _daily_trades:
                _daily_wr = 100.0 * sum(
                    1 for p in todays if (p.pnl or 0) > 0
                ) / _daily_trades
        except Exception:
            pass
        try:
            self.notifier.trade_closed(
                pos.exchange, pos.symbol, pos.side,
                pos.entry_price, price,
                pos.pnl, pnl_pct, reason,
                strategy=getattr(pos, "strategy", None),
                size=getattr(pos, "size", None),
                leverage=getattr(pos, "leverage", None),
                gross_pnl=getattr(pos, "gross_pnl", None),
                entry_fee=getattr(pos, "entry_fee", None),
                exit_fee=getattr(pos, "exit_fee", None),
                total_fees=getattr(pos, "total_fees", None),
                stop_loss=getattr(pos, "stop_loss", None),
                take_profit=getattr(pos, "take_profit", None),
                partial_taken=getattr(pos, "partial_taken", False),
                open_time=getattr(pos, "open_time", None),
                close_time=getattr(pos, "close_time", None),
                duration_sec=_duration,
                balance_after=_bal_after,
                daily_pnl=_daily_pnl,
                daily_trades=_daily_trades,
                daily_wr=_daily_wr,
                open_positions=_open_n,
            )
        except Exception as ne:
            # Upgraded from debug → warning so an email failure is visible
            # at normal log levels. The trade close itself already
            # succeeded; this is purely informational delivery.
            logger.warning(f"[Notifier] trade_closed email failed: {ne}")

    # ── SL / TP / Trailing check ──────────────────────────────────────

    def _net_pnl_at_price(self, pos: Position, price: float) -> tuple:
        """Calculate what net PnL (after fees) would be if we closed at this price.
        Returns (net_pnl_usd, net_pnl_pct, breakeven_price)."""
        from core.position_tracker import _fee_rate
        rate = _fee_rate(pos.market_type)
        entry_fee = pos.size * pos.entry_price * rate
        exit_fee = pos.size * price * rate
        if pos.side == "buy":
            # size is already the leveraged quantity — do NOT multiply by leverage again
            gross = (price - pos.entry_price) * pos.size
            # Breakeven = entry where gross covers both fees + buffer
            be = pos.entry_price * (1 + rate * 2 + 0.0005)
        else:
            gross = (pos.entry_price - price) * pos.size
            be = pos.entry_price * (1 - rate * 2 - 0.0005)
        net = gross - entry_fee - exit_fee
        notional = pos.entry_price * pos.size
        net_pct = (net / notional * 100) if notional > 0 else 0.0
        return net, net_pct, be

    def _early_breakeven_move(self, pos: Position, price: float, effective_sl: float) -> float:
        """Once the trade is at least half way to TP, move SL to breakeven.
        2026-04-16 (post-audit): threshold raised 1.0% → half of (TP distance)
        because ratcheting to BE at 1.0% (25% of a 4% TP) combined with the
        already-removed MCP proactive TP meant any 1% pullback cashed the trade
        at BE. Winners never reached TP — that's the 33 trailing_stop exits
        averaging +$0.82 instead of full TP wins averaging +$2.83.
        """
        _, net_pct, be = self._net_pnl_at_price(pos, price)
        # Threshold = halfway to TP (side-aware). Floored at 1.5% to cover
        # taker-fee round-trip (~0.1%) with breathing room. With a 4% TP this
        # fires at +2%, with an 8% TP at +4% — much less likely to stop out
        # on a normal 1h retrace.
        tp_dist_pct = 0.0
        if pos.take_profit and pos.entry_price > 0:
            if pos.side == "buy":
                tp_dist_pct = (pos.take_profit - pos.entry_price) / pos.entry_price * 100.0
            else:
                tp_dist_pct = (pos.entry_price - pos.take_profit) / pos.entry_price * 100.0
        threshold = max(1.5, 0.5 * tp_dist_pct) if tp_dist_pct > 0 else 2.0
        if net_pct >= threshold:
            if pos.side == "buy" and effective_sl < be:
                logger.info(
                    f"[Orders] BREAKEVEN MOVE: {pos.symbol} BUY — "
                    f"net profit {net_pct:.2f}%, moving SL {effective_sl:.6g} → {be:.6g}")
                return be
            elif pos.side == "sell" and effective_sl > be:
                logger.info(
                    f"[Orders] BREAKEVEN MOVE: {pos.symbol} SELL — "
                    f"net profit {net_pct:.2f}%, moving SL {effective_sl:.6g} → {be:.6g}")
                return be
        return effective_sl

    def accrue_paper_funding(self, exchange: BaseExchange):
        """
        DRY_RUN-only: at each 8h UTC funding boundary, charge funding on
        every held futures position for this exchange. LIVE futures accrue
        funding on the exchange side and settle automatically — paper
        previously ignored it entirely, making carry-heavy longs look free.

        We check once per call; the first call landing in a funding hour
        (00/08/16 UTC) after the last settlement triggers the charge.
        Idempotent: only one settlement per 8h window.
        """
        if not self.dry_run or not self.sim.funding_on:
            return
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        hour = now.hour
        if hour not in (0, 8, 16):
            return
        # Settled this window already?
        window_key = hour + now.day * 100 + now.month * 10000
        if self._last_funding_hour == window_key:
            return
        self._last_funding_hour = window_key

        positions = [p for p in self.tracker.get_open(exchange=exchange.name)
                     if p.market_type == "futures"]
        if not positions:
            return
        for pos in positions:
            try:
                # Current mark + funding rate in one shot
                rate = 0.0
                try:
                    fr = exchange.exchange.fetch_funding_rate(pos.symbol)
                    rate = float(fr.get("fundingRate") or 0.0)
                except Exception:
                    # Fallback median: 0.01% per 8h for BTC/ETH-class perps
                    rate = 0.0001
                try:
                    tk = exchange.fetch_ticker(pos.symbol, "futures")
                    mark = float(tk.get("last") or tk.get("close") or pos.entry_price)
                except Exception:
                    mark = pos.entry_price
                cost = self.sim.funding_payment(pos, mark, rate)
                if abs(cost) < 0.0001:
                    continue
                # Accumulate onto the position so attribution.record sees
                # total funding paid over the hold (positive = we paid).
                try:
                    pos.funding_paid = float(getattr(pos, "funding_paid", 0.0) or 0.0) + float(cost)
                except Exception:
                    pass
                # Apply to wallet: +cost = debit (wallet shrinks), -cost = credit.
                # Use the wallet's LOCKED apply_funding (audit 2026-06-04) instead of
                # mutating self.wallet._balances directly — the direct RMW raced the
                # on_open/on_close RMW on the same per-exchange key under the SL/TP daemon.
                name = exchange.name.lower()
                prev = self.wallet.balance(name)
                new_bal = self.wallet.apply_funding(name, cost)
                logger.info(
                    f"[SimFund] {exchange.name} {pos.symbol} {pos.side.upper()}: "
                    f"rate={rate*100:.4f}% cost={cost:+.4f} USDT "
                    f"→ bal {prev:.2f} → {new_bal:.2f}"
                )
            except Exception as e:
                logger.debug(f"[SimFund] {pos.symbol}: {e}")

    def check_sl_tp(self, exchange: BaseExchange, market_type: str = "spot"):
        # Funding settlement piggybacks on the monitor loop
        if self.dry_run and market_type == "futures":
            self.accrue_paper_funding(exchange)
        positions = self.tracker.get_open(exchange=exchange.name)
        for pos in positions:
            if pos.market_type != market_type:
                continue
            # Flag: exchange holds both SL and TP conditionals. We still
            # need to run trailing-stop advancement, age limits, hard max
            # loss, and entry-invalidation on these positions — only the
            # direct price-vs-SL/TP trigger checks are redundant (the
            # exchange fires those). Prior code `continue`-d here, which
            # silently skipped all local monitoring for LIVE futures
            # positions — trailing never advanced the SL, age limits
            # never fired, and the 3% hard-max-loss circuit breaker was
            # bypassed.
            _exchange_handles_sltp = (
                getattr(pos, '_exchange_sl', False)
                and getattr(pos, '_exchange_tp', False)
            )
            ticker = exchange.fetch_ticker(pos.symbol, market_type)
            price  = ticker.get("last")
            if not price:
                continue
            price = float(price)

            # ── SL reconciliation (2026-06-20) ──
            # Restore exchange-side SL if a prior placement failed and left the
            # position relying only on polled monitoring. No-op in paper / spot
            # and when the SL is already attached.
            self._reconcile_missing_sl(exchange, pos)

            # ── TP reconciliation (audit 2026-06-21 B7) ──
            # Restore an orphaned exchange-side TP (e.g. a venue-side cancel that
            # left _exchange_tp stale). No-op in paper / spot and when a TP is
            # already resting; throttled to once/minute/position.
            self._reconcile_missing_tp(exchange, pos)

            # ── DRY_RUN realism: intrabar wick SL/TP (2026-04-11) ──
            # LIVE exchange-side SL/TP orders trigger on wick price (high/low
            # within the candle). Paper polling `ticker.last` every ~N seconds
            # misses wicks that dipped below SL and recovered — survivorship
            # bias that turned profitable paper into losing live. Consult the
            # last 1m candle: if it touched SL/TP, fire the close at the
            # trigger level (not at `last`) so paper matches what LIVE would
            # have caught.
            if self.dry_run:
                wick_reason, wick_px = self.sim.check_wick_trigger(
                    exchange, pos.symbol, market_type, pos.side,
                    pos.stop_loss, pos.take_profit)
                if wick_reason == "stop_loss":
                    logger.warning(
                        f"[Orders] WICK STOP: {pos.symbol} {pos.side.upper()} "
                        f"1m wick touched SL={pos.stop_loss:.6g} "
                        f"(ticker last={price:.6g})")
                    self.close_position(
                        exchange, pos, "stop_loss", wick_px)
                    continue
                if wick_reason == "take_profit":
                    logger.info(
                        f"[Orders] WICK TP: {pos.symbol} {pos.side.upper()} "
                        f"1m wick touched TP={pos.take_profit:.6g} "
                        f"(ticker last={price:.6g})")
                    self.close_position(
                        exchange, pos, "take_profit", wick_px)
                    continue

            # ── TSMOM capital-preservation exit policy (Phase 2b) ───────
            # A position opened by the long-only TSMOM signal is HELD until its
            # own daily momentum-flip CLOSE (emitted from the portfolio cycle).
            # Suppress every scalp early-exit below (partial-TP, scalp wall,
            # trailing, breakeven, fixed TP, entry-staleness, age/stale) but KEEP
            # a wide disaster stop: the wick-stop above already enforced
            # pos.stop_loss (the ~8% entry stop) in paper; here we add the
            # all-modes hard-max-loss backstop and a LIVE polled SL trigger that
            # is independent of take_profit (so zeroing TP can't disable the stop,
            # the coupling the exit audit flagged). Then skip the scalp machinery.
            from core.tsmom_signal import is_tsmom_position as _is_tsmom_pos
            if _is_tsmom_pos(pos):
                from config import TSMOM_HARD_MAX_LOSS_PCT as _TSMOM_HML
                _t_net_pnl, _t_net_pct, _ = self._net_pnl_at_price(pos, price)
                if abs(_t_net_pct) >= _TSMOM_HML and _t_net_pnl < 0:
                    logger.error(
                        f"[Orders] TSMOM HARD MAX LOSS: {pos.symbol} "
                        f"{pos.side.upper()} @ {price:.4f} net={_t_net_pct:+.2f}% "
                        f"— disaster close")
                    self.close_position(exchange, pos, "hard_max_loss", price)
                    continue
                if (not self.dry_run and not _exchange_handles_sltp
                        and pos.stop_loss and float(pos.stop_loss) > 0
                        and pos.side == "buy" and price <= float(pos.stop_loss)):
                    logger.warning(
                        f"[Orders] TSMOM STOP LOSS: {pos.symbol} @ {price:.4f} "
                        f"(SL={float(pos.stop_loss):.4f})")
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                continue  # hold to the momentum-flip CLOSE; skip all scalp exits

            # ── B6 (audit 2026-06-21): PLANNED-TP-FIRST (flag-gated) ──────────
            # When near_target_exit is ON, the configured/planned TP is the FIRST
            # profit authority so a winner that has reached target is not pre-empted
            # by partial-TP / trailing / early-BE and clipped below it. Mirrors the
            # late TP check's guards (tp>0, not exchange-held) and reuses the
            # 'take_profit' exit_reason. Flag OFF => this block is skipped entirely,
            # so the dispatch order is byte-identical to today (the late TP check
            # still owns it). Only the TP half is hoisted — SL stays in its later
            # position so trailing/early-BE can still advance it first.
            if RISK.get("near_target_exit_enabled", False):
                _tp_ptf = pos.take_profit
                if _tp_ptf and float(_tp_ptf) > 0 and not _exchange_handles_sltp:
                    _hit = (pos.side == "buy" and price >= float(_tp_ptf)) or \
                           (pos.side == "sell" and price <= float(_tp_ptf))
                    if _hit:
                        _, _ntp_ptf, _ = self._net_pnl_at_price(pos, price)
                        logger.info(
                            f"[Orders] TAKE PROFIT (planned-first): {pos.symbol} "
                            f"{pos.side.upper()} @ {price:.4f} "
                            f"(TP={float(_tp_ptf):.4f}) net={_ntp_ptf:+.2f}%")
                        self.close_position(exchange, pos, "take_profit", price)
                        continue

            # ── PARTIAL TAKE PROFIT ──
            try:
                from config import PARTIAL_TP
                should_fire, take_sz, _level = _should_fire_partial_tp(
                    pos, price, PARTIAL_TP)
                if should_fire:
                    take_at = PARTIAL_TP.get("first_take_at_pct", 0.5)
                    logger.info(
                        f"[Orders] PARTIAL TP: {pos.symbol} {pos.side.upper()} "
                        f"@ {price:.4f} ({take_at:.0%} of TP)")
                    self.partial_close_position(
                        exchange, pos, take_sz, "partial_tp", price)
            except ImportError:
                pass

            # ── SCALP TIME WALL (v4: hard 60-min close) ──────────────
            try:
                from config import SCALP_MODE as _SM_tw
            except ImportError:
                _SM_tw = {}
            if _SM_tw.get("enabled", False):
                _is_scalp = getattr(pos, "_scalp", False) or (
                    hasattr(pos, "tp_pct") and pos.tp_pct and abs(float(pos.tp_pct) - _SM_tw.get("tp_pct", 1.3)) < 0.5
                )
                if _is_scalp:
                    _tw_min = _SM_tw.get("time_wall_min", 60)
                    _pos_age_min = 0
                    try:
                        if hasattr(pos, "open_time") and pos.open_time:
                            from datetime import datetime, timezone
                            _ot = pos.open_time if isinstance(pos.open_time, datetime) else datetime.fromisoformat(str(pos.open_time))
                            _pos_age_min = (datetime.now(timezone.utc) - _ot.replace(tzinfo=timezone.utc if _ot.tzinfo is None else _ot.tzinfo)).total_seconds() / 60
                    except Exception:
                        pass
                    if _pos_age_min >= _tw_min:
                        logger.warning(
                            f"[Orders] SCALP_TIME_WALL: {pos.symbol} age={_pos_age_min:.0f}m >= {_tw_min}m — force-closing")
                        self.close_position(exchange, pos, "scalp_time_wall")
                        continue

                    # Scalp stale close: flat positions at 45 min
                    _stale_min = _SM_tw.get("stale_close_min", 45)
                    _stale_profit = _SM_tw.get("stale_min_profit", 0.3)
                    if _pos_age_min >= _stale_min:
                        # Live fee-aware net. pos.pnl_pct is None on an OPEN
                        # position (only set at close, position_tracker.py:248),
                        # so the old read made `0.0 < stale_profit` ALWAYS true
                        # and force-closed EVERY aged scalp — winners included
                        # (audit 2026-06-21 H3). Use the same fee-aware net the
                        # AGE block uses and spare genuinely-profitable positions.
                        _stale_net_pnl, _net_pct, _ = self._net_pnl_at_price(
                            pos, price)
                        if _stale_net_pnl <= 0 or _net_pct < _stale_profit:
                            logger.info(
                                f"[Orders] SCALP_STALE: {pos.symbol} "
                                f"age={_pos_age_min:.0f}m net={_net_pct:+.2f}% "
                                f"pnl=${_stale_net_pnl:+.2f} < {_stale_profit}% — closing")
                            self.close_position(exchange, pos, "scalp_stale_close")
                            continue

            # ── TRAILING STOP ─────────────────────────────────────────
            # Skip trailing for scalp trades (v4: trailing clips winners at 0.67R)
            try:
                from config import SCALP_MODE as _SM_trail
            except ImportError:
                _SM_trail = {}
            _skip_trail_for_scalp = False
            if _SM_trail.get("enabled", False) and not _SM_trail.get("trailing_enabled", True):
                _is_scalp_trail = getattr(pos, "_scalp", False) or (
                    hasattr(pos, "tp_pct") and pos.tp_pct and abs(float(pos.tp_pct) - _SM_trail.get("tp_pct", 1.3)) < 0.5
                )
                if _is_scalp_trail:
                    _skip_trail_for_scalp = True

            if _skip_trail_for_scalp:
                updated_sl = pos.stop_loss
                should_trail = False
                trail_reason = "scalp_trail_skipped"
            else:
                should_trail, trail_reason, updated_sl = self.trailing.update(
                    pos, price
                )
            if should_trail:
                logger.info(
                    f"[Orders] TRAILING STOP: {pos.symbol} "
                    f"@ {price:.4f} (trail SL={updated_sl:.4f})"
                )
                self.close_position(exchange, pos, "trailing_stop", price)
                continue

            effective_sl = (
                max(updated_sl, pos.stop_loss)
                if pos.side == "buy"
                else min(updated_sl, pos.stop_loss)
            )

            # ── Early breakeven move: lock SL to breakeven once in profit ──
            effective_sl = self._early_breakeven_move(pos, price, effective_sl)

            # Persist trailing advance so it survives restarts
            # 2026-04-19 (Fix A2): when the exchange holds SL, also re-place
            # it. Previously the tracker advanced but the exchange kept
            # running the original (wider) SL → trailing was a ghost in live.
            sl_advanced = False
            if pos.side == "buy" and effective_sl > pos.stop_loss:
                pos.stop_loss = effective_sl
                sl_advanced = True
                with self.tracker._lock:
                    self.tracker._save()
            elif pos.side == "sell" and effective_sl < pos.stop_loss:
                pos.stop_loss = effective_sl
                sl_advanced = True
                with self.tracker._lock:
                    self.tracker._save()

            if (sl_advanced
                    and not self.dry_run
                    and getattr(pos, "_exchange_sl", False)
                    and pos.market_type == "futures"):
                try:
                    self._replace_exchange_sl(exchange, pos)
                except Exception as e:
                    logger.warning(
                        f"[Orders] Trailing SL re-place failed for "
                        f"{pos.symbol}: {str(e)[:120]}")

            # ── HARD MAX LOSS GATE — absolute circuit breaker ──
            # 2026-04-12: replaces ANTI-LOSS gate which WIDENED SL on MCP
            # advice, turning -3% losses into -16% catastrophes. No override,
            # no MCP consultation, no exceptions. This is the last line of
            # defense — if price moves 3% against you, you're OUT.
            net_pnl, net_pct, be = self._net_pnl_at_price(pos, price)
            if abs(net_pct) >= 3.0 and net_pnl < 0:
                logger.error(
                    f"[Orders] HARD MAX LOSS: {pos.symbol} {pos.side.upper()} "
                    f"@ {price:.4f} net={net_pct:+.2f}% — forced close, no override")
                self.close_position(exchange, pos, "hard_max_loss", price)
                continue

            # 2026-04-20: Guard against garbage SL/TP values (0.0, None, NaN).
            # Root cause of ALGO naked-short cascade — a position with
            # take_profit=0.0 fires TAKE_PROFIT on the first tick because
            # any positive price >= 0. Skip the SL/TP trigger *only* when
            # invalid, but let age-limit + hard-max-loss enforcement continue
            # below so an unprotected position isn't immortal.
            _tp_ok = pos.take_profit is not None and float(pos.take_profit) > 0
            _sl_ok = effective_sl is not None and float(effective_sl) > 0
            _sltp_valid = _tp_ok and _sl_ok
            if not _sltp_valid:
                logger.warning(
                    f"[Orders] Invalid SL/TP for {pos.symbol} "
                    f"(SL={effective_sl}, TP={pos.take_profit}) — "
                    f"skipping SL/TP trigger this cycle (age/hard-loss still active)")

            # Skip direct SL/TP price-trigger checks when the exchange
            # holds both conditionals — the exchange fires them on its own.
            # All OTHER monitoring (trailing, age limit, hard max loss,
            # entry invalidation) still runs above and below this block.
            if _sltp_valid and not _exchange_handles_sltp and pos.side == "buy":
                if price <= effective_sl:
                    # 2026-04-12: ANTI-LOSS gate REMOVED. SL hit = close.
                    # No widening, no MCP hold consultation. Discipline > hope.
                    logger.warning(
                        f"[Orders] STOP LOSS: {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif price >= pos.take_profit:
                    # 2026-04-16: MCP TP override REMOVED — always close at TP.
                    # The old "MCP says RIDE" gate collapsed R:R from 2.5:1 to 1.12:1
                    # by letting winning positions ride past TP, only to retrace and
                    # exit at trailing SL for a fraction of the planned profit.
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    logger.info(
                        f"[Orders] TAKE PROFIT: {pos.symbol} "
                        f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "take_profit", price)
                    continue

                # 2026-04-16 (post-audit): Proactive MCP TP-at-+2% REMOVED.
                # Together with the earlier TP override, this gate was exiting
                # winners at +2% (halfway to planned TP), collapsing realized
                # R:R to 0.74:1. Deterministic TP at pos.take_profit is the
                # sole authority for profit-taking; trailing stop handles the
                # retrace case, and MCP advice now only applies to exits below
                # the planned TP line (loss-cut / breakeven).

            elif _sltp_valid and not _exchange_handles_sltp and pos.side == "sell":
                if price >= effective_sl:
                    # 2026-04-12: ANTI-LOSS gate REMOVED for shorts too.
                    logger.warning(
                        f"[Orders] STOP LOSS (short): {pos.symbol} "
                        f"@ {price:.4f} (SL={effective_sl:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "stop_loss", price)
                    continue
                elif price <= pos.take_profit:
                    # 2026-04-16: MCP TP override REMOVED for shorts too.
                    _, net_pct, _ = self._net_pnl_at_price(pos, price)
                    logger.info(
                        f"[Orders] TAKE PROFIT (short): {pos.symbol} "
                        f"@ {price:.4f} (TP={pos.take_profit:.4f}) net={net_pct:+.2f}%"
                    )
                    self.close_position(exchange, pos, "take_profit", price)
                    continue

                # 2026-04-16 (post-audit): Proactive MCP TP-at-+2% REMOVED
                # for shorts too. Deterministic TP is sole authority.

            # ── ENTRY-STALENESS EXIT (2026-05-01 Tier 1.1) ──────────────
            # Re-check the directional hypothesis. If the 4h EMA20/50 has
            # flipped against the position (with margin), the entry rationale
            # is invalid — close at market BEFORE waiting for SL or AGE_LOSS.
            #
            # Why 4h EMA? It's the SIDE-determining signal in
            # mcp_brain._score_coin (line 1976: side='buy' if ema20 > ema50_4h).
            # If that flips, the trade has no hypothesis left.
            #
            # Why a margin (0.15%)? Avoids whipsaw on tight crosses. EMAs
            # touching the cross-line by 0.05% isn't a regime change.
            #
            # Why min_hold_minutes? Entries that fired right before a brief
            # 4h cross deserve the chance to resolve. 30min grace.
            #
            # Skips on transient errors (don't close on a fetch failure).
            try:
                from config import ENTRY_STALENESS_EXIT as _ES
            except ImportError:
                _ES = {"enabled": False}
            age_minutes_for_staleness = (pos.duration_minutes or 0)
            if (_ES.get("enabled", True)
                    and age_minutes_for_staleness >= int(_ES.get("min_hold_minutes", 30))
                    and pos.market_type == "futures"
                    and self.mcp_brain is not None):
                try:
                    # 2026-06-11 gap-flip semantics: pass the entry epoch so
                    # born-invalid positions are exempt. Knob off (or missing
                    # open_time) → entry_ts=0 → checker preserves OLD behavior.
                    _es_entry_ts = (
                        float(getattr(pos, "open_time", 0.0) or 0.0)
                        if _ES.get("require_flip_after_entry", True) else 0.0)
                    invalidated, reason = self.mcp_brain.is_entry_invalidated(
                        symbol=pos.symbol,
                        side=pos.side,
                        gap_pct=float(_ES.get("invalidation_gap_pct", 0.15)),
                        entry_ts=_es_entry_ts,
                    )
                    if invalidated:
                        logger.warning(
                            f"[Orders] ENTRY_STALE: {pos.symbol} {pos.side} "
                            f"opened {age_minutes_for_staleness:.0f}m ago, "
                            f"4h EMA hypothesis invalidated ({reason}) — closing")
                        self.close_position(
                            exchange, pos, "entry_invalidated", price)
                        continue
                    elif "born-invalid" in reason:
                        logger.debug(
                            f"[Orders] ENTRY_STALE exempt: {pos.symbol} "
                            f"{pos.side} ({reason})")
                except Exception as _ese:
                    logger.debug(
                        f"[Orders] entry-staleness check skipped ({_ese})")

            # ── POSITION AGE LIMIT ENFORCEMENT ──────────────────────────
            # Three rules, checked in order:
            #   1. AGE_LIMIT: open >= max_position_age_hours AND losing → force-close
            #   2. AGE_LOSS:  open >= max_loss_age_hours      AND net <= -max_loss_age_pct
            #                 (Phase 13.1, 2026-04-28). Closes the 2-4h hold-time bleed:
            #                 warehouse 30d data showed 55 trades / -$17.39 / 35% WR in
            #                 that bucket. Cuts losers before they slide further.
            #   3. STALE:     open >= max_stale_hours          AND PnL ≈ 0
            age_hours = (pos.duration_minutes or 0) / 60.0
            max_age_h = RISK.get("max_position_age_hours", 24)
            max_loss_age_h = RISK.get("max_loss_age_hours", 3.0)
            max_loss_age_pct = RISK.get("max_loss_age_pct", 0.5)
            max_stale_h = RISK.get("max_stale_hours", 4)
            # Patch #2 (2026-05-19): refit-driven per-tier AGE_LIMIT override.
            # scripts/refit_age_cutoffs.py writes data/models/age_cutoffs.json
            # ONLY when the 45d-fit / 15d-holdout split shows strict
            # improvement on the holdout. If the JSON is absent, we keep the
            # RISK config global (the "current cutoff" the refit would have
            # had to beat). If present, we resolve cutoff-by-tier via
            # pos.confidence (= mcp_score / 100, persisted at entry).
            _age_cutoffs = load_age_cutoffs()
            if _age_cutoffs and getattr(pos, "confidence", 0.0) > 0:
                _score = float(pos.confidence) * 100.0
                if _score >= 85 and "AGGRESSIVE" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["AGGRESSIVE"]) / 60.0
                elif _score >= 75 and "CONVICTION" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["CONVICTION"]) / 60.0
                elif _score >= 65 and "STANDARD" in _age_cutoffs:
                    max_age_h = float(_age_cutoffs["STANDARD"]) / 60.0
            net_pnl, net_pct, _ = self._net_pnl_at_price(pos, price)

            # Phase 41 (2026-05-10): AGE_LOSS DISABLED, AGE_LIMIT tightened.
            # 6 AGE_LOSS trades all-time, 0% WR, -$1.78. Rule realizes paper
            # losses (-0.5% to -3%) at 3h hold before SL backstop (-2.5%) gets
            # to fire OR position recovers. Math: recovery rate >13% makes
            # "no AGE_LOSS" net positive (avoids realizing -1.5% avg). With
            # 46% overall WR, recovery rate on stale losers is plausibly
            # 20-30% — trust the SL instead.
            # AGE_LIMIT (24h backstop) tightened: only fires when loss
            # >= 1.5% (was: any loss). Tiny -0.1% age-outs realized losses
            # on positions that just needed more time.
            AGE_LOSS_ENABLED = False
            AGE_LIMIT_MIN_LOSS_PCT = 1.5

            # 2026-05-19 Patch #3 — soft-close amplification wrapper.
            # `_try_soft_close` defers STALE / AGE_LIMIT / AGE_LOSS when
            # proximity to mcp_take_profit ≥ threshold; otherwise it
            # invokes the close immediately. The `_close` closure binds
            # the current exchange / pos / price so the helper only
            # needs the reason label.
            def _close(reason_label, _ex=exchange, _p=pos, _px=price):
                self.close_position(_ex, _p, reason_label, _px)
                return True

            # Populate the dynamic attrs the proximity scorer reads.
            # These match Patch #3 plan §5.5 — pos object is mutated so
            # `score_take_profit_proximity` has current mark + uPnL.
            pos.current_px = price
            pos.unrealized_pnl_pct = net_pct / 100.0

            if (age_hours >= max_age_h and net_pnl < 0
                    and abs(net_pct) >= AGE_LIMIT_MIN_LOSS_PCT):
                logger.warning(
                    f"[Orders] AGE_LIMIT: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (limit {max_age_h}h), "
                    f"net={net_pct:+.2f}% — force-closing losing position")
                _try_soft_close(self, pos, "AGE_LIMIT", proceed_fn=_close)
                continue
            elif (AGE_LOSS_ENABLED
                    and age_hours >= max_loss_age_h
                    and net_pct <= -max_loss_age_pct):
                logger.warning(
                    f"[Orders] AGE_LOSS: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (loss-age limit {max_loss_age_h}h), "
                    f"net={net_pct:+.2f}% — cutting before mid-hold bleed worsens")
                _try_soft_close(self, pos, "AGE_LOSS", proceed_fn=_close)
                continue
            elif age_hours >= max_stale_h and -0.3 <= net_pct <= 0.0:
                # 2026-05-28: only STALE-close flat/losing positions.
                # Profitable trades (net_pct > 0) should run to TP, not get
                # killed as "stale". The old range [-0.3, +0.3] was prematurely
                # closing winners (XRP +0.28%, SOL +0.05% killed today).
                logger.warning(
                    f"[Orders] STALE: {pos.symbol} {pos.side} "
                    f"open {age_hours:.1f}h (stale limit {max_stale_h}h), "
                    f"net={net_pct:+.2f}% — flat/slight loss, freeing capital")
                _try_soft_close(self, pos, "STALE", proceed_fn=_close)
                continue


# ─────────────────────────────────────────────────────────────────────
# 2026-05-19 Patch #3 — mcp_take_profit path amplification helper.
#
# Wraps soft-close call sites (STALE / AGE_LIMIT / AGE_LOSS) so that
# when a position is "close to firing TP" (proximity score
# ≥ config.MCP_TP_PROXIMITY_THRESHOLD) the soft-close is deferred by
# config.MCP_TP_GRACE_SEC seconds. After grace expires the original
# close fires with `_post_grace` suffix on the exit_reason.
#
# SL paths (stop_loss / sl_failed / sl_placement_failed) are NEVER
# gated — they always close immediately. SL is the last line of
# defense; amplifying it would defeat the circuit breaker.
#
# Grace state lives on `position._mcp_tp_grace_until` (set dynamically
# on the Position dataclass, same pattern as `_ghost_reroute_count` in
# Task 1). No module-level state — each position carries its own
# timer, so leak-across-positions is impossible.
# ─────────────────────────────────────────────────────────────────────
def _try_soft_close(order_manager, position, soft_reason, proceed_fn):
    """Soft-close decision gate.

    Args:
        order_manager: OrderManager instance (currently unused; kept for
            API symmetry with `_patch0_instrument_ghost` and so future
            callers can read config off the manager if needed).
        position: Position object with `_mcp_tp_grace_until` writable.
            Callers must have set `position.current_px` and
            `position.unrealized_pnl_pct` (fraction) before invoking,
            so `score_take_profit_proximity` has the data it needs.
        soft_reason: short label for the close path (e.g. "STALE",
            "AGE_LIMIT", "AGE_LOSS"). SL paths bypass entirely.
        proceed_fn: callable taking a reason-string. Should perform the
            close and return whatever the caller wants returned. This
            helper returns `proceed_fn(reason)` when closing.

    Returns:
        None when the close is deferred (still in grace, or first time
        proximity meets threshold). Otherwise the return value of
        `proceed_fn(reason)`. Reason is `f"{soft_reason}_post_grace"`
        on the post-grace fire path, otherwise the original
        `soft_reason`.
    """
    # Local imports keep ruff/isort happy and avoid circular-import
    # risk with `core.mcp_brain` (which transitively re-imports parts
    # of this module). `time` is already imported at module level but
    # we re-bind here for explicitness — proximity grace math reads
    # `_time.time()` once.
    import time as _time  # noqa: I001 — function-local by design
    import config as _cfg  # noqa: I001
    from core.mcp_brain import score_take_profit_proximity  # noqa: I001

    # Invariant: SL paths never gated.
    SL_REASONS = ("stop_loss", "sl_failed", "sl_placement_failed")
    if soft_reason in SL_REASONS:
        return proceed_fn(soft_reason)

    # Master kill-switch — flag off bypasses amplification entirely.
    if not getattr(_cfg, "MCP_TP_AMPLIFY_ENABLED", True):
        return proceed_fn(soft_reason)

    threshold = float(getattr(_cfg, "MCP_TP_PROXIMITY_THRESHOLD", 0.7))
    grace_sec = int(getattr(_cfg, "MCP_TP_GRACE_SEC", 1800))

    try:
        proximity = float(score_take_profit_proximity(position))
    except Exception:
        proximity = 0.0

    # Below threshold → close immediately, no defer.
    # BUT: if grace_until was previously set (proximity crossed threshold
    # earlier and we're now back below it), preserve the `_post_grace`
    # audit signal so sprint_kpi.py can count amplified closes correctly.
    # Without this, the close emits the bare `soft_reason` and the fact
    # that the position was ever deferred becomes invisible.
    if proximity < threshold:
        prior_grace = float(getattr(position, "_mcp_tp_grace_until", 0) or 0)
        if prior_grace > 0:
            try:
                logger.info(
                    f"[Orders] MCP_TP_AMPLIFY: proximity dropped below "
                    f"threshold for {getattr(position, 'symbol', '?')} "
                    f"after grace was set — firing "
                    f"{soft_reason}_post_grace")
            except Exception:
                pass
            return proceed_fn(f"{soft_reason}_post_grace")
        return proceed_fn(soft_reason)

    now = _time.time()
    grace_until = float(getattr(position, "_mcp_tp_grace_until", 0) or 0)

    # First time crossing threshold — set grace and defer.
    if grace_until <= 0:
        position._mcp_tp_grace_until = now + grace_sec
        try:
            logger.info(
                f"[Orders] MCP_TP_AMPLIFY: deferring {soft_reason} on "
                f"{getattr(position, 'symbol', '?')} "
                f"(prox={proximity:.2f}, grace={grace_sec}s)")
        except Exception:
            pass
        return None

    # Inside the grace window → still defer.
    if now < grace_until:
        return None

    # Grace expired → close with `_post_grace` suffix.
    try:
        logger.info(
            f"[Orders] MCP_TP_AMPLIFY: grace expired for "
            f"{getattr(position, 'symbol', '?')} — firing "
            f"{soft_reason}_post_grace")
    except Exception:
        pass
    return proceed_fn(f"{soft_reason}_post_grace")
