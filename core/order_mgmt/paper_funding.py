"""
core/order_mgmt/paper_funding.py — OrderManager _PaperFundingMixin mixin (Phase D4).
"""
import time

from loguru import logger

from core.position_tracker import Position
from exchanges.base import BaseExchange

class _PaperFundingMixin:
    @staticmethod
    def _paper_funding_mark_at(exchange: BaseExchange, pos: Position,
                               settlement_ts: float) -> float:
        """Historical mark nearest one realized settlement, or zero."""
        try:
            fetcher = getattr(exchange.exchange, "fetch_mark_ohlcv", None)
            if not callable(fetcher):
                return 0.0
            since_ms = int((float(settlement_ts) - 60.0) * 1000.0)
            rows = fetcher(pos.symbol, "1m", since=since_ms, limit=3) or []
            candidates = []
            for row in rows:
                row_ts = float(row[0]) / 1000.0
                mark = float(row[4])
                if mark > 0 and abs(row_ts - float(settlement_ts)) <= 120.0:
                    candidates.append((abs(row_ts - float(settlement_ts)), mark))
            return min(candidates)[1] if candidates else 0.0
        except (AttributeError, IndexError, TypeError, ValueError):
            return 0.0

    def _paper_funding_live_settlements(
        self, exchange: BaseExchange, pos, start_ts: float, end_ts: float
    ):
        """Venue-realized funding rows fetched live when the local CSV store
        lacks coverage (missing file or stale tail — measured 2026-08-03:
        binance ETC/FET had no store file and accrual was silently inert).
        Attempts are throttled per (venue, base) so the ~12s monitor tick
        cannot hammer the API; a failed or empty fetch returns None and the
        caller defers the charge, exactly like a missing store."""
        from core.funding_history import FundingSettlement

        base = str(pos.symbol).split("/", 1)[0].upper()
        key = (str(exchange.name).lower(), base)
        attempts = getattr(self, "_simfund_live_attempt_at", None)
        if attempts is None:
            attempts = self._simfund_live_attempt_at = {}
        now = time.time()
        last = attempts.get(key)
        if last is not None and now - last < 900.0:
            return None
        attempts[key] = now
        client = getattr(exchange, "exchange", None)
        if client is None or not hasattr(client, "fetch_funding_rate_history"):
            return None
        try:
            raw = client.fetch_funding_rate_history(
                pos.symbol, since=int(start_ts * 1000), limit=50
            )
        except Exception as e:
            logger.debug(
                f"[SimFund] live funding history fetch failed for "
                f"{pos.symbol}: {e}"
            )
            return None
        # Only a real sequence of mappings is usable. A venue client that
        # returns None, an error object, or anything non-iterable must defer
        # the charge — never raise, because this runs inside the SL/TP monitor
        # loop and an exception here would stop protecting the position.
        if not isinstance(raw, (list, tuple)):
            return None
        out = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            try:
                ts = float(row.get("timestamp") or 0.0) / 1000.0
                rate = float(row.get("fundingRate"))
            except (TypeError, ValueError):
                continue
            if start_ts <= ts <= end_ts:
                out.append(FundingSettlement(settlement_ts=ts, rate=rate))
        if not out:
            return None
        return tuple(sorted(out, key=lambda s: s.settlement_ts))

    def accrue_paper_funding(self, exchange: BaseExchange):
        """Accrue authoritative realized funding settlements exactly once.

        Settlement timestamps and rates come from the per-venue realized
        history, so variable intervals and monitor/restart gaps are handled
        without inventing rates.  Missing history or a missing historical mark
        defers the charge and leaves the cursor unchanged.
        """
        if not self.dry_run or not self.sim.funding_on:
            return
        from core.funding_history import load_realized_settlements

        now_ts = time.time()
        positions = [
            pos for pos in self.tracker.get_open(exchange=exchange.name)
            if pos.market_type == "futures"
        ]
        dirty = False
        for pos in positions:
            base = str(pos.symbol).split("/", 1)[0].upper()
            cursor = float(getattr(pos, "last_funding_ts", 0.0) or 0.0)
            start_ts = max(float(pos.open_time or 0.0), cursor) + 0.001
            settlements = load_realized_settlements(
                str(exchange.name),
                base,
                start_ts=start_ts,
                end_ts=now_ts,
            )
            overdue = (now_ts - start_ts) > 9 * 3600
            if settlements is None or (not settlements and overdue):
                # Local store has no file for this (venue, base) OR its tail is
                # stale (zero rows in an overdue window — funding_history.py:73
                # says callers must treat either as unavailable; the empty case
                # was previously silent and left accrual inert for weeks).
                # Fall back to the venue's own realized history — the same
                # endpoint the backfill script uses, so no rate is fabricated.
                settlements = self._paper_funding_live_settlements(
                    exchange, pos, start_ts, now_ts
                )
            if settlements is None:
                wkey = (str(exchange.name).lower(), base)
                warned = getattr(self, "_simfund_warned_at", None)
                if warned is None:
                    warned = self._simfund_warned_at = {}
                if now_ts - warned.get(wkey, 0.0) >= 3600.0:
                    warned[wkey] = now_ts
                    logger.warning(
                        f"[SimFund] {exchange.name} {pos.symbol}: realized "
                        "funding history unavailable (local store and live "
                        "fetch); no rate fabricated"
                    )
                continue
            for settlement in settlements:
                mark = self._paper_funding_mark_at(
                    exchange, pos, settlement.settlement_ts
                )
                if mark <= 0:
                    logger.warning(
                        f"[SimFund] {exchange.name} {pos.symbol}: historical mark "
                        f"unavailable at {settlement.settlement_ts:.3f}; accrual deferred"
                    )
                    break
                cost = float(self.sim.funding_payment(pos, mark, settlement.rate))
                if abs(cost) >= 0.0001:
                    name = str(exchange.name).lower()
                    previous = self.wallet.balance(name)
                    new_balance = self.wallet.apply_funding(name, cost)
                    pos.funding_paid = (
                        float(getattr(pos, "funding_paid", 0.0) or 0.0) + cost
                    )
                    logger.info(
                        f"[SimFund] {exchange.name} {pos.symbol} {pos.side.upper()}: "
                        f"settled={settlement.settlement_ts:.3f} "
                        f"rate={settlement.rate * 100:.4f}% cost={cost:+.4f} USDT "
                        f"-> bal {previous:.2f} -> {new_balance:.2f}"
                    )
                pos.last_funding_ts = settlement.settlement_ts
                dirty = True
        if dirty:
            save = getattr(self.tracker, "_save", None)
            if callable(save):
                save()

