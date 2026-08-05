"""
core/order_mgmt/provenance.py — OrderManager _ProvenanceMixin mixin (Phase D4).
"""
import json
import time
import uuid

from loguru import logger

from utils.http_redaction import redact_http_debug as _redact_http_debug

class _ProvenanceMixin:
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

    @staticmethod
    def _provenance_venue(exchange_name: str) -> str:
        value = str(exchange_name or "").lower()
        for venue in ("binance", "bybit", "bitget"):
            if venue in value:
                return venue
        raise ValueError(f"unsupported execution venue: {exchange_name!r}")

    def _append_order_intent(
        self,
        exchange,
        symbol: str,
        market_type: str,
        side: str,
        size: float,
        decision_id: str | None,
        strategy: str,
        candidate_id: int | None,
        leverage: float,
        *,
        order_type: str = "market",
        limit_price: float | None = None,
        post_only: bool = False,
        parent_decision_id: str | None = None,
        confidence: float | None = None,
    ):
        """Write the logical order intent before any exposure can be created."""
        if not getattr(self, "enforce_event_provenance", False):
            return None
        try:
            from datetime import datetime, timezone

            from core.contracts import InstrumentId, MarketType, OrderIntent
            from core.warehouse import get_warehouse

            resolved_decision_id = str(decision_id or f"direct-{uuid.uuid4()}")
            instrument = InstrumentId(
                venue=self._provenance_venue(exchange.name),
                market=(
                    MarketType.PERPETUAL
                    if market_type == "futures"
                    else MarketType.SPOT
                ),
                canonical_symbol=symbol,
                exchange_symbol=symbol,
            )
            intent = OrderIntent(
                intent_id=f"intent-{uuid.uuid4()}",
                decision_id=resolved_decision_id,
                instrument=instrument,
                created_at=datetime.now(timezone.utc),
                side=side,
                order_type=order_type,
                quantity=size,
                limit_price=limit_price,
                post_only=post_only,
                context={
                    "strategy_id": str(strategy or "unassigned"),
                    "candidate_id": candidate_id,
                    "leverage": float(leverage),
                    "execution_policy": (
                        "paper_virtual_post_only"
                        if post_only
                        else "limit_then_market_or_paper_l2"
                    ),
                    "parent_decision_id": str(parent_decision_id or ""),
                    "confidence": confidence,
                },
            )
            get_warehouse().append_order_intent(intent)
            return intent
        except Exception as exc:
            self.last_open_reject = "order_intent_persistence_failed"
            logger.error(f"[Provenance] order intent write failed: {exc}")
            try:
                self.risk.latch_incident(
                    f"order intent persistence failed: {exc}",
                    category="execution",
                )
            except Exception:
                pass
            return False

    def _append_execution_event(
        self,
        intent,
        event_type: str,
        *,
        order: dict | None = None,
        quantity: float | None = None,
        price: float | None = None,
        reason: str | None = None,
        context: dict | None = None,
    ) -> bool:
        """Append an acknowledgement/fill/cancel/error for an order intent."""
        if intent is None:
            return True
        if intent is False:
            return False
        try:
            from datetime import datetime, timezone

            from core.contracts import ExecutionEvent, ExecutionEventType
            from core.warehouse import get_warehouse

            payload = order if isinstance(order, dict) else {}
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            client_order_id = (
                payload.get("_client_order_id")
                or payload.get("clientOrderId")
                or info.get("clientOrderId")
                or info.get("orderLinkId")
                or info.get("clientOid")
            )
            now = datetime.now(timezone.utc)
            event_kind = ExecutionEventType(event_type)
            event = ExecutionEvent(
                event_id=f"execution-{uuid.uuid4()}",
                intent_id=intent.intent_id,
                decision_id=intent.decision_id,
                instrument=intent.instrument,
                event_type=event_kind,
                occurred_at=now,
                received_at=now,
                venue_order_id=(
                    str(payload.get("id")) if payload.get("id") is not None else None
                ),
                client_order_id=(
                    str(client_order_id) if client_order_id is not None else None
                ),
                quantity=quantity,
                price=price,
                cumulative_quantity=quantity,
                rejection_reason=reason,
                context=context or {},
            )
            get_warehouse().append_execution_event(event)
            return True
        except Exception as exc:
            logger.error(f"[Provenance] execution event write failed: {exc}")
            try:
                self.risk.latch_incident(
                    f"execution event persistence failed: {exc}",
                    category="execution",
                )
            except Exception:
                pass
            return False

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

