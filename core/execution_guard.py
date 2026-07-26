"""Venue-pinned order-book validation for new exposure.

The guard consumes the same final quantity that will be submitted to the
exchange.  It is intentionally independent of signal generation so paper and
live execution share identical market-data denial rules.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from core.contracts import (
    InstrumentId,
    MarketDataKind,
    MarketDatum,
    MarketSnapshot,
    MarketType,
)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("numeric value is missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("numeric value is invalid") from exc
    if not result.is_finite():
        raise ValueError("numeric value must be finite")
    return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_time(book: Mapping[str, Any], received_at: datetime) -> tuple[datetime, bool]:
    raw = book.get("timestamp")
    if raw is None:
        return received_at, False
    value = _decimal(raw)
    # CCXT timestamps are milliseconds.  Accept seconds for deterministic
    # fixtures and explicitly normalize rather than guessing downstream.
    seconds = value / (Decimal(1000) if value >= Decimal("100000000000") else Decimal(1))
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc), True


def _levels(raw: Any, *, descending: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("book side is missing")
    levels: list[tuple[Decimal, Decimal]] = []
    prior: Decimal | None = None
    for level in raw:
        if not isinstance(level, Sequence) or len(level) < 2:
            raise ValueError("book level is malformed")
        price, quantity = _decimal(level[0]), _decimal(level[1])
        if price <= 0 or quantity <= 0:
            raise ValueError("book levels must be positive")
        if prior is not None:
            ordered = price <= prior if descending else price >= prior
            if not ordered:
                raise ValueError("book levels are out of order")
        levels.append((price, quantity))
        prior = price
    if not levels:
        raise ValueError("book side is empty")
    return tuple(levels)


def _walk(
    levels: tuple[tuple[Decimal, Decimal], ...], requested: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    remaining = requested
    filled = Decimal(0)
    quote_cost = Decimal(0)
    for price, available in levels:
        take = min(available, remaining)
        quote_cost += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        return filled, quote_cost, Decimal(0)
    return filled, quote_cost, quote_cost / filled


@dataclass(frozen=True)
class ExecutionBookDecision:
    allowed: bool
    reason: str
    requested_quantity: Decimal
    filled_quantity: Decimal = Decimal(0)
    quote_cost: Decimal = Decimal(0)
    vwap: Decimal = Decimal(0)
    mid: Decimal = Decimal(0)
    best_bid: Decimal = Decimal(0)
    best_ask: Decimal = Decimal(0)
    spread_bps: Decimal = Decimal(0)
    slippage_bps: Decimal = Decimal(0)
    snapshot: MarketSnapshot | None = None

    def to_action_dict(self) -> dict[str, Any]:
        payload = {
            "allowed": self.allowed,
            "reason": self.reason,
            "requested_quantity": str(self.requested_quantity),
            "filled_quantity": str(self.filled_quantity),
            "quote_cost": str(self.quote_cost),
            "vwap": str(self.vwap),
            "mid": str(self.mid),
            "best_bid": str(self.best_bid),
            "best_ask": str(self.best_ask),
            "spread_bps": str(self.spread_bps),
            "slippage_bps": str(self.slippage_bps),
        }
        if self.snapshot is not None:
            payload["snapshot"] = self.snapshot.to_dict()
        return payload


def validate_execution_book(
    book: Mapping[str, Any] | None,
    *,
    venue: str,
    market_type: str,
    canonical_symbol: str,
    exchange_symbol: str,
    side: str,
    requested_quantity: Any,
    max_slippage_bps: Any,
    max_age_seconds: float = 5.0,
    max_future_skew_seconds: float = 2.0,
    received_at: datetime | None = None,
) -> ExecutionBookDecision:
    """Validate and walk one venue's execution book, failing closed."""
    try:
        requested = _decimal(requested_quantity)
    except ValueError:
        requested = Decimal(0)
    if requested <= 0:
        return ExecutionBookDecision(False, "invalid_requested_quantity", requested)
    try:
        slippage_limit = _decimal(max_slippage_bps)
    except ValueError:
        return ExecutionBookDecision(False, "invalid_slippage_limit", requested)
    if slippage_limit < 0 or max_age_seconds <= 0 or max_future_skew_seconds < 0:
        return ExecutionBookDecision(False, "invalid_guard_configuration", requested)
    normalized_side = str(side).strip().lower()
    if normalized_side not in {"buy", "sell"}:
        return ExecutionBookDecision(False, "invalid_side", requested)
    if not isinstance(book, Mapping):
        return ExecutionBookDecision(False, "order_book_unavailable", requested)

    received = received_at or _utc_now()
    if received.tzinfo is None or received.utcoffset() is None:
        return ExecutionBookDecision(False, "receipt_time_not_utc", requested)
    received = received.astimezone(timezone.utc)
    if book.get("timestamp") is None:
        return ExecutionBookDecision(False, "order_book_timestamp_missing", requested)

    try:
        instrument = InstrumentId(
            venue=venue,
            market=MarketType.PERPETUAL if market_type == "futures" else market_type,
            canonical_symbol=canonical_symbol,
            exchange_symbol=exchange_symbol,
        )
        bids = _levels(book.get("bids"), descending=True)
        asks = _levels(book.get("asks"), descending=False)
        observed, event_time_known = _event_time(book, received)
    except (TypeError, ValueError, OverflowError, OSError):
        return ExecutionBookDecision(False, "order_book_invalid", requested)

    if observed > received + timedelta(seconds=max_future_skew_seconds):
        return ExecutionBookDecision(False, "order_book_clock_skew", requested)
    age_seconds = max(0.0, (received - observed).total_seconds())
    if age_seconds > max_age_seconds:
        return ExecutionBookDecision(False, "order_book_stale", requested)

    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        return ExecutionBookDecision(
            False,
            "order_book_crossed",
            requested,
            best_bid=best_bid,
            best_ask=best_ask,
        )

    mid = (best_bid + best_ask) / Decimal(2)
    spread_bps = (best_ask - best_bid) / mid * Decimal(10_000)
    selected = asks if normalized_side == "buy" else bids
    filled, quote_cost, vwap = _walk(selected, requested)
    if filled < requested:
        return ExecutionBookDecision(
            False,
            "insufficient_order_book_depth",
            requested,
            filled_quantity=filled,
            quote_cost=quote_cost,
            mid=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_bps=spread_bps,
        )

    if normalized_side == "buy":
        slippage_bps = (vwap - mid) / mid * Decimal(10_000)
    else:
        slippage_bps = (mid - vwap) / mid * Decimal(10_000)

    sequence = book.get("nonce")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        sequence = None
    raw_source = book.get("source")
    source = (
        str(raw_source).strip()
        if isinstance(raw_source, str) and raw_source.strip()
        else f"{instrument.venue.value}.rest.order_book"
    )
    digest_input = "|".join(
        (
            source,
            instrument.market.value,
            instrument.canonical_symbol,
            instrument.exchange_symbol,
            observed.isoformat(),
            str(sequence),
            str(best_bid),
            str(best_ask),
            str(requested),
        )
    )
    snapshot_id = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    data = (
        MarketDatum(
            instrument=instrument,
            kind=MarketDataKind.BID_PRICE,
            value=best_bid,
            observed_at=observed,
            received_at=received,
            source=source,
            sequence=sequence,
        ),
        MarketDatum(
            instrument=instrument,
            kind=MarketDataKind.ASK_PRICE,
            value=best_ask,
            observed_at=observed,
            received_at=received,
            source=source,
            sequence=sequence,
        ),
    )
    snapshot = MarketSnapshot(
        snapshot_id=snapshot_id,
        instrument=instrument,
        observed_at=observed,
        received_at=received,
        data=data,
        source=source,
        sequence=sequence,
        context={
            "event_time_known": event_time_known,
            "age_seconds": age_seconds,
            "side": normalized_side,
            "requested_quantity": str(requested),
            "filled_quantity": str(filled),
            "quote_cost": str(quote_cost),
            "vwap": str(vwap),
            "spread_bps": str(spread_bps),
            "slippage_bps": str(slippage_bps),
        },
    )
    allowed = slippage_bps <= slippage_limit
    return ExecutionBookDecision(
        allowed,
        "ok" if allowed else "entry_slippage_exceeds_limit",
        requested,
        filled_quantity=filled,
        quote_cost=quote_cost,
        vwap=vwap,
        mid=mid,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        snapshot=snapshot,
    )


