"""
core/smart_executor.py — Smart Order Execution Engine

Replaces blind market orders with intelligent execution:

1. LIMIT ORDER + TIMEOUT — Place limit at best bid/ask, wait up to N seconds,
   then fall back to market if not filled. Saves 50-80% on taker fees.

2. ENTRY TIMING — After signal, wait for a small pullback before entering.
   E.g., on a buy signal, wait for price to dip 0.1-0.3% from signal price.
   Improves average entry by 0.1-0.5%.

3. TWAP — For larger positions (>$500), split into 3-5 smaller orders
   executed over 30-120 seconds to reduce market impact.

4. SPREAD CHECK — Skip entry if bid-ask spread is too wide (illiquid market).

Estimated edge: 0.1-0.3% per trade in execution improvement.
On 100 trades/month, that's 10-30% additional annual return.
"""

import json
import time
from pathlib import Path

from loguru import logger

EXEC_STATS_FILE = Path("data/execution_stats.json")


class SmartExecutor:

    def __init__(self):
        self.limit_timeout_sec = 15     # wait 15s for limit fill
        self.pullback_wait_sec = 30     # wait up to 30s for pullback
        self.pullback_pct = 0.002       # 0.2% pullback target
        self.max_spread_pct = 0.003     # skip if spread > 0.3%
        self.twap_threshold_usd = 500   # TWAP for orders > $500
        self.twap_slices = 3            # split into 3 sub-orders
        self.twap_interval_sec = 10     # 10s between slices
        self._stats = self._load_stats()

    def check_spread(self, exchange, symbol: str,
                     market_type: str = "spot") -> dict:
        """
        Check bid-ask spread. Returns dict with spread info.
        If spread is too wide, signals should be skipped.
        """
        try:
            orderbook = exchange.fetch_order_book(symbol, limit=5)
            if not orderbook or not orderbook.get("bids") or not orderbook.get("asks"):
                return {"ok": True, "spread_pct": 0, "bid": 0, "ask": 0}

            best_bid = float(orderbook["bids"][0][0])
            best_ask = float(orderbook["asks"][0][0])
            mid = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid if mid > 0 else 0

            ok = spread_pct <= self.max_spread_pct

            if not ok:
                self._record_stat("spread_rejects")
                logger.info(
                    f"[Executor] {symbol}: spread {spread_pct*100:.3f}% > "
                    f"{self.max_spread_pct*100:.1f}% — too wide, skip")

            return {
                "ok": ok,
                "spread_pct": spread_pct,
                "bid": best_bid,
                "ask": best_ask,
                "mid": mid,
            }
        except Exception as e:
            logger.debug(f"[Executor] Spread check {symbol}: {e}")
            return {"ok": True, "spread_pct": 0, "bid": 0, "ask": 0}

    def get_entry_price(self, exchange, symbol: str, side: str,
                        market_type: str = "spot") -> float:
        """
        Get the best entry price. For buys, use the best ask (or slightly below).
        For sells, use the best bid (or slightly above).

        This is used for limit order placement.
        """
        try:
            orderbook = exchange.fetch_order_book(symbol, limit=5)
            if not orderbook:
                return 0

            if side == "buy":
                # Place limit at best bid + small offset (between bid and ask)
                best_bid = float(orderbook["bids"][0][0])
                best_ask = float(orderbook["asks"][0][0])
                # Aim for 30% into the spread (closer to bid = better price)
                price = best_bid + (best_ask - best_bid) * 0.3
                return price
            else:
                best_bid = float(orderbook["bids"][0][0])
                best_ask = float(orderbook["asks"][0][0])
                # For sells, aim 30% into spread from ask side
                price = best_ask - (best_ask - best_bid) * 0.3
                return price
        except Exception:
            return 0

    def execute_limit_with_fallback(self, exchange, symbol: str,
                                     side: str, amount: float,
                                     market_type: str = "spot",
                                     params: dict = None) -> dict:
        """
        Place a limit order at an aggressive price. If not filled within
        timeout, cancel and execute at market.

        2026-05-03 (Phase 15): when config.MAKER_ONLY.enabled is True,
        adds postOnly to the limit order and on timeout cancels + returns
        a skip dict instead of falling back to market — preserving the
        maker fee at the cost of a missed entry.

        Returns the fill result dict, or None on failure.
        """
        # Copy caller's params and strip any clientOrderId/newClientOrderId.
        # This path may fire up to 2 physical HTTP create_order calls
        # (limit, then market fallback). Bybit reserves orderLinkId even
        # after cancellation, so reusing the same ID yields retCode 110072
        # "OrderLinkedID is duplicate". base.create_order's setdefault()
        # will assign a fresh UUID per call when no ID is present.
        params = dict(params) if params else {}
        params.pop("clientOrderId", None)
        params.pop("newClientOrderId", None)

        # Maker-only mode: tag the limit as postOnly + extend wait window
        try:
            from config import MAKER_ONLY as _MO
        except ImportError:
            _MO = {"enabled": False, "max_wait_sec": 120}
        _maker_only = bool(_MO.get("enabled"))
        if _maker_only:
            # postOnly boolean ONLY — ccxt translates it per venue
            # (Binance USD-M wants timeInForce=GTX, Bybit v5 wants
            # timeInForce=PostOnly). The previous explicit
            # timeInForce="PostOnly" string passed raw was Bybit-only
            # vocabulary and would break Binance live maker orders
            # (latent: PAPER bypasses this path; found 2026-06-11).
            params["postOnly"] = True

        entry_price = self.get_entry_price(exchange, symbol, side, market_type)
        if entry_price <= 0:
            # Fallback to market immediately
            logger.debug(f"[Executor] {symbol}: no orderbook, using market order")
            return self._market_order(exchange, symbol, side, amount,
                                       market_type, params)

        # Place limit order
        try:
            logger.info(
                f"[Executor] {symbol}: limit {side.upper()} {amount:.8f} "
                f"@ {entry_price:.6f} (timeout={self.limit_timeout_sec}s)")

            order = exchange.create_order(
                symbol=symbol,
                order_type="limit",
                side=side,
                amount=amount,
                price=entry_price,
                params=params,
                market_type=market_type,
            )
            order_id = order.get("id")

            if not order_id:
                return self._market_order(exchange, symbol, side, amount,
                                           market_type, params)

            # Wait for fill — extended window in MAKER_ONLY mode
            wait_window = (
                int(_MO.get("max_wait_sec", 120)) if _maker_only
                else self.limit_timeout_sec
            )
            start = time.time()
            while time.time() - start < wait_window:
                time.sleep(1)
                try:
                    status = exchange.exchange.fetch_order(order_id, symbol)
                    if status.get("status") == "closed":
                        fill_price = float(status.get("average", entry_price))
                        logger.info(
                            f"[Executor] {symbol}: limit FILLED @ {fill_price:.6f} "
                            f"(saved vs market)")
                        self._record_stat("limit_fills")
                        if isinstance(status, dict):
                            status["_fill_type"] = "maker"
                        return status
                except Exception:
                    pass

            # Timeout — try to cancel, then fall back to market.
            #
            # 2026-05-02 BUG FIX (Bybit double-fill):
            # Pre-fix, this swallowed cancel errors silently and ALWAYS
            # placed a market order. But "order not exists or too late to
            # cancel" (Bybit retCode 110001 etc.) means the limit order
            # ALREADY FILLED in the timeout window. Placing a market on
            # top doubled the position size, then SL placement saw too
            # many open positions and fail-closed. Net result: the bot
            # had a phantom "unprotected" extra fill that the
            # ghost-reconciler then re-imported as a manual position.
            #
            # New behavior:
            #   1. Try to cancel the limit order
            #   2. ALWAYS re-fetch the order's true status from exchange
            #   3. If status == 'closed' / fully filled → return it,
            #      DO NOT place market (would be a double-fill)
            #   4. If status == 'canceled' or unknown → proceed to market
            cancel_failed = False
            try:
                exchange.cancel_order(order_id, symbol)
            except Exception as cancel_err:
                cancel_failed = True
                logger.debug(
                    f"[Executor] {symbol}: cancel raised "
                    f"({str(cancel_err)[:80]}) — verifying actual fill state")

            # Re-check the order's true status. If it filled during the
            # timeout window, return without placing a market order.
            filled_qty = 0.0
            status = {}
            try:
                status = exchange.exchange.fetch_order(order_id, symbol)
                state = (status.get("status") or "").lower()
                filled_qty = float(status.get("filled") or 0)
                if state == "closed" or (filled_qty > 0 and filled_qty >= amount * 0.95):
                    fill_price = float(status.get("average") or status.get("price") or entry_price)
                    logger.info(
                        f"[Executor] {symbol}: limit FILLED in timeout window "
                        f"@ {fill_price:.6f} (cancel was {'rejected' if cancel_failed else 'accepted'} "
                        f"because order already filled) — NO market fallback")
                    self._record_stat("limit_fills")
                    if isinstance(status, dict):
                        status["_fill_type"] = "maker"
                    return status
            except Exception as verify_err:
                # Verification failed too — fall through to market only if
                # we got a CLEAN cancel (the order's gone for sure).
                if cancel_failed:
                    logger.warning(
                        f"[Executor] {symbol}: cancel failed AND verification "
                        f"failed ({str(verify_err)[:80]}) — refusing to market "
                        f"to avoid double-fill. Operator should reconcile.")
                    return {"status": "uncertain", "id": order_id,
                            "symbol": symbol, "amount": amount,
                            "_executor_warning": "cancel+verify both failed"}

            # Partial fill below the 95% "treat as filled" bar above: a real,
            # smaller position now exists (cancel removed only the unfilled
            # remainder). Report it as a fill so the CALLER sizes the SL to the
            # actual fill — NEVER skip while a naked partial sits on the book.
            # Mode-agnostic: also stops a taker market top-up from double-filling
            # on top of a partial.
            if filled_qty > 0:
                fill_price = float(
                    status.get("average") or status.get("price") or entry_price)
                logger.warning(
                    f"[Executor] {symbol}: partial fill {filled_qty}/{amount} at "
                    f"timeout — reporting partial_maker (caller sizes SL to fill).")
                self._record_stat("maker_partial_fill")
                return {"status": "partial_maker", "id": order_id,
                        "symbol": symbol, "amount": filled_qty, "filled": filled_qty,
                        "average": fill_price, "price": fill_price,
                        "_fill_type": "maker_partial",
                        "_executor_warning": "partial_fill_at_timeout"}

            if _maker_only:
                logger.info(
                    f"[Executor] {symbol}: maker-only timeout, cancelled "
                    f"— SKIP entry (no market fallback per MAKER_ONLY)")
                self._record_stat("maker_only_skipped")
                return {"status": "skipped_maker_only", "id": order_id,
                        "symbol": symbol, "amount": amount,
                        "_executor_warning": "maker_only_timeout"}
            logger.info(
                f"[Executor] {symbol}: limit timeout, cancelled → market order")
            return self._market_order(exchange, symbol, side, amount,
                                       market_type, params)

        except Exception as e:
            logger.debug(f"[Executor] Limit order failed {symbol}: {e}")
            if _maker_only:
                logger.info(
                    f"[Executor] {symbol}: limit-place failed under MAKER_ONLY "
                    f"— SKIP entry (no market fallback)")
                self._record_stat("maker_only_skipped")
                return {"status": "skipped_maker_only", "id": None,
                        "symbol": symbol, "amount": amount,
                        "_executor_warning": "maker_only_place_failed"}
            return self._market_order(exchange, symbol, side, amount,
                                       market_type, params)

    def execute_twap(self, exchange, symbol: str, side: str,
                     total_amount: float, market_type: str = "spot",
                     params: dict = None) -> list:
        """
        TWAP execution: split large order into smaller slices
        executed over time to reduce market impact.
        """
        # Strip clientOrderId/newClientOrderId — each slice (and each
        # limit+market fallback inside a slice) must get its own fresh
        # UUID or Bybit rejects with 110072 "OrderLinkedID is duplicate".
        params = dict(params) if params else {}
        params.pop("clientOrderId", None)
        params.pop("newClientOrderId", None)

        slice_amount = total_amount / self.twap_slices
        results = []

        self._record_stat("twap_orders")
        logger.info(
            f"[Executor] TWAP {symbol}: {side.upper()} {total_amount:.8f} "
            f"in {self.twap_slices} slices of {slice_amount:.8f}")

        for i in range(self.twap_slices):
            try:
                result = self.execute_limit_with_fallback(
                    exchange, symbol, side, slice_amount,
                    market_type, params)
                if result:
                    results.append(result)
                    logger.debug(
                        f"[Executor] TWAP slice {i+1}/{self.twap_slices} filled")

                if i < self.twap_slices - 1:
                    time.sleep(self.twap_interval_sec)
            except Exception as e:
                logger.debug(f"[Executor] TWAP slice {i+1} failed: {e}")

        return results

    def should_use_twap(self, notional_usd: float) -> bool:
        """Return True if the order is large enough to warrant TWAP."""
        return notional_usd >= self.twap_threshold_usd

    def wait_for_pullback(self, exchange, symbol: str, side: str,
                           signal_price: float,
                           market_type: str = "spot") -> float:
        """
        After a signal, wait briefly for a better entry price.
        For buys: wait for price to dip below signal_price.
        For sells: wait for price to pop above signal_price.

        Returns the best price seen, or signal_price if no pullback.
        """
        target = signal_price * (1 - self.pullback_pct) if side == "buy" \
            else signal_price * (1 + self.pullback_pct)

        best_price = signal_price
        start = time.time()

        while time.time() - start < self.pullback_wait_sec:
            try:
                ticker = exchange.fetch_ticker(symbol, market_type)
                current = float(ticker.get("last", signal_price))

                if side == "buy":
                    if current < best_price:
                        best_price = current
                    if current <= target:
                        logger.info(
                            f"[Executor] {symbol}: pullback caught! "
                            f"{signal_price:.6f} → {current:.6f} "
                            f"({(signal_price-current)/signal_price*100:.2f}% better)")
                        return current
                else:
                    if current > best_price:
                        best_price = current
                    if current >= target:
                        logger.info(
                            f"[Executor] {symbol}: pullback caught! "
                            f"{signal_price:.6f} → {current:.6f} "
                            f"({(current-signal_price)/signal_price*100:.2f}% better)")
                        return current

                time.sleep(2)
            except Exception:
                time.sleep(2)

        # No pullback within timeout — use best price seen
        improvement = abs(best_price - signal_price) / signal_price * 100
        if improvement > 0.01:
            logger.debug(
                f"[Executor] {symbol}: partial pullback {improvement:.2f}%")
        return best_price

    def _market_order(self, exchange, symbol, side, amount,
                      market_type, params):
        """Fallback market order."""
        try:
            order = exchange.create_order(
                symbol=symbol,
                order_type="market",
                side=side,
                amount=amount,
                params=params,
                market_type=market_type,
            )
            self._record_stat("market_fallbacks")
            if isinstance(order, dict):
                order["_fill_type"] = "taker"
            return order
        except Exception as e:
            logger.error(f"[Executor] Market order failed {symbol}: {e}")
            return None

    # ── Stats tracking ───────────────────────────────────────────────

    def _record_stat(self, key: str, value: float = 1.0):
        """Increment a stat counter and save."""
        self._stats[key] = self._stats.get(key, 0) + value
        self._stats["total_orders"] = (
            self._stats.get("limit_fills", 0) +
            self._stats.get("market_fallbacks", 0)
        )
        self._save_stats()

    def _load_stats(self) -> dict:
        try:
            if EXEC_STATS_FILE.exists():
                return json.loads(EXEC_STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {
            "total_orders": 0, "limit_fills": 0,
            "market_fallbacks": 0, "twap_orders": 0,
            "spread_rejects": 0, "avg_slippage_pct": 0,
            "estimated_fee_savings": 0,
        }

    def _save_stats(self):
        try:
            EXEC_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            EXEC_STATS_FILE.write_text(
                json.dumps(self._stats, indent=2), encoding="utf-8")
        except Exception:
            pass
