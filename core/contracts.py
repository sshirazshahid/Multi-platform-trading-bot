"""Immutable contracts shared by market-data, decision, and execution layers.

The contracts in this module are deliberately dependency-free.  They normalize
wire values once at the boundary and retain exact decimal values, UTC timestamps,
and deeply immutable JSON context after construction.

The legacy JSON strategy-spec module remains available for compatibility.  The
``StrategySpec`` below is the immutable execution-boundary contract used by the
strategy program and promotion latches.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Optional, Tuple, Type, TypeVar

CONTRACT_SCHEMA_VERSION = 1


class _NormalizedStringEnum(str, Enum):
    """String enum accepting case and separator differences at the boundary."""

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            for member in cls:
                if member.value == normalized:
                    return member
        return None

    def __str__(self) -> str:
        return self.value


class Venue(_NormalizedStringEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    BITGET = "bitget"


class MarketType(_NormalizedStringEnum):
    SPOT = "spot"
    MARGIN = "margin"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            aliases = {
                "perp": cls.PERPETUAL,
                "perps": cls.PERPETUAL,
                "swap": cls.PERPETUAL,
                "swaps": cls.PERPETUAL,
                "futures": cls.FUTURE,
                "options": cls.OPTION,
            }
            if normalized in aliases:
                return aliases[normalized]
        return super()._missing_(value)


class MarketDataKind(_NormalizedStringEnum):
    AVAILABILITY = "availability"
    BID_PRICE = "bid_price"
    ASK_PRICE = "ask_price"
    LAST_PRICE = "last_price"
    TRADE_PRICE = "trade_price"
    MARK_PRICE = "mark_price"
    INDEX_PRICE = "index_price"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    VOLUME = "volume"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            aliases = {
                "bid": cls.BID_PRICE,
                "ask": cls.ASK_PRICE,
                "last": cls.LAST_PRICE,
                "trade": cls.TRADE_PRICE,
                "mark": cls.MARK_PRICE,
                "index": cls.INDEX_PRICE,
                "funding": cls.FUNDING_RATE,
                "oi": cls.OPEN_INTEREST,
            }
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in aliases:
                return aliases[normalized]
        return super()._missing_(value)


class DecisionAction(_NormalizedStringEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"
    HOLD = "hold"
    SKIP = "skip"
    ALLOW = "allow"
    REVIEW = "review"
    CANCEL = "cancel"


class DecisionOutcome(_NormalizedStringEnum):
    """Terminal lifecycle outcome for one candidate decision."""

    REJECTED = "rejected"
    DEFERRED = "deferred"
    EXPIRED = "expired"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    ERROR = "error"


class OrderSide(_NormalizedStringEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(_NormalizedStringEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT_MARKET = "take_profit_market"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class TimeInForce(_NormalizedStringEnum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    GTX = "gtx"


class ExecutionEventType(_NormalizedStringEnum):
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ERROR = "error"


_EnumT = TypeVar("_EnumT", bound=Enum)
_CANONICAL_SYMBOL_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*/[A-Z0-9][A-Z0-9._-]*(?::[A-Z0-9][A-Z0-9._-]*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRICE_KINDS = {
    MarketDataKind.BID_PRICE,
    MarketDataKind.ASK_PRICE,
    MarketDataKind.LAST_PRICE,
    MarketDataKind.TRADE_PRICE,
    MarketDataKind.MARK_PRICE,
    MarketDataKind.INDEX_PRICE,
}


def _enum(value: Any, enum_type: Type[_EnumT], name: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}")


def _text(value: Any, name: str, *, max_length: int = 255) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")
    return normalized


def _optional_text(value: Any, name: str, *, max_length: int = 255) -> Optional[str]:
    if value is None:
        return None
    return _text(value, name, max_length=max_length)


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_utc(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        return _utc(value, name)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO-8601 string or datetime")
    encoded = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(encoded)
    except ValueError:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp")
    return _utc(parsed, name)


def _iso(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TypeError(f"{name} must be a decimal-compatible value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a valid decimal")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _optional_decimal(value: Any, name: str) -> Optional[Decimal]:
    return None if value is None else _decimal(value, name)


def _version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("schema_version must be a positive integer")
    return value


def _sequence(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sequence must be a non-negative integer")
    return value


def _freeze_json(value: Any, path: str = "context") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise TypeError(f"{path} must contain only JSON-compatible values")


def _freeze_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return _freeze_json(value, name)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _mapping_payload(value: Any, contract_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{contract_name}.from_dict requires a mapping")
    return value


@dataclass(frozen=True)
class InstrumentId:
    """Unambiguous identity across canonical and venue-native symbol spaces."""

    venue: Venue
    market: MarketType
    canonical_symbol: str
    exchange_symbol: str

    def __post_init__(self) -> None:
        venue = _enum(self.venue, Venue, "venue")
        market = _enum(self.market, MarketType, "market")
        canonical = _text(self.canonical_symbol, "canonical_symbol").upper()
        exchange_symbol = _text(self.exchange_symbol, "exchange_symbol")
        if not _CANONICAL_SYMBOL_RE.fullmatch(canonical):
            raise ValueError(
                "canonical_symbol must use BASE/QUOTE or BASE/QUOTE:SETTLE format"
            )
        if any(char.isspace() for char in exchange_symbol):
            raise ValueError("exchange_symbol must not contain whitespace")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "canonical_symbol", canonical)
        object.__setattr__(self, "exchange_symbol", exchange_symbol)

    @property
    def identity_key(self) -> Tuple[str, str, str, str]:
        return (
            self.venue.value,
            self.market.value,
            self.canonical_symbol,
            self.exchange_symbol,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue.value,
            "market": self.market.value,
            "canonical_symbol": self.canonical_symbol,
            "exchange_symbol": self.exchange_symbol,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InstrumentId:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            venue=data["venue"],
            market=data["market"],
            canonical_symbol=data["canonical_symbol"],
            exchange_symbol=data["exchange_symbol"],
        )


@dataclass(frozen=True)
class StrategySpec:
    """Immutable strategy identity, rule set, and runtime eligibility record."""

    strategy_id: str
    strategy_version: str
    family: str
    market_types: Tuple[MarketType, ...]
    venues: Tuple[Venue, ...]
    instruments: Tuple[InstrumentId, ...] = ()
    entry_rules: Mapping[str, Any] = field(default_factory=dict)
    exit_rules: Mapping[str, Any] = field(default_factory=dict)
    sizing: Mapping[str, Any] = field(default_factory=dict)
    risk_limits: Mapping[str, Any] = field(default_factory=dict)
    validation_status: str = "untested"
    promotion_status: str = "disabled"
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "strategy_id", _text(self.strategy_id, "strategy_id")
        )
        object.__setattr__(
            self,
            "strategy_version",
            _text(self.strategy_version, "strategy_version"),
        )
        object.__setattr__(self, "family", _text(self.family, "family"))
        if not isinstance(self.market_types, (tuple, list)) or not self.market_types:
            raise ValueError("market_types must contain at least one market type")
        market_types = tuple(
            _enum(item, MarketType, "market_type") for item in self.market_types
        )
        if len(set(market_types)) != len(market_types):
            raise ValueError("market_types must not contain duplicates")
        if not isinstance(self.venues, (tuple, list)) or not self.venues:
            raise ValueError("venues must contain at least one venue")
        venues = tuple(_enum(item, Venue, "venue") for item in self.venues)
        if len(set(venues)) != len(venues):
            raise ValueError("venues must not contain duplicates")
        if not isinstance(self.instruments, (tuple, list)):
            raise TypeError("instruments must be a sequence")
        instruments = tuple(self.instruments)
        if not all(isinstance(item, InstrumentId) for item in instruments):
            raise TypeError("instruments must contain only InstrumentId values")
        if any(item.venue not in venues for item in instruments):
            raise ValueError("instrument venue must be declared in venues")
        if any(item.market not in market_types for item in instruments):
            raise ValueError("instrument market must be declared in market_types")
        object.__setattr__(self, "market_types", market_types)
        object.__setattr__(self, "venues", venues)
        object.__setattr__(self, "instruments", instruments)
        for name in ("entry_rules", "exit_rules", "sizing", "risk_limits"):
            object.__setattr__(self, name, _freeze_mapping(getattr(self, name), name))
        object.__setattr__(
            self,
            "validation_status",
            _text(self.validation_status, "validation_status"),
        )
        object.__setattr__(
            self,
            "promotion_status",
            _text(self.promotion_status, "promotion_status"),
        )
        object.__setattr__(self, "schema_version", _version(self.schema_version))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "family": self.family,
            "market_types": [item.value for item in self.market_types],
            "venues": [item.value for item in self.venues],
            "instruments": [item.to_dict() for item in self.instruments],
            "entry_rules": _thaw_json(self.entry_rules),
            "exit_rules": _thaw_json(self.exit_rules),
            "sizing": _thaw_json(self.sizing),
            "risk_limits": _thaw_json(self.risk_limits),
            "validation_status": self.validation_status,
            "promotion_status": self.promotion_status,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StrategySpec:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            strategy_id=data["strategy_id"],
            strategy_version=data["strategy_version"],
            family=data["family"],
            market_types=tuple(data["market_types"]),
            venues=tuple(data["venues"]),
            instruments=tuple(
                InstrumentId.from_dict(item) for item in data.get("instruments", ())
            ),
            entry_rules=data.get("entry_rules", {}),
            exit_rules=data.get("exit_rules", {}),
            sizing=data.get("sizing", {}),
            risk_limits=data.get("risk_limits", {}),
            validation_status=data.get("validation_status", "untested"),
            promotion_status=data.get("promotion_status", "disabled"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class MarketDatum:
    instrument: InstrumentId
    kind: MarketDataKind
    value: Decimal
    observed_at: datetime
    received_at: datetime
    source: str = ""
    sequence: Optional[int] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        kind = _enum(self.kind, MarketDataKind, "kind")
        value = _decimal(self.value, "value")
        observed_at = _utc(self.observed_at, "observed_at")
        received_at = _utc(self.received_at, "received_at")
        if received_at < observed_at:
            raise ValueError("received_at must not precede observed_at")
        if kind in _PRICE_KINDS and value <= 0:
            raise ValueError("price datum value must be greater than zero")
        if kind in {MarketDataKind.OPEN_INTEREST, MarketDataKind.VOLUME} and value < 0:
            raise ValueError(f"{kind.value} value must not be negative")
        source = self.source.strip() if isinstance(self.source, str) else None
        if source is None:
            raise TypeError("source must be a string")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "sequence", _sequence(self.sequence))
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    @property
    def data_type(self) -> MarketDataKind:
        return self.kind

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.instrument.to_dict(),
            "kind": self.kind.value,
            "value": str(self.value),
            "observed_at": _iso(self.observed_at),
            "received_at": _iso(self.received_at),
            "source": self.source,
            "sequence": self.sequence,
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MarketDatum:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            instrument=InstrumentId.from_dict(data["instrument"]),
            kind=data["kind"],
            value=data["value"],
            observed_at=_parse_utc(data["observed_at"], "observed_at"),
            received_at=_parse_utc(data["received_at"], "received_at"),
            source=data.get("source", ""),
            sequence=data.get("sequence"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    instrument: InstrumentId
    observed_at: datetime
    received_at: datetime
    data: Tuple[MarketDatum, ...]
    source: str = ""
    sequence: Optional[int] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        snapshot_id = _text(self.snapshot_id, "snapshot_id")
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        observed_at = _utc(self.observed_at, "observed_at")
        received_at = _utc(self.received_at, "received_at")
        if received_at < observed_at:
            raise ValueError("received_at must not precede observed_at")
        if not isinstance(self.data, (tuple, list)) or not self.data:
            raise ValueError("data must contain at least one MarketDatum")
        data = tuple(self.data)
        if not all(isinstance(datum, MarketDatum) for datum in data):
            raise TypeError("data must contain only MarketDatum values")
        if any(datum.instrument != self.instrument for datum in data):
            raise ValueError("all market data must use the same instrument as the snapshot")
        source = self.source.strip() if isinstance(self.source, str) else None
        if source is None:
            raise TypeError("source must be a string")
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "sequence", _sequence(self.sequence))
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    @property
    def event_id(self) -> str:
        return self.snapshot_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "instrument": self.instrument.to_dict(),
            "observed_at": _iso(self.observed_at),
            "received_at": _iso(self.received_at),
            "data": [datum.to_dict() for datum in self.data],
            "source": self.source,
            "sequence": self.sequence,
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MarketSnapshot:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            snapshot_id=data["snapshot_id"],
            instrument=InstrumentId.from_dict(data["instrument"]),
            observed_at=_parse_utc(data["observed_at"], "observed_at"),
            received_at=_parse_utc(data["received_at"], "received_at"),
            data=tuple(MarketDatum.from_dict(item) for item in data["data"]),
            source=data.get("source", ""),
            sequence=data.get("sequence"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    instrument: InstrumentId
    strategy_id: str
    action: DecisionAction
    decided_at: datetime
    snapshot_id: str
    side: Optional[OrderSide] = None
    confidence: Optional[Decimal] = None
    model_manifest_id: Optional[str] = None
    outcome: Optional[DecisionOutcome] = None
    reason_codes: Tuple[str, ...] = ()
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        decision_id = _text(self.decision_id, "decision_id")
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        strategy_id = _text(self.strategy_id, "strategy_id")
        action = _enum(self.action, DecisionAction, "action")
        decided_at = _utc(self.decided_at, "decided_at")
        snapshot_id = _text(self.snapshot_id, "snapshot_id")
        side = None if self.side is None else _enum(self.side, OrderSide, "side")
        confidence = _optional_decimal(self.confidence, "confidence")
        if confidence is not None and not Decimal("0") <= confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        expected_side = {
            DecisionAction.ENTER_LONG: OrderSide.BUY,
            DecisionAction.ENTER_SHORT: OrderSide.SELL,
        }.get(action)
        if expected_side is not None and side is not None and side is not expected_side:
            raise ValueError(f"{action.value} requires side={expected_side.value}")
        model_manifest_id = _optional_text(
            self.model_manifest_id, "model_manifest_id"
        )
        outcome = (
            None
            if self.outcome is None
            else _enum(self.outcome, DecisionOutcome, "outcome")
        )
        if not isinstance(self.reason_codes, (tuple, list)):
            raise TypeError("reason_codes must be a sequence of strings")
        reason_codes = tuple(_text(item, "reason_code") for item in self.reason_codes)
        if len(set(reason_codes)) != len(reason_codes):
            raise ValueError("reason_codes must not contain duplicates")
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "decided_at", decided_at)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "model_manifest_id", model_manifest_id)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    @property
    def event_id(self) -> str:
        return self.decision_id

    @property
    def occurred_at(self) -> datetime:
        return self.decided_at

    @property
    def is_terminal(self) -> bool:
        return self.outcome is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "instrument": self.instrument.to_dict(),
            "strategy_id": self.strategy_id,
            "action": self.action.value,
            "decided_at": _iso(self.decided_at),
            "snapshot_id": self.snapshot_id,
            "side": self.side.value if self.side is not None else None,
            "confidence": str(self.confidence) if self.confidence is not None else None,
            "model_manifest_id": self.model_manifest_id,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "reason_codes": list(self.reason_codes),
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DecisionRecord:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            decision_id=data["decision_id"],
            instrument=InstrumentId.from_dict(data["instrument"]),
            strategy_id=data["strategy_id"],
            action=data["action"],
            decided_at=_parse_utc(data["decided_at"], "decided_at"),
            snapshot_id=data["snapshot_id"],
            side=data.get("side"),
            confidence=data.get("confidence"),
            model_manifest_id=data.get("model_manifest_id"),
            outcome=data.get("outcome"),
            reason_codes=tuple(data.get("reason_codes", ())),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    decision_id: str
    instrument: InstrumentId
    created_at: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    client_order_id: Optional[str] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        intent_id = _text(self.intent_id, "intent_id")
        decision_id = _text(self.decision_id, "decision_id")
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        created_at = _utc(self.created_at, "created_at")
        side = _enum(self.side, OrderSide, "side")
        order_type = _enum(self.order_type, OrderType, "order_type")
        quantity = _decimal(self.quantity, "quantity")
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        limit_price = _optional_decimal(self.limit_price, "limit_price")
        stop_price = _optional_decimal(self.stop_price, "stop_price")
        if limit_price is not None and limit_price <= 0:
            raise ValueError("limit_price must be greater than zero")
        if stop_price is not None and stop_price <= 0:
            raise ValueError("stop_price must be greater than zero")
        limit_types = {OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT_LIMIT}
        stop_types = {
            OrderType.STOP_MARKET,
            OrderType.STOP_LIMIT,
            OrderType.TAKE_PROFIT_MARKET,
            OrderType.TAKE_PROFIT_LIMIT,
        }
        if order_type in limit_types and limit_price is None:
            raise ValueError(f"limit_price is required for {order_type.value}")
        if order_type in stop_types and stop_price is None:
            raise ValueError(f"stop_price is required for {order_type.value}")
        if order_type is OrderType.MARKET and limit_price is not None:
            raise ValueError("limit_price is not valid for market orders")
        time_in_force = _enum(self.time_in_force, TimeInForce, "time_in_force")
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be a bool")
        if not isinstance(self.post_only, bool):
            raise TypeError("post_only must be a bool")
        if self.post_only and order_type not in limit_types:
            raise ValueError("post_only is valid only for limit order types")
        client_order_id = _optional_text(self.client_order_id, "client_order_id")
        object.__setattr__(self, "intent_id", intent_id)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "stop_price", stop_price)
        object.__setattr__(self, "time_in_force", time_in_force)
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    @property
    def event_id(self) -> str:
        return self.intent_id

    @property
    def occurred_at(self) -> datetime:
        return self.created_at

    @property
    def price(self) -> Optional[Decimal]:
        return self.limit_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "decision_id": self.decision_id,
            "instrument": self.instrument.to_dict(),
            "created_at": _iso(self.created_at),
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": str(self.quantity),
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "stop_price": str(self.stop_price) if self.stop_price is not None else None,
            "time_in_force": self.time_in_force.value,
            "reduce_only": self.reduce_only,
            "post_only": self.post_only,
            "client_order_id": self.client_order_id,
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OrderIntent:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            intent_id=data["intent_id"],
            decision_id=data["decision_id"],
            instrument=InstrumentId.from_dict(data["instrument"]),
            created_at=_parse_utc(data["created_at"], "created_at"),
            side=data["side"],
            order_type=data["order_type"],
            quantity=data["quantity"],
            limit_price=data.get("limit_price"),
            stop_price=data.get("stop_price"),
            time_in_force=data.get("time_in_force", TimeInForce.GTC.value),
            reduce_only=data.get("reduce_only", False),
            post_only=data.get("post_only", False),
            client_order_id=data.get("client_order_id"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    intent_id: str
    instrument: InstrumentId
    event_type: ExecutionEventType
    occurred_at: datetime
    received_at: datetime
    decision_id: Optional[str] = None
    venue_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    sequence: Optional[int] = None
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    cumulative_quantity: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    fee_asset: Optional[str] = None
    rejection_reason: Optional[str] = None
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event_id = _text(self.event_id, "event_id")
        intent_id = _text(self.intent_id, "intent_id")
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        event_type = _enum(self.event_type, ExecutionEventType, "event_type")
        occurred_at = _utc(self.occurred_at, "occurred_at")
        received_at = _utc(self.received_at, "received_at")
        if received_at < occurred_at:
            raise ValueError("received_at must not precede occurred_at")
        decision_id = _optional_text(self.decision_id, "decision_id")
        venue_order_id = _optional_text(self.venue_order_id, "venue_order_id")
        client_order_id = _optional_text(self.client_order_id, "client_order_id")
        quantity = _optional_decimal(self.quantity, "quantity")
        price = _optional_decimal(self.price, "price")
        cumulative = _optional_decimal(self.cumulative_quantity, "cumulative_quantity")
        fee = _optional_decimal(self.fee, "fee")
        if quantity is not None and quantity < 0:
            raise ValueError("quantity must not be negative")
        if cumulative is not None and cumulative < 0:
            raise ValueError("cumulative_quantity must not be negative")
        if price is not None and price <= 0:
            raise ValueError("price must be greater than zero")
        if quantity is not None and cumulative is not None and cumulative < quantity:
            raise ValueError("cumulative_quantity must not be less than quantity")
        if event_type in {
            ExecutionEventType.PARTIALLY_FILLED,
            ExecutionEventType.FILLED,
        } and (quantity is None or quantity <= 0 or price is None):
            raise ValueError("fill events require positive quantity and price")
        rejection_reason = _optional_text(
            self.rejection_reason, "rejection_reason", max_length=2000
        )
        if event_type is ExecutionEventType.REJECTED and rejection_reason is None:
            raise ValueError("rejection_reason is required for rejected events")
        fee_asset = _optional_text(self.fee_asset, "fee_asset", max_length=32)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "intent_id", intent_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "venue_order_id", venue_order_id)
        object.__setattr__(self, "client_order_id", client_order_id)
        object.__setattr__(self, "sequence", _sequence(self.sequence))
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "cumulative_quantity", cumulative)
        object.__setattr__(self, "fee", fee)
        object.__setattr__(self, "fee_asset", fee_asset)
        object.__setattr__(self, "rejection_reason", rejection_reason)
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "intent_id": self.intent_id,
            "decision_id": self.decision_id,
            "instrument": self.instrument.to_dict(),
            "event_type": self.event_type.value,
            "occurred_at": _iso(self.occurred_at),
            "received_at": _iso(self.received_at),
            "venue_order_id": self.venue_order_id,
            "client_order_id": self.client_order_id,
            "sequence": self.sequence,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "price": str(self.price) if self.price is not None else None,
            "cumulative_quantity": (
                str(self.cumulative_quantity)
                if self.cumulative_quantity is not None else None
            ),
            "fee": str(self.fee) if self.fee is not None else None,
            "fee_asset": self.fee_asset,
            "rejection_reason": self.rejection_reason,
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionEvent:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            event_id=data["event_id"],
            intent_id=data["intent_id"],
            decision_id=data.get("decision_id"),
            instrument=InstrumentId.from_dict(data["instrument"]),
            event_type=data["event_type"],
            occurred_at=_parse_utc(data["occurred_at"], "occurred_at"),
            received_at=_parse_utc(data["received_at"], "received_at"),
            venue_order_id=data.get("venue_order_id"),
            client_order_id=data.get("client_order_id"),
            sequence=data.get("sequence"),
            quantity=data.get("quantity"),
            price=data.get("price"),
            cumulative_quantity=data.get("cumulative_quantity"),
            fee=data.get("fee"),
            fee_asset=data.get("fee_asset"),
            rejection_reason=data.get("rejection_reason"),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class ModelManifest:
    manifest_id: str
    model_name: str
    model_version: str
    created_at: datetime
    artifact_uri: str
    artifact_sha256: str
    feature_schema_version: str
    training_data_version: str
    code_version: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        manifest_id = _text(self.manifest_id, "manifest_id")
        model_name = _text(self.model_name, "model_name")
        model_version = _text(self.model_version, "model_version")
        created_at = _utc(self.created_at, "created_at")
        artifact_uri = _text(self.artifact_uri, "artifact_uri", max_length=2000)
        artifact_sha256 = _text(self.artifact_sha256, "artifact_sha256").lower()
        if not _SHA256_RE.fullmatch(artifact_sha256):
            raise ValueError("artifact_sha256 must be 64 hexadecimal characters")
        object.__setattr__(self, "manifest_id", manifest_id)
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "artifact_uri", artifact_uri)
        object.__setattr__(self, "artifact_sha256", artifact_sha256)
        object.__setattr__(
            self, "feature_schema_version",
            _text(self.feature_schema_version, "feature_schema_version"),
        )
        object.__setattr__(
            self, "training_data_version",
            _text(self.training_data_version, "training_data_version"),
        )
        object.__setattr__(self, "code_version", _text(self.code_version, "code_version"))
        object.__setattr__(self, "metrics", _freeze_mapping(self.metrics, "metrics"))
        object.__setattr__(
            self, "parameters", _freeze_mapping(self.parameters, "parameters")
        )
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "created_at": _iso(self.created_at),
            "artifact_uri": self.artifact_uri,
            "artifact_sha256": self.artifact_sha256,
            "feature_schema_version": self.feature_schema_version,
            "training_data_version": self.training_data_version,
            "code_version": self.code_version,
            "metrics": _thaw_json(self.metrics),
            "parameters": _thaw_json(self.parameters),
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ModelManifest:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            manifest_id=data["manifest_id"],
            model_name=data["model_name"],
            model_version=data["model_version"],
            created_at=_parse_utc(data["created_at"], "created_at"),
            artifact_uri=data["artifact_uri"],
            artifact_sha256=data["artifact_sha256"],
            feature_schema_version=data["feature_schema_version"],
            training_data_version=data["training_data_version"],
            code_version=data["code_version"],
            metrics=data.get("metrics", {}),
            parameters=data.get("parameters", {}),
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class FundingSettlement:
    settlement_id: str
    instrument: InstrumentId
    position_id: str
    occurred_at: datetime
    received_at: datetime
    rate: Decimal
    amount: Decimal
    asset: str
    schema_version: int = CONTRACT_SCHEMA_VERSION
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        settlement_id = _text(self.settlement_id, "settlement_id")
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        position_id = _text(self.position_id, "position_id")
        occurred_at = _utc(self.occurred_at, "occurred_at")
        received_at = _utc(self.received_at, "received_at")
        if received_at < occurred_at:
            raise ValueError("received_at must not precede occurred_at")
        asset = _text(self.asset, "asset", max_length=32).upper()
        object.__setattr__(self, "settlement_id", settlement_id)
        object.__setattr__(self, "position_id", position_id)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "rate", _decimal(self.rate, "rate"))
        object.__setattr__(self, "amount", _decimal(self.amount, "amount"))
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "schema_version", _version(self.schema_version))
        object.__setattr__(self, "context", _freeze_mapping(self.context, "context"))

    @property
    def event_id(self) -> str:
        return self.settlement_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "instrument": self.instrument.to_dict(),
            "position_id": self.position_id,
            "occurred_at": _iso(self.occurred_at),
            "received_at": _iso(self.received_at),
            "rate": str(self.rate),
            "amount": str(self.amount),
            "asset": self.asset,
            "schema_version": self.schema_version,
            "context": _thaw_json(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FundingSettlement:
        data = _mapping_payload(payload, cls.__name__)
        return cls(
            settlement_id=data["settlement_id"],
            instrument=InstrumentId.from_dict(data["instrument"]),
            position_id=data["position_id"],
            occurred_at=_parse_utc(data["occurred_at"], "occurred_at"),
            received_at=_parse_utc(data["received_at"], "received_at"),
            rate=data["rate"],
            amount=data["amount"],
            asset=data["asset"],
            schema_version=data.get("schema_version", CONTRACT_SCHEMA_VERSION),
            context=data.get("context", {}),
        )


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "DecisionAction",
    "DecisionRecord",
    "ExecutionEvent",
    "ExecutionEventType",
    "FundingSettlement",
    "InstrumentId",
    "MarketDataKind",
    "MarketDatum",
    "MarketSnapshot",
    "MarketType",
    "ModelManifest",
    "OrderIntent",
    "StrategySpec",
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "Venue",
]