def fetch_and_validate_execution_book(
    exchange: Any,
    *,
    venue: str,
    market_type: str,
    canonical_symbol: str,
    exchange_symbol: str,
    side: str,
    requested_quantity: Any,
    max_slippage_bps: Any,
    max_age_seconds: float = 5.0,
    limit: int = 50,
    realtime_provider: Any = None,
    allow_rest_fallback: bool = True,
) -> ExecutionBookDecision:
    """Prefer a healthy WebSocket book, with bounded REST fallback."""
    received_at = _utc_now()
    book = None
    if realtime_provider is not None:
        try:
            book = realtime_provider.get_order_book_dict(
                venue,
                MarketType.PERPETUAL.value if market_type == "futures" else market_type,
                exchange_symbol,
            )
            received_at = _utc_now()
        except Exception:
            book = None
    if book is None and not allow_rest_fallback:
        return ExecutionBookDecision(
            False,
            "realtime_order_book_unavailable",
            _decimal(requested_quantity),
        )
    if book is not None:
        return validate_execution_book(
            book,
            venue=venue,
            market_type=market_type,
            canonical_symbol=canonical_symbol,
            exchange_symbol=exchange_symbol,
            side=side,
            requested_quantity=requested_quantity,
            max_slippage_bps=max_slippage_bps,
            max_age_seconds=max_age_seconds,
            received_at=received_at,
        )
    try:
        book = exchange.fetch_order_book(
            exchange_symbol,
            limit=limit,
            market_type=market_type,
        )
        received_at = _utc_now()
    except Exception:
        return ExecutionBookDecision(
            False,
            "order_book_fetch_failed",
            _decimal(requested_quantity),
        )
    return validate_execution_book(
        book,
        venue=venue,
        market_type=market_type,
        canonical_symbol=canonical_symbol,
        exchange_symbol=exchange_symbol,
        side=side,
        requested_quantity=requested_quantity,
        max_slippage_bps=max_slippage_bps,
        max_age_seconds=max_age_seconds,
        received_at=received_at,
    )
