"""Realtime ccxt.pro streams with fail-closed health and immutable caches.

The module deliberately has no dependency on the bot engine, order manager, or
the synchronous exchange clients.  ``ccxt.pro`` is imported only when
``ccxt_pro_adapter_factory`` is called, so importing this module never opens a
socket, loads markets, or reads credentials.

One :class:`RealtimeStreamManager` can supervise multiple venue/market adapters.
Adapters emit normalized immutable records; the manager handles deadlines,
sequence integrity, reconnect backoff, private REST reconciliation, and
thread-safe lookups for synchronous execution code.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import math
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

MARKET_SILENCE_SECONDS = 15.0
PRIVATE_SILENCE_SECONDS = 60.0


class RealtimeStreamError(RuntimeError):
    """Base class for normalized stream failures."""


class StreamProtocolError(RealtimeStreamError):
    """An adapter emitted data that violates the normalized stream contract."""


class StreamDeadlineExceeded(RealtimeStreamError):
    """A cancellable stream operation made no progress before its deadline."""


class ReconciliationUnavailable(RealtimeStreamError):
    """A private stream fault cannot be recovered without a REST callback."""


class _StopRequested(RealtimeStreamError):
    pass


def _normalize_venue(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("venue must be a string")
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "binanceusdm": "binance",
        "binance_usdm": "binance",
        "bybit_linear": "bybit",
        "bitget_usdt": "bitget",
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized or any(char.isspace() for char in normalized):
        raise ValueError("venue must be a non-empty identifier")
    return normalized


def _normalize_market(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("market must be a string")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "future": "perpetual",
        "futures": "perpetual",
        "linear": "perpetual",
        "linear_swap": "perpetual",
        "perp": "perpetual",
        "perps": "perpetual",
        "swap": "perpetual",
        "usdm": "perpetual",
        "usdt_futures": "perpetual",
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized or any(char.isspace() for char in normalized):
        raise ValueError("market must be a non-empty identifier")
    return normalized


def _normalize_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("symbol must be a string")
    normalized = value.strip().upper()
    if not normalized or any(char.isspace() for char in normalized):
        raise ValueError("symbol must be non-empty and contain no whitespace")
    return normalized


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sequence(value: Any, name: str = "sequence") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_sequence(value: Any, name: str) -> Optional[int]:
    return None if value is None else _sequence(value, name)


def _decimal(value: Any, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _freeze(value: Any, path: str = "payload") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{path} contains a non-finite decimal")
        return value
    if isinstance(value, Mapping):
        frozen: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            frozen[key] = _freeze(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(item, f"{path}[]") for item in value)
    raise TypeError(f"{path} contains unsupported value {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


Level = Tuple[Decimal, Decimal]


def _levels(value: Any, *, allow_zero_quantity: bool, name: str) -> Tuple[Level, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a sequence of price/quantity levels")
    result: List[Level] = []
    seen = set()
    for index, level in enumerate(value):
        if not isinstance(level, Sequence) or isinstance(level, (str, bytes, bytearray)):
            raise ValueError(f"{name}[{index}] must be a price/quantity sequence")
        if len(level) < 2:
            raise ValueError(f"{name}[{index}] must contain price and quantity")
        price = _decimal(level[0], f"{name}[{index}].price")
        quantity = _decimal(level[1], f"{name}[{index}].quantity")
        if price <= 0 or quantity < 0 or (quantity == 0 and not allow_zero_quantity):
            qualifier = "non-negative" if allow_zero_quantity else "positive"
            raise ValueError(f"{name} prices must be positive and quantities {qualifier}")
        if price in seen:
            raise ValueError(f"{name} contains duplicate price {price}")
        seen.add(price)
        result.append((price, quantity))
    return tuple(result)


@dataclass(frozen=True, order=True)
class StreamKey:
    """Canonical cache identity: venue, market, then unified symbol."""

    venue: str
    market: str
    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue", _normalize_venue(self.venue))
        object.__setattr__(self, "market", _normalize_market(self.market))
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))


class StreamChannel(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class BookUpdateKind(str, Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"


def _book_kind(value: Union[BookUpdateKind, str]) -> BookUpdateKind:
    if isinstance(value, BookUpdateKind):
        return value
    try:
        return BookUpdateKind(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError("kind must be 'snapshot' or 'delta'") from exc


def _validate_range(
    sequence: int,
    first_sequence: Optional[int],
    previous_sequence: Optional[int],
) -> None:
    if first_sequence is not None and first_sequence > sequence:
        raise ValueError("first_sequence must not exceed sequence")
    if previous_sequence is not None and previous_sequence >= sequence:
        raise ValueError("previous_sequence must be lower than sequence")


@dataclass(frozen=True)
class OrderBookUpdate:
    """One immutable public snapshot or delta received from an adapter."""

    key: StreamKey
    kind: Union[BookUpdateKind, str]
    bids: Sequence[Sequence[Any]]
    asks: Sequence[Sequence[Any]]
    event_at: datetime
    received_at: datetime
    sequence: int
    first_sequence: Optional[int] = None
    previous_sequence: Optional[int] = None
    source: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise TypeError("key must be a StreamKey")
        kind = _book_kind(self.kind)
        bids = _levels(
            self.bids,
            allow_zero_quantity=kind is BookUpdateKind.DELTA,
            name="bids",
        )
        asks = _levels(
            self.asks,
            allow_zero_quantity=kind is BookUpdateKind.DELTA,
            name="asks",
        )
        if kind is BookUpdateKind.SNAPSHOT and (not bids or not asks):
            raise ValueError("a snapshot must contain both bids and asks")
        if kind is BookUpdateKind.DELTA and not bids and not asks:
            raise ValueError("a delta must contain at least one changed level")
        sequence = _sequence(self.sequence)
        first = _optional_sequence(self.first_sequence, "first_sequence")
        previous = _optional_sequence(self.previous_sequence, "previous_sequence")
        _validate_range(sequence, first, previous)
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "event_at", _utc(self.event_at, "event_at"))
        object.__setattr__(self, "received_at", _utc(self.received_at, "received_at"))
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "first_sequence", first)
        object.__setattr__(self, "previous_sequence", previous)
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class OrderBookSnapshot:
    """A complete immutable book resulting from a snapshot/delta sequence."""

    key: StreamKey
    bids: Tuple[Level, ...]
    asks: Tuple[Level, ...]
    event_at: datetime
    received_at: datetime
    sequence: int
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise TypeError("key must be a StreamKey")
        bids = _levels(self.bids, allow_zero_quantity=False, name="bids")
        asks = _levels(self.asks, allow_zero_quantity=False, name="asks")
        if not bids or not asks:
            raise ValueError("an order-book snapshot must contain bids and asks")
        bids = tuple(sorted(bids, key=lambda level: level[0], reverse=True))
        asks = tuple(sorted(asks, key=lambda level: level[0]))
        if bids[0][0] >= asks[0][0]:
            raise ValueError("an order-book snapshot must not be crossed")
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "event_at", _utc(self.event_at, "event_at"))
        object.__setattr__(self, "received_at", _utc(self.received_at, "received_at"))
        object.__setattr__(self, "sequence", _sequence(self.sequence))
        object.__setattr__(self, "source", self.source.strip())

    def as_ccxt_order_book(self) -> Dict[str, Any]:
        """Return an independent ccxt-shaped copy for synchronous consumers."""
        return {
            "symbol": self.key.symbol,
            "bids": [[float(price), float(quantity)] for price, quantity in self.bids],
            "asks": [[float(price), float(quantity)] for price, quantity in self.asks],
            "timestamp": int(self.event_at.timestamp() * 1000),
            "datetime": self.event_at.isoformat().replace("+00:00", "Z"),
            "nonce": self.sequence,
            "received_at": self.received_at.isoformat().replace("+00:00", "Z"),
            "source": self.source,
        }


@dataclass(frozen=True)
class PrivateOrderEvent:
    """One immutable private order update, keyed to the subscribed symbol."""

    key: StreamKey
    order_id: str
    status: str
    event_at: datetime
    received_at: datetime
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    first_sequence: Optional[int] = None
    previous_sequence: Optional[int] = None
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise TypeError("key must be a StreamKey")
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("order_id must be a non-empty string")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("status must be a non-empty string")
        sequence = _sequence(self.sequence)
        first = _optional_sequence(self.first_sequence, "first_sequence")
        previous = _optional_sequence(self.previous_sequence, "previous_sequence")
        _validate_range(sequence, first, previous)
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "status", self.status.strip().lower())
        object.__setattr__(self, "event_at", _utc(self.event_at, "event_at"))
        object.__setattr__(self, "received_at", _utc(self.received_at, "received_at"))
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "first_sequence", first)
        object.__setattr__(self, "previous_sequence", previous)
        object.__setattr__(self, "payload", _freeze(self.payload))
        object.__setattr__(self, "source", self.source.strip())

    def as_ccxt_order(self) -> Dict[str, Any]:
        """Return an independent order dictionary suitable for order-manager code."""
        result = _thaw(self.payload)
        result.setdefault("id", self.order_id)
        result.setdefault("symbol", self.key.symbol)
        result.setdefault("status", self.status)
        result.setdefault("timestamp", int(self.event_at.timestamp() * 1000))
        result["sequence"] = self.sequence
        result["received_at"] = self.received_at.isoformat().replace("+00:00", "Z")
        result["source"] = self.source
        return result


class SequenceIntegrityError(RealtimeStreamError):
    """Base class for strict sequence-integrity failures."""

    def __init__(
        self,
        key: StreamKey,
        channel: StreamChannel,
        previous_sequence: Optional[int],
        received_sequence: int,
    ) -> None:
        self.key = key
        self.channel = channel
        self.previous_sequence = previous_sequence
        self.received_sequence = received_sequence
        super().__init__(
            f"{channel.value} sequence error for {key.venue}/{key.market}/{key.symbol}: "
            f"previous={previous_sequence}, received={received_sequence}"
        )


class SequenceGapError(SequenceIntegrityError):
    """A delta or private event does not continue the accepted sequence."""


class SequenceRegressionError(SequenceIntegrityError):
    """A duplicate or older event arrived after an accepted event."""


def _continues(
    previous: int,
    sequence: int,
    first_sequence: Optional[int],
    previous_sequence: Optional[int],
) -> bool:
    if previous_sequence is not None:
        return previous_sequence == previous
    if first_sequence is not None:
        return first_sequence <= previous + 1 <= sequence
    return sequence == previous + 1


@dataclass
class _BookSequenceState:
    accepted: Optional[int] = None
    highest_seen: Optional[int] = None
    requires_snapshot: bool = True


class RealtimeCache:
    """Thread-safe public/private cache with strict per-key sequence state."""

    def __init__(self, *, max_private_orders: int = 10_000) -> None:
        if (
            isinstance(max_private_orders, bool)
            or not isinstance(max_private_orders, int)
            or max_private_orders < 1
        ):
            raise ValueError("max_private_orders must be a positive integer")
        self._max_private_orders = int(max_private_orders)
        self._lock = threading.RLock()
        self._books: Dict[StreamKey, OrderBookSnapshot] = {}
        self._book_sequences: Dict[StreamKey, _BookSequenceState] = {}
        self._private_sequences: Dict[StreamKey, int] = {}
        self._latest_orders: Dict[StreamKey, PrivateOrderEvent] = {}
        self._orders: OrderedDict[Tuple[StreamKey, str], PrivateOrderEvent] = OrderedDict()

    @staticmethod
    def key(venue: str, market: str, symbol: str) -> StreamKey:
        return StreamKey(venue, market, symbol)

    def apply_order_book(self, update: OrderBookUpdate) -> OrderBookSnapshot:
        if not isinstance(update, OrderBookUpdate):
            raise TypeError("update must be an OrderBookUpdate")
        with self._lock:
            state = self._book_sequences.setdefault(update.key, _BookSequenceState())
            if state.accepted is not None and update.sequence <= state.accepted:
                self._reject_book(update.key, state, update.sequence)
                raise SequenceRegressionError(
                    update.key,
                    StreamChannel.PUBLIC,
                    state.accepted,
                    update.sequence,
                )

            if update.kind is BookUpdateKind.DELTA:
                if state.accepted is None or state.requires_snapshot:
                    self._reject_book(update.key, state, update.sequence)
                    raise SequenceGapError(
                        update.key,
                        StreamChannel.PUBLIC,
                        state.accepted,
                        update.sequence,
                    )
                if not _continues(
                    state.accepted,
                    update.sequence,
                    update.first_sequence,
                    update.previous_sequence,
                ):
                    previous = state.accepted
                    self._reject_book(update.key, state, update.sequence)
                    raise SequenceGapError(
                        update.key,
                        StreamChannel.PUBLIC,
                        previous,
                        update.sequence,
                    )
                prior = self._books.get(update.key)
                if prior is None:
                    self._reject_book(update.key, state, update.sequence)
                    raise SequenceGapError(
                        update.key,
                        StreamChannel.PUBLIC,
                        state.accepted,
                        update.sequence,
                    )
                bids = dict(prior.bids)
                asks = dict(prior.asks)
                self._merge_levels(bids, update.bids)
                self._merge_levels(asks, update.asks)
            else:
                if (
                    state.requires_snapshot
                    and state.highest_seen is not None
                    and update.sequence < state.highest_seen
                ):
                    raise SequenceRegressionError(
                        update.key,
                        StreamChannel.PUBLIC,
                        state.highest_seen,
                        update.sequence,
                    )
                bids = dict(update.bids)
                asks = dict(update.asks)

            snapshot = self._snapshot(update, bids, asks)
            self._books[update.key] = snapshot
            state.accepted = update.sequence
            state.highest_seen = max(state.highest_seen or update.sequence, update.sequence)
            state.requires_snapshot = False
            return snapshot

    def _reject_book(
        self,
        key: StreamKey,
        state: _BookSequenceState,
        sequence: int,
    ) -> None:
        self._books.pop(key, None)
        state.highest_seen = max(state.highest_seen or sequence, sequence)
        state.requires_snapshot = True

    @staticmethod
    def _merge_levels(target: Dict[Decimal, Decimal], changes: Tuple[Level, ...]) -> None:
        for price, quantity in changes:
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity

    def _snapshot(
        self,
        update: OrderBookUpdate,
        bids: Mapping[Decimal, Decimal],
        asks: Mapping[Decimal, Decimal],
    ) -> OrderBookSnapshot:
        if not bids or not asks:
            state = self._book_sequences[update.key]
            self._reject_book(update.key, state, update.sequence)
            raise StreamProtocolError("an applied order book must retain bids and asks")
        sorted_bids = tuple(sorted(bids.items(), key=lambda level: level[0], reverse=True))
        sorted_asks = tuple(sorted(asks.items(), key=lambda level: level[0]))
        if sorted_bids[0][0] >= sorted_asks[0][0]:
            state = self._book_sequences[update.key]
            self._reject_book(update.key, state, update.sequence)
            raise StreamProtocolError("an applied order book must not be crossed")
        return OrderBookSnapshot(
            key=update.key,
            bids=sorted_bids,
            asks=sorted_asks,
            event_at=update.event_at,
            received_at=update.received_at,
            sequence=update.sequence,
            source=update.source,
        )

    def invalidate_order_book(self, key: StreamKey) -> None:
        if not isinstance(key, StreamKey):
            raise TypeError("key must be a StreamKey")
        with self._lock:
            self._books.pop(key, None)
            self._book_sequences.setdefault(key, _BookSequenceState()).requires_snapshot = True

    def get_order_book(
        self,
        venue: str,
        market: str,
        symbol: str,
    ) -> Optional[OrderBookSnapshot]:
        key = StreamKey(venue, market, symbol)
        with self._lock:
            return self._books.get(key)

    def get_order_book_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        snapshot = self.get_order_book(venue, market, symbol)
        return None if snapshot is None else snapshot.as_ccxt_order_book()

    get_latest_order_book = get_order_book

    def apply_order_event(self, event: PrivateOrderEvent) -> PrivateOrderEvent:
        if not isinstance(event, PrivateOrderEvent):
            raise TypeError("event must be a PrivateOrderEvent")
        with self._lock:
            previous = self._private_sequences.get(event.key)
            if previous is not None:
                if event.sequence <= previous:
                    raise SequenceRegressionError(
                        event.key,
                        StreamChannel.PRIVATE,
                        previous,
                        event.sequence,
                    )
                if not _continues(
                    previous,
                    event.sequence,
                    event.first_sequence,
                    event.previous_sequence,
                ):
                    raise SequenceGapError(
                        event.key,
                        StreamChannel.PRIVATE,
                        previous,
                        event.sequence,
                    )
            self._private_sequences[event.key] = event.sequence
            self._latest_orders[event.key] = event
            order_key = (event.key, event.order_id)
            self._orders[order_key] = event
            self._orders.move_to_end(order_key)
            while len(self._orders) > self._max_private_orders:
                self._orders.popitem(last=False)
            return event

    def reset_private_sequence(self, key: StreamKey) -> None:
        if not isinstance(key, StreamKey):
            raise TypeError("key must be a StreamKey")
        with self._lock:
            self._private_sequences.pop(key, None)

    def get_order_event(
        self,
        venue: str,
        market: str,
        symbol: str,
        order_id: Optional[str] = None,
    ) -> Optional[PrivateOrderEvent]:
        key = StreamKey(venue, market, symbol)
        with self._lock:
            if order_id is None:
                return self._latest_orders.get(key)
            return self._orders.get((key, str(order_id)))

    def get_order_event_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
        order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        event = self.get_order_event(venue, market, symbol, order_id)
        return None if event is None else event.as_ccxt_order()

    get_latest_order_event = get_order_event


RealtimeStreamCache = RealtimeCache


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class RealtimeStreamAdapter(Protocol):
    async def watch_order_book(self, symbol: str) -> OrderBookUpdate: ...

    async def watch_orders(self, symbol: str) -> Sequence[PrivateOrderEvent]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class ReconnectPolicy:
    initial_delay: float = 0.25
    maximum_delay: float = 15.0
    multiplier: float = 2.0
    max_attempts: int = 8
    jitter_factor: float = 0.2

    def __post_init__(self) -> None:
        numeric_values = (
            self.initial_delay,
            self.maximum_delay,
            self.multiplier,
            self.jitter_factor,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in numeric_values
        ):
            raise ValueError("reconnect delays and multiplier must be finite numbers")
        if self.initial_delay < 0:
            raise ValueError("initial_delay must be non-negative")
        if self.maximum_delay < self.initial_delay:
            raise ValueError("maximum_delay must be at least initial_delay")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least one")
        if not 0 <= self.jitter_factor <= 1:
            raise ValueError("jitter_factor must be between zero and one")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")

    def delay(
        self,
        attempt: int,
        *,
        jitter_sample: Optional[float] = None,
    ) -> float:
        """Return capped exponential backoff, optionally applying bounded jitter.

        ``max_attempts`` is the threshold at which health reports
        ``reconnect_exhausted``; it is deliberately not a terminal retry limit.
        A live manager keeps retrying at the capped delay until it is stopped.
        """
        if attempt < 1:
            raise ValueError("attempt must be positive")
        if self.initial_delay == 0 or self.multiplier == 1:
            delay = self.initial_delay
        else:
            # Clamp the exponent before evaluating it. Reconnects are now
            # intentionally unbounded, so calculating ``2 ** failures`` first
            # would eventually allocate an enormous integer when callers pass
            # integer-valued policy fields.
            cap_exponent = math.ceil(
                math.log(self.maximum_delay / self.initial_delay, self.multiplier)
            )
            if attempt - 1 >= cap_exponent:
                delay = self.maximum_delay
            else:
                delay = self.initial_delay * (self.multiplier ** (attempt - 1))
        if delay > self.maximum_delay:
            delay = self.maximum_delay
        if jitter_sample is None or self.jitter_factor == 0 or delay == 0:
            return delay
        if (
            isinstance(jitter_sample, bool)
            or not isinstance(jitter_sample, (int, float))
            or not math.isfinite(float(jitter_sample))
            or not 0 <= float(jitter_sample) <= 1
        ):
            raise ValueError("jitter_sample must be a finite number between zero and one")
        spread = delay * self.jitter_factor
        jittered = delay + ((2 * float(jitter_sample) - 1) * spread)
        return max(0.0, min(jittered, self.maximum_delay))


@dataclass(frozen=True)
class StreamSettings:
    market_silence_seconds: float = MARKET_SILENCE_SECONDS
    private_silence_seconds: float = PRIVATE_SILENCE_SECONDS
    reconciliation_timeout_seconds: float = 15.0
    close_timeout_seconds: float = 5.0
    reconnect: ReconnectPolicy = field(default_factory=ReconnectPolicy)

    def __post_init__(self) -> None:
        for name in (
            "market_silence_seconds",
            "private_silence_seconds",
            "reconciliation_timeout_seconds",
            "close_timeout_seconds",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if not isinstance(self.reconnect, ReconnectPolicy):
            raise TypeError("reconnect must be a ReconnectPolicy")


@dataclass(frozen=True)
class ReconciliationRequest:
    key: StreamKey
    reason: str
    detected_at: datetime
    previous_sequence: Optional[int] = None
    received_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise TypeError("key must be a StreamKey")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "detected_at", _utc(self.detected_at, "detected_at"))
        object.__setattr__(
            self,
            "previous_sequence",
            _optional_sequence(self.previous_sequence, "previous_sequence"),
        )
        object.__setattr__(
            self,
            "received_sequence",
            _optional_sequence(self.received_sequence, "received_sequence"),
        )


ReconciliationCallback = Callable[
    [ReconciliationRequest],
    Union[Awaitable[None], None],
]


@dataclass(frozen=True)
class StreamHealth:
    key: StreamKey
    channel: StreamChannel
    connected: bool
    healthy: bool
    reason: str
    connected_at: Optional[datetime]
    last_progress_at: Optional[datetime]
    last_received_at: Optional[datetime]
    sequence: Optional[int]
    reconnect_attempts: int


@dataclass
class _HealthState:
    connected: bool = False
    reason: str = "not_started"
    connected_at: Optional[datetime] = None
    connected_monotonic: Optional[float] = None
    last_progress_at: Optional[datetime] = None
    last_received_at: Optional[datetime] = None
    last_progress_monotonic: Optional[float] = None
    sequence: Optional[int] = None
    reconnect_attempts: int = 0


@dataclass(frozen=True)
class _Registration:
    adapter: RealtimeStreamAdapter
    key: StreamKey
    private: bool


class RealtimeStreamManager:
    """Supervise normalized public/private streams and expose fail-closed reads."""

    def __init__(
        self,
        *,
        cache: Optional[RealtimeCache] = None,
        settings: Optional[StreamSettings] = None,
        reconciliation_callback: Optional[ReconciliationCallback] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.cache = cache or RealtimeCache()
        self.settings = settings or StreamSettings()
        self._reconciliation_callback = reconciliation_callback
        self._clock = clock or SystemClock()
        self._registrations: List[_Registration] = []
        self._registered_keys = set()
        self._health_lock = threading.RLock()
        self._health: Dict[Tuple[StreamKey, StreamChannel], _HealthState] = {}
        self._tasks: List[asyncio.Task[Any]] = []
        self._stop_event: Optional[asyncio.Event] = None
        self._started = False
        self._closed = False

    def register(
        self,
        adapter: RealtimeStreamAdapter,
        *,
        venue: str,
        market: str,
        symbols: Union[str, Sequence[str]],
        private: bool = True,
    ) -> Tuple[StreamKey, ...]:
        """Register symbols before ``start``; no adapter I/O occurs here."""
        if self._started or self._closed:
            raise RuntimeError("streams must be registered before the manager starts")
        raw_symbols = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        if not raw_symbols:
            raise ValueError("symbols must not be empty")
        keys = tuple(StreamKey(venue, market, symbol) for symbol in raw_symbols)
        if len(set(keys)) != len(keys):
            raise ValueError("symbols contain duplicate stream keys")
        if any(key in self._registered_keys for key in keys):
            raise ValueError("a stream key is already registered")
        for key in keys:
            self._registrations.append(_Registration(adapter, key, bool(private)))
            self._registered_keys.add(key)
            self._health[(key, StreamChannel.PUBLIC)] = _HealthState()
            if private:
                self._health[(key, StreamChannel.PRIVATE)] = _HealthState()
        return keys

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("a stopped manager cannot be restarted")
        if self._started:
            return
        if not self._registrations:
            raise RuntimeError("at least one stream must be registered")
        self._stop_event = asyncio.Event()
        self._started = True
        for registration in self._registrations:
            public_name = (
                f"realtime:public:{registration.key.venue}:"
                f"{registration.key.market}:{registration.key.symbol}"
            )
            self._tasks.append(
                asyncio.create_task(self._run_public(registration), name=public_name)
            )
            if registration.private:
                private_name = (
                    f"realtime:private:{registration.key.venue}:"
                    f"{registration.key.market}:{registration.key.symbol}"
                )
                self._tasks.append(
                    asyncio.create_task(self._run_private(registration), name=private_name)
                )
        await asyncio.sleep(0)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        adapters = {id(item.adapter): item.adapter for item in self._registrations}.values()
        for adapter in adapters:
            try:
                await asyncio.wait_for(
                    adapter.close(),
                    timeout=self.settings.close_timeout_seconds,
                )
            except Exception:
                pass
        with self._health_lock:
            for state in self._health.values():
                state.connected = False
                state.reason = "stopped"
        self._started = False

    @property
    def is_running(self) -> bool:
        """Whether every registered worker task is still supervised and alive."""
        if not self._started or self._closed or not self._tasks:
            return False
        return all(not task.done() for task in tuple(self._tasks))

    async def __aenter__(self) -> RealtimeStreamManager:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.stop()

    def health(
        self,
        venue: str,
        market: str,
        symbol: str,
        channel: Union[StreamChannel, str] = StreamChannel.PUBLIC,
    ) -> StreamHealth:
        key = StreamKey(venue, market, symbol)
        normalized_channel = (
            channel if isinstance(channel, StreamChannel) else StreamChannel(str(channel).lower())
        )
        with self._health_lock:
            state = self._health.get((key, normalized_channel))
            if state is None:
                return StreamHealth(
                    key,
                    normalized_channel,
                    False,
                    False,
                    "not_registered",
                    None,
                    None,
                    None,
                    None,
                    0,
                )
            healthy, reason = self._evaluate_health(state, normalized_channel)
            return StreamHealth(
                key=key,
                channel=normalized_channel,
                connected=state.connected,
                healthy=healthy,
                reason=reason,
                connected_at=state.connected_at,
                last_progress_at=state.last_progress_at,
                last_received_at=state.last_received_at,
                sequence=state.sequence,
                reconnect_attempts=state.reconnect_attempts,
            )

    def _evaluate_health(
        self,
        state: _HealthState,
        channel: StreamChannel,
    ) -> Tuple[bool, str]:
        if not state.connected:
            return False, state.reason
        if state.last_progress_monotonic is None:
            return False, "no_progress"
        silence_limit = (
            self.settings.market_silence_seconds
            if channel is StreamChannel.PUBLIC
            else self.settings.private_silence_seconds
        )
        silence = max(0.0, self._clock.monotonic() - state.last_progress_monotonic)
        if silence > silence_limit:
            reason = "market_silence" if channel is StreamChannel.PUBLIC else "private_silence"
            return False, reason
        return True, "healthy"

    def is_healthy(
        self,
        venue: str,
        market: str,
        symbol: str,
        channel: Union[StreamChannel, str] = StreamChannel.PUBLIC,
    ) -> bool:
        return self.health(venue, market, symbol, channel).healthy

    def get_order_book(
        self,
        venue: str,
        market: str,
        symbol: str,
        *,
        require_healthy: bool = True,
    ) -> Optional[OrderBookSnapshot]:
        if require_healthy and not self.is_healthy(venue, market, symbol, StreamChannel.PUBLIC):
            return None
        return self.cache.get_order_book(venue, market, symbol)

    def get_order_book_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
        *,
        require_healthy: bool = True,
    ) -> Optional[Dict[str, Any]]:
        snapshot = self.get_order_book(
            venue,
            market,
            symbol,
            require_healthy=require_healthy,
        )
        return None if snapshot is None else snapshot.as_ccxt_order_book()

    def get_order_event(
        self,
        venue: str,
        market: str,
        symbol: str,
        order_id: Optional[str] = None,
        *,
        require_healthy: bool = True,
    ) -> Optional[PrivateOrderEvent]:
        if require_healthy and not self.is_healthy(venue, market, symbol, StreamChannel.PRIVATE):
            return None
        return self.cache.get_order_event(venue, market, symbol, order_id)

    def get_order_event_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
        order_id: Optional[str] = None,
        *,
        require_healthy: bool = True,
    ) -> Optional[Dict[str, Any]]:
        event = self.get_order_event(
            venue,
            market,
            symbol,
            order_id,
            require_healthy=require_healthy,
        )
        return None if event is None else event.as_ccxt_order()

    async def _run_public(self, registration: _Registration) -> None:
        key = registration.key
        failures = 0
        self._mark_connected(key, StreamChannel.PUBLIC, failures)
        while not self._stopping:
            try:
                update = await self._deadline(
                    registration.adapter.watch_order_book(key.symbol),
                    self.settings.market_silence_seconds,
                )
                if not isinstance(update, OrderBookUpdate) or update.key != key:
                    raise StreamProtocolError("public adapter returned the wrong stream key")
                snapshot = self.cache.apply_order_book(update)
            except _StopRequested:
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.cache.invalidate_order_book(key)
                failures += 1
                if not await self._retry(key, StreamChannel.PUBLIC, failures, exc):
                    return
            else:
                failures = 0
                self._mark_progress(
                    key,
                    StreamChannel.PUBLIC,
                    snapshot.received_at,
                    snapshot.sequence,
                )

    async def _run_private(self, registration: _Registration) -> None:
        key = registration.key
        failures = 0
        pending_reconciliation: Optional[ReconciliationRequest] = None
        self._mark_connected(key, StreamChannel.PRIVATE, failures)
        while not self._stopping:
            try:
                events = await self._deadline(
                    registration.adapter.watch_orders(key.symbol),
                    self.settings.private_silence_seconds,
                )
                if not isinstance(events, Sequence) or isinstance(events, (str, bytes, bytearray)):
                    raise StreamProtocolError("private adapter must return an event sequence")
                if not events:
                    raise StreamProtocolError("private adapter returned an empty event batch")
                if pending_reconciliation is not None:
                    await self._reconcile(pending_reconciliation)
                    self.cache.reset_private_sequence(key)
                    pending_reconciliation = None
                latest: Optional[PrivateOrderEvent] = None
                for event in events:
                    if not isinstance(event, PrivateOrderEvent) or event.key != key:
                        raise StreamProtocolError("private adapter returned the wrong stream key")
                    latest = self.cache.apply_order_event(event)
                if latest is None:
                    raise StreamProtocolError("private adapter made no progress")
            except _StopRequested:
                return
            except asyncio.CancelledError:
                raise
            except SequenceIntegrityError as exc:
                request = ReconciliationRequest(
                    key=key,
                    reason="private_sequence_gap",
                    detected_at=self._clock.now(),
                    previous_sequence=exc.previous_sequence,
                    received_sequence=exc.received_sequence,
                )
                self._mark_failure(
                    key,
                    StreamChannel.PRIVATE,
                    "sequence_gap",
                    failures + 1,
                )
                try:
                    await self._reconcile(request)
                except Exception:
                    pending_reconciliation = request
                else:
                    self.cache.reset_private_sequence(key)
                    pending_reconciliation = None
                failures += 1
                if not await self._retry(
                    key,
                    StreamChannel.PRIVATE,
                    failures,
                    exc,
                ):
                    return
            except Exception as exc:
                if pending_reconciliation is None:
                    pending_reconciliation = ReconciliationRequest(
                        key=key,
                        reason="private_reconnect",
                        detected_at=self._clock.now(),
                    )
                failures += 1
                if not await self._retry(
                    key,
                    StreamChannel.PRIVATE,
                    failures,
                    exc,
                ):
                    return
            else:
                failures = 0
                self._mark_progress(
                    key,
                    StreamChannel.PRIVATE,
                    latest.received_at,
                    latest.sequence,
                )

    @property
    def _stopping(self) -> bool:
        return self._stop_event is None or self._stop_event.is_set()

    async def _retry(
        self,
        key: StreamKey,
        channel: StreamChannel,
        failures: int,
        exc: Exception,
    ) -> bool:
        exhausted = failures > self.settings.reconnect.max_attempts
        reason = (
            "reconnect_exhausted"
            if exhausted
            else self._failure_reason(channel, exc)
        )
        self._mark_failure(key, channel, reason, failures)
        try:
            await self._sleep(
                self.settings.reconnect.delay(
                    failures,
                    jitter_sample=random.random(),
                )
            )
        except _StopRequested:
            return False
        self._mark_connected(key, channel, failures)
        return True

    @staticmethod
    def _failure_reason(channel: StreamChannel, exc: Exception) -> str:
        if isinstance(exc, SequenceGapError):
            return "sequence_gap"
        if isinstance(exc, SequenceRegressionError):
            return "sequence_regression"
        if isinstance(exc, StreamDeadlineExceeded):
            return "market_silence" if channel is StreamChannel.PUBLIC else "private_silence"
        if isinstance(exc, ReconciliationUnavailable):
            return "reconciliation_unavailable"
        if isinstance(exc, StreamProtocolError):
            return "protocol_error"
        return f"stream_error:{type(exc).__name__}"

    async def _reconcile(self, request: ReconciliationRequest) -> None:
        callback = self._reconciliation_callback
        if callback is None:
            raise ReconciliationUnavailable("private reconciliation callback is not configured")
        result = callback(request)
        if inspect.isawaitable(result):
            await self._deadline(result, self.settings.reconciliation_timeout_seconds)

    async def _deadline(self, awaitable: Awaitable[Any], timeout: float) -> Any:
        stop_event = self._stop_event
        if stop_event is None:
            raise _StopRequested()
        operation = asyncio.ensure_future(awaitable)
        stopper = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, stopper},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopper in done:
                raise _StopRequested()
            if operation in done:
                return await operation
            raise StreamDeadlineExceeded(f"stream operation exceeded {timeout:.3f}s")
        finally:
            for task in (operation, stopper):
                if not task.done():
                    task.cancel()
            for task in (operation, stopper):
                with suppress(Exception, asyncio.CancelledError):
                    await task

    async def _sleep(self, delay: float) -> None:
        stop_event = self._stop_event
        if stop_event is None or stop_event.is_set():
            raise _StopRequested()
        if delay == 0:
            await asyncio.sleep(0)
            if stop_event.is_set():
                raise _StopRequested()
            return
        sleeper = asyncio.create_task(asyncio.sleep(delay))
        stopper = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {sleeper, stopper},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopper in done:
                raise _StopRequested()
        finally:
            for task in (sleeper, stopper):
                if not task.done():
                    task.cancel()
            for task in (sleeper, stopper):
                with suppress(Exception, asyncio.CancelledError):
                    await task

    def _mark_connected(
        self,
        key: StreamKey,
        channel: StreamChannel,
        attempts: int,
    ) -> None:
        with self._health_lock:
            state = self._health[(key, channel)]
            state.connected = True
            state.reason = "no_progress"
            state.connected_at = self._clock.now()
            state.connected_monotonic = self._clock.monotonic()
            state.last_progress_at = None
            state.last_received_at = None
            state.last_progress_monotonic = None
            state.reconnect_attempts = attempts

    def _mark_progress(
        self,
        key: StreamKey,
        channel: StreamChannel,
        received_at: datetime,
        sequence: int,
    ) -> None:
        with self._health_lock:
            state = self._health[(key, channel)]
            state.connected = True
            state.reason = "healthy"
            state.last_progress_at = self._clock.now()
            state.last_received_at = received_at
            state.last_progress_monotonic = self._clock.monotonic()
            state.sequence = sequence
            state.reconnect_attempts = 0

    def _mark_failure(
        self,
        key: StreamKey,
        channel: StreamChannel,
        reason: str,
        attempts: int,
    ) -> None:
        with self._health_lock:
            state = self._health[(key, channel)]
            state.connected = False
            state.reason = reason
            state.reconnect_attempts = attempts


def _wire_datetime(value: Any, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return _utc(value, "timestamp")
    if isinstance(value, str):
        encoded = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(encoded)
        except ValueError:
            try:
                value = Decimal(value)
            except (InvalidOperation, ValueError):
                return fallback
        else:
            return _utc(parsed, "timestamp")
    try:
        numeric = _decimal(value, "timestamp")
    except ValueError:
        return fallback
    divisor = Decimal(1000) if abs(numeric) >= Decimal("100000000000") else Decimal(1)
    try:
        return datetime.fromtimestamp(float(numeric / divisor), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return fallback


def _wire_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _mapping_int(payload: Mapping[str, Any], names: Sequence[str]) -> Optional[int]:
    for name in names:
        value = _wire_int(payload.get(name))
        if value is not None:
            return value
    info = payload.get("info")
    if isinstance(info, Mapping):
        for name in names:
            value = _wire_int(info.get(name))
            if value is not None:
                return value
    return None


class CcxtProAdapter:
    """Normalize one explicitly-created ccxt.pro exchange instance."""

    def __init__(
        self,
        exchange: Any,
        *,
        venue: str,
        market: str = "perpetual",
        clock: Optional[Clock] = None,
    ) -> None:
        self.exchange = exchange
        self.venue = _normalize_venue(venue)
        self.market = _normalize_market(market)
        self._clock = clock or SystemClock()
        self._public_local_sequences: Dict[str, int] = {}
        self._private_local_sequences: Dict[str, int] = {}

    async def watch_order_book(self, symbol: str) -> OrderBookUpdate:
        normalized_symbol = _normalize_symbol(symbol)
        raw = await self.exchange.watch_order_book(normalized_symbol)
        received_at = self._clock.now()
        if not isinstance(raw, Mapping):
            raise StreamProtocolError("ccxt.pro watch_order_book returned a non-mapping")
        sequence = _mapping_int(
            raw,
            ("nonce", "sequence", "seq", "seqId", "u", "updateId", "lastUpdateId"),
        )
        local = self._public_local_sequences.get(normalized_symbol, -1)
        if sequence is None:
            sequence = local + 1
        self._public_local_sequences[normalized_symbol] = max(local, sequence)
        timestamp = raw.get("timestamp", raw.get("datetime"))
        info = raw.get("info") if isinstance(raw.get("info"), Mapping) else {}
        metadata = {
            "checksum": raw.get("checksum"),
            "info": info,
            "sequence_source": "exchange" if raw.get("nonce") is not None else "normalized",
        }
        # ccxt.pro has already merged venue deltas and returns a complete book.
        return OrderBookUpdate(
            key=StreamKey(self.venue, self.market, normalized_symbol),
            kind=BookUpdateKind.SNAPSHOT,
            bids=raw.get("bids", ()),
            asks=raw.get("asks", ()),
            event_at=_wire_datetime(timestamp, received_at),
            received_at=received_at,
            sequence=sequence,
            source=f"{self.venue}.ccxt.pro.order_book",
            payload=metadata,
        )

    async def watch_orders(self, symbol: str) -> Sequence[PrivateOrderEvent]:
        normalized_symbol = _normalize_symbol(symbol)
        raw = await self.exchange.watch_orders(normalized_symbol)
        received_at = self._clock.now()
        if isinstance(raw, Mapping):
            orders: Sequence[Any] = (raw,)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            orders = raw
        else:
            raise StreamProtocolError("ccxt.pro watch_orders returned an invalid batch")
        events: List[PrivateOrderEvent] = []
        sequence = self._private_local_sequences.get(normalized_symbol, -1)
        for order in orders:
            if not isinstance(order, Mapping):
                raise StreamProtocolError("ccxt.pro order update must be a mapping")
            order_id = order.get("id") or order.get("orderId") or order.get("clientOrderId")
            if order_id is None:
                raise StreamProtocolError("ccxt.pro order update is missing an order id")
            sequence += 1
            status = str(order.get("status") or "unknown")
            timestamp = order.get("lastTradeTimestamp", order.get("timestamp"))
            events.append(
                PrivateOrderEvent(
                    key=StreamKey(self.venue, self.market, normalized_symbol),
                    order_id=str(order_id),
                    status=status,
                    event_at=_wire_datetime(timestamp, received_at),
                    received_at=received_at,
                    sequence=sequence,
                    payload=order,
                    source=f"{self.venue}.ccxt.pro.orders",
                )
            )
        self._private_local_sequences[normalized_symbol] = sequence
        return tuple(events)

    async def close(self) -> None:
        result = self.exchange.close()
        if inspect.isawaitable(result):
            await result


_CCXT_PRO_VENUES = MappingProxyType(
    {
        "binance": (
            "binanceusdm",
            {"defaultType": "future", "defaultSubType": "linear"},
        ),
        "bybit": (
            "bybit",
            {"defaultType": "swap", "defaultSubType": "linear"},
        ),
        "bitget": (
            "bitget",
            {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
            },
        ),
    }
)


def ccxt_pro_adapter_factory(
    venue: str,
    *,
    market: str = "perpetual",
    credentials: Optional[Mapping[str, Any]] = None,
    exchange_config: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
    clock: Optional[Clock] = None,
) -> CcxtProAdapter:
    """Create a supported ccxt.pro adapter without doing network I/O.

    Supported venue profiles are Binance USDM, Bybit linear swaps, and Bitget
    USDT-settled swaps.  Credentials are passed directly to ccxt's constructor
    and are never logged or included in exceptions emitted by this module.
    """
    normalized_venue = _normalize_venue(venue)
    normalized_market = _normalize_market(market)
    if normalized_market != "perpetual":
        raise ValueError("the ccxt.pro factory supports linear perpetual markets only")
    profile = _CCXT_PRO_VENUES.get(normalized_venue)
    if profile is None:
        supported = ", ".join(sorted(_CCXT_PRO_VENUES))
        raise ValueError(f"unsupported ccxt.pro venue; expected one of: {supported}")
    if credentials is not None and not isinstance(credentials, Mapping):
        raise TypeError("credentials must be a mapping")
    if exchange_config is not None and not isinstance(exchange_config, Mapping):
        raise TypeError("exchange_config must be a mapping")
    if options is not None and not isinstance(options, Mapping):
        raise TypeError("options must be a mapping")

    exchange_id, default_options = profile
    config: Dict[str, Any] = {
        "enableRateLimit": True,
        "newUpdates": True,
    }
    if exchange_config:
        config.update(dict(exchange_config))
    configured_options = config.get("options")
    merged_options = dict(default_options)
    if isinstance(configured_options, Mapping):
        merged_options.update(dict(configured_options))
    if options:
        merged_options.update(dict(options))
    config["options"] = merged_options
    if credentials:
        config.update(dict(credentials))

    try:
        ccxt_pro = importlib.import_module("ccxt.pro")
        exchange_type = getattr(ccxt_pro, exchange_id)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("ccxt.pro with the requested exchange is not installed") from exc
    exchange = exchange_type(config)
    return CcxtProAdapter(
        exchange,
        venue=normalized_venue,
        market=normalized_market,
        clock=clock,
    )


create_ccxt_pro_adapter = ccxt_pro_adapter_factory


__all__ = [
    "BookUpdateKind",
    "CcxtProAdapter",
    "MARKET_SILENCE_SECONDS",
    "OrderBookSnapshot",
    "OrderBookUpdate",
    "PRIVATE_SILENCE_SECONDS",
    "PrivateOrderEvent",
    "RealtimeCache",
    "RealtimeStreamAdapter",
    "RealtimeStreamCache",
    "RealtimeStreamError",
    "RealtimeStreamManager",
    "ReconnectPolicy",
    "ReconciliationRequest",
    "SequenceGapError",
    "SequenceIntegrityError",
    "SequenceRegressionError",
    "StreamChannel",
    "StreamDeadlineExceeded",
    "StreamHealth",
    "StreamKey",
    "StreamProtocolError",
    "StreamSettings",
    "ccxt_pro_adapter_factory",
    "create_ccxt_pro_adapter",
]
