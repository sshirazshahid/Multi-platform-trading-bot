"""
core/order_mgmt/funds.py — OrderManager _FundsMixin mixin (Phase D4).
"""
import uuid

from loguru import logger

from exchanges.base import BaseExchange

class _FundsMixin:
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
            outcome, fill_sz = _FundsMixin._interpret_execution_result(
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

