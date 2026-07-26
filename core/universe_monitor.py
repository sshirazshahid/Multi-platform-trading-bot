"""Point-in-time USDT perpetual universe monitor for shadow research.

The monitor has no order or position-management dependency.  It performs one
batched ``fetch_tickers(market_type="futures")`` call per venue, validates and
normalizes the returned contracts, and persists compact ticker snapshots in
SQLite.  Subsequent scans reconstruct 1h, 24h, and 7d percentage returns from
snapshots that were actually observable at the time.  A bounded, direction-
balanced shortlist is intended for deeper OHLCV/order-book analysis in the
log-only shadow lane; it must never be used to expand an executable universe.

Each scan also snapshots the exchange clients' already-loaded ``markets``
metadata into a durable contract master.  That path never loads or refreshes
markets, and it preserves inactive/disappeared rows without treating absence
or first observation as proof of a listing or delisting date.

The exchange-provided 24h ``percentage`` is used only as a cold-start seed when
there is no suitably timed point-in-time snapshot.  The source is carried on
the result so research can distinguish it from a locally reconstructed return.
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from loguru import logger

DEFAULT_DB_PATH = Path("data/universe_monitor.sqlite")
DEFAULT_HORIZONS_MS: Tuple[Tuple[str, int], ...] = (
    ("1h", 60 * 60 * 1000),
    ("24h", 24 * 60 * 60 * 1000),
    ("7d", 7 * 24 * 60 * 60 * 1000),
)


@dataclass(frozen=True)
class UniverseMonitorConfig:
    """Resource and data-quality limits for :class:`UniverseMonitor`."""

    min_quote_volume_usdt: float = 5_000_000.0
    max_ticker_age_s: float = 180.0
    max_future_skew_s: float = 30.0
    reference_tolerance_s: float = 30 * 60.0
    retention_days: float = 8.0
    max_abs_return_pct: float = 5_000.0
    per_direction_per_horizon: int = 3
    max_shortlist: int = 18
    max_contracts_per_venue: int = 5_000
    horizons_ms: Tuple[Tuple[str, int], ...] = field(
        default_factory=lambda: DEFAULT_HORIZONS_MS
    )

    def __post_init__(self) -> None:
        if self.min_quote_volume_usdt < 0:
            raise ValueError("min_quote_volume_usdt must not be negative")
        if self.max_ticker_age_s <= 0:
            raise ValueError("max_ticker_age_s must be positive")
        if self.max_future_skew_s < 0:
            raise ValueError("max_future_skew_s must not be negative")
        if self.reference_tolerance_s < 0:
            raise ValueError("reference_tolerance_s must not be negative")
        if self.max_abs_return_pct <= 0:
            raise ValueError("max_abs_return_pct must be positive")
        if self.per_direction_per_horizon < 0:
            raise ValueError("per_direction_per_horizon must not be negative")
        if self.max_shortlist < 0:
            raise ValueError("max_shortlist must not be negative")
        if self.max_contracts_per_venue <= 0:
            raise ValueError("max_contracts_per_venue must be positive")
        if not self.horizons_ms:
            raise ValueError("at least one horizon is required")
        labels = [label for label, _ in self.horizons_ms]
        if len(labels) != len(set(labels)):
            raise ValueError("horizon labels must be unique")
        if any(not label or duration <= 0 for label, duration in self.horizons_ms):
            raise ValueError("horizons must have a label and positive duration")
        required_retention_ms = max(duration for _, duration in self.horizons_ms)
        required_retention_ms += int(self.reference_tolerance_s * 1000)
        if self.retention_days * 86_400_000 < required_retention_ms:
            raise ValueError("retention_days must cover the longest horizon and tolerance")


@dataclass(frozen=True)
class NormalizedTicker:
    venue: str
    symbol: str
    base: str
    observed_at_ms: int
    source_at_ms: int
    timestamp_basis: str
    price: float
    quote_volume_usdt: float
    ticker_return_24h_pct: Optional[float]


@dataclass(frozen=True)
class ContractMetadata:
    """One observed USDT-linear perpetual from an exchange's loaded markets.

    ``listed_at_ms`` and ``delisted_at_ms`` are deliberately nullable.  The
    monitor records only timestamps explicitly supplied by the venue/CCXT
    market mapping; ``first_seen_at_ms`` is a discovery timestamp, not a
    synthetic listing timestamp.
    """

    venue: str
    symbol: str
    market_id: Optional[str]
    base: str
    quote: str
    settle: str
    linear: bool
    swap: bool
    active: Optional[bool]
    contract_size: Optional[float]
    contract_multiplier: Optional[float]
    price_tick: Optional[float]
    amount_step: Optional[float]
    min_notional: Optional[float]
    listed_at_ms: Optional[int]
    delisted_at_ms: Optional[int]


@dataclass(frozen=True)
class ReturnPoint:
    value_pct: float
    source: str
    reference_at_ms: Optional[int]


@dataclass(frozen=True)
class ShortlistEntry:
    """One deduplicated contract selected for deeper, read-only analysis."""

    venue: str
    symbol: str
    base: str
    price: float
    quote_volume_usdt: float
    observed_at_ms: int
    selected_horizon: str
    direction: str
    selected_return_pct: float
    selected_return_source: str
    returns_pct: Mapping[str, float]
    return_sources: Mapping[str, str]


@dataclass(frozen=True)
class UniverseScanResult:
    observed_at_ms: int
    venues_requested: int
    venue_fetches: int
    raw_tickers: int
    accepted_tickers: int
    rejection_counts: Mapping[str, int]
    venue_errors: Mapping[str, str]
    shortlist: Tuple[ShortlistEntry, ...]
    contract_counts: Mapping[str, int] = field(default_factory=dict)
    contract_metadata_coverage: Mapping[str, float] = field(default_factory=dict)

    @property
    def symbols(self) -> Tuple[str, ...]:
        return tuple(row.symbol for row in self.shortlist)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_ticker_snapshots (
    venue                  TEXT    NOT NULL,
    symbol                 TEXT    NOT NULL,
    base                   TEXT    NOT NULL,
    observed_at_ms         INTEGER NOT NULL,
    source_at_ms           INTEGER NOT NULL,
    timestamp_basis        TEXT    NOT NULL,
    price                  REAL    NOT NULL,
    quote_volume_usdt      REAL    NOT NULL,
    ticker_return_24h_pct  REAL,
    PRIMARY KEY (venue, symbol, observed_at_ms)
);
CREATE INDEX IF NOT EXISTS idx_universe_snapshot_lookup
    ON universe_ticker_snapshots (venue, symbol, observed_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_universe_snapshot_retention
    ON universe_ticker_snapshots (observed_at_ms);

CREATE TABLE IF NOT EXISTS universe_contract_master (
    venue                    TEXT    NOT NULL,
    symbol                   TEXT    NOT NULL,
    market_id                TEXT,
    base                     TEXT,
    quote                    TEXT,
    settle                   TEXT,
    linear                   INTEGER,
    swap                     INTEGER,
    active                   INTEGER,
    contract_size            REAL,
    contract_multiplier      REAL,
    price_tick               REAL,
    amount_step              REAL,
    min_notional             REAL,
    listed_at_ms             INTEGER,
    delisted_at_ms           INTEGER,
    first_seen_at_ms         INTEGER,
    last_seen_at_ms          INTEGER,
    PRIMARY KEY (venue, symbol)
);
"""

_CONTRACT_MASTER_COLUMNS = {
    "market_id": "TEXT",
    "base": "TEXT",
    "quote": "TEXT",
    "settle": "TEXT",
    "linear": "INTEGER",
    "swap": "INTEGER",
    "active": "INTEGER",
    "contract_size": "REAL",
    "contract_multiplier": "REAL",
    "price_tick": "REAL",
    "amount_step": "REAL",
    "min_notional": "REAL",
    "listed_at_ms": "INTEGER",
    "delisted_at_ms": "INTEGER",
    "first_seen_at_ms": "INTEGER",
    "last_seen_at_ms": "INTEGER",
}

_CONTRACT_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_contract_master_active
    ON universe_contract_master (venue, active, symbol);
CREATE INDEX IF NOT EXISTS idx_contract_master_last_seen
    ON universe_contract_master (last_seen_at_ms);
"""


class _SnapshotStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Create the schema and add newly introduced nullable columns.

        SQLite's ``CREATE TABLE IF NOT EXISTS`` does not update an older table.
        Explicit additive migration keeps databases created by early shadow
        builds readable without dropping their point-in-time history.
        """

        conn.executescript(_SCHEMA)
        existing = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(universe_contract_master)")
        }
        for name, column_type in _CONTRACT_MASTER_COLUMNS.items():
            if name not in existing:
                conn.execute(
                    f'ALTER TABLE universe_contract_master ADD COLUMN "{name}" '
                    f"{column_type}"
                )
        conn.executescript(_CONTRACT_INDEX_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def persist_and_load_references(
        self,
        records: Sequence[NormalizedTicker],
        *,
        now_ms: int,
        horizons_ms: Sequence[Tuple[str, int]],
        tolerance_ms: int,
        retention_ms: int,
    ) -> Dict[Tuple[str, str, str], Tuple[int, float]]:
        """Atomically persist a scan and resolve all historical points.

        A temporary contracts table and indexed correlated lookup keep this to
        one local SQL result set rather than loading a week of snapshots into
        Python or making any per-symbol network request.
        """

        if not records:
            return {}
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.executemany(
                """
                INSERT INTO universe_ticker_snapshots (
                    venue, symbol, base, observed_at_ms, source_at_ms,
                    timestamp_basis, price, quote_volume_usdt,
                    ticker_return_24h_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, symbol, observed_at_ms) DO UPDATE SET
                    base=excluded.base,
                    source_at_ms=excluded.source_at_ms,
                    timestamp_basis=excluded.timestamp_basis,
                    price=excluded.price,
                    quote_volume_usdt=excluded.quote_volume_usdt,
                    ticker_return_24h_pct=excluded.ticker_return_24h_pct
                """,
                [
                    (
                        row.venue,
                        row.symbol,
                        row.base,
                        row.observed_at_ms,
                        row.source_at_ms,
                        row.timestamp_basis,
                        row.price,
                        row.quote_volume_usdt,
                        row.ticker_return_24h_pct,
                    )
                    for row in records
                ],
            )
            conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS current_universe_contracts "
                "(venue TEXT NOT NULL, symbol TEXT NOT NULL, "
                "PRIMARY KEY (venue, symbol)) WITHOUT ROWID"
            )
            conn.execute("DELETE FROM current_universe_contracts")
            conn.executemany(
                "INSERT OR IGNORE INTO current_universe_contracts VALUES (?, ?)",
                [(row.venue, row.symbol) for row in records],
            )

            values_sql = ", ".join("(?, ?)" for _ in horizons_ms)
            params: list[Any] = []
            for label, duration_ms in horizons_ms:
                params.extend((label, now_ms - int(duration_ms)))
            params.append(int(tolerance_ms))
            query = f"""
                WITH targets(label, target_ms) AS (VALUES {values_sql})
                SELECT c.venue, c.symbol, targets.label,
                       snap.observed_at_ms, snap.price
                FROM current_universe_contracts AS c
                CROSS JOIN targets
                LEFT JOIN universe_ticker_snapshots AS snap
                  ON snap.rowid = (
                    SELECT hist.rowid
                    FROM universe_ticker_snapshots AS hist
                    WHERE hist.venue = c.venue
                      AND hist.symbol = c.symbol
                      AND hist.observed_at_ms <= targets.target_ms
                      AND hist.observed_at_ms >= targets.target_ms - ?
                    ORDER BY hist.observed_at_ms DESC
                    LIMIT 1
                  )
            """
            references: Dict[Tuple[str, str, str], Tuple[int, float]] = {}
            for venue, symbol, label, observed_at_ms, price in conn.execute(query, params):
                if observed_at_ms is not None and price is not None:
                    references[(venue, symbol, label)] = (
                        int(observed_at_ms), float(price)
                    )
            conn.execute(
                "DELETE FROM universe_ticker_snapshots WHERE observed_at_ms < ?",
                (now_ms - int(retention_ms),),
            )
            return references

    def persist_contract_master(
        self,
        records: Sequence[ContractMetadata],
        *,
        now_ms: int,
    ) -> Tuple[Dict[str, int], Dict[str, float]]:
        """Upsert observed metadata and return durable state diagnostics.

        Rows absent from a later market mapping are intentionally retained and
        left unchanged.  Absence is not evidence of a delisting.  Likewise,
        nullable values never erase older explicit metadata.
        """

        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.executemany(
                """
                INSERT INTO universe_contract_master (
                    venue, symbol, market_id, base, quote, settle, linear, swap,
                    active, contract_size, contract_multiplier, price_tick,
                    amount_step, min_notional, listed_at_ms, delisted_at_ms,
                    first_seen_at_ms, last_seen_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(venue, symbol) DO UPDATE SET
                    market_id=COALESCE(excluded.market_id,
                                       universe_contract_master.market_id),
                    base=COALESCE(excluded.base, universe_contract_master.base),
                    quote=COALESCE(excluded.quote, universe_contract_master.quote),
                    settle=COALESCE(excluded.settle, universe_contract_master.settle),
                    linear=COALESCE(excluded.linear, universe_contract_master.linear),
                    swap=COALESCE(excluded.swap, universe_contract_master.swap),
                    active=COALESCE(excluded.active, universe_contract_master.active),
                    contract_size=COALESCE(excluded.contract_size,
                                           universe_contract_master.contract_size),
                    contract_multiplier=COALESCE(
                        excluded.contract_multiplier,
                        universe_contract_master.contract_multiplier
                    ),
                    price_tick=COALESCE(excluded.price_tick,
                                        universe_contract_master.price_tick),
                    amount_step=COALESCE(excluded.amount_step,
                                         universe_contract_master.amount_step),
                    min_notional=COALESCE(excluded.min_notional,
                                          universe_contract_master.min_notional),
                    listed_at_ms=CASE
                        WHEN universe_contract_master.listed_at_ms IS NULL
                            THEN excluded.listed_at_ms
                        WHEN excluded.listed_at_ms IS NULL
                            THEN universe_contract_master.listed_at_ms
                        ELSE MIN(universe_contract_master.listed_at_ms,
                                 excluded.listed_at_ms)
                    END,
                    delisted_at_ms=CASE
                        WHEN universe_contract_master.delisted_at_ms IS NULL
                            THEN excluded.delisted_at_ms
                        WHEN excluded.delisted_at_ms IS NULL
                            THEN universe_contract_master.delisted_at_ms
                        ELSE MAX(universe_contract_master.delisted_at_ms,
                                 excluded.delisted_at_ms)
                    END,
                    last_seen_at_ms=excluded.last_seen_at_ms
                """,
                [
                    (
                        row.venue,
                        row.symbol,
                        row.market_id,
                        row.base,
                        row.quote,
                        row.settle,
                        int(row.linear),
                        int(row.swap),
                        None if row.active is None else int(row.active),
                        row.contract_size,
                        row.contract_multiplier,
                        row.price_tick,
                        row.amount_step,
                        row.min_notional,
                        row.listed_at_ms,
                        row.delisted_at_ms,
                        int(now_ms),
                        int(now_ms),
                    )
                    for row in records
                ],
            )
            state = conn.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(active = 1), 0),
                    COALESCE(SUM(active = 0), 0),
                    COALESCE(SUM(active IS NULL), 0),
                    COALESCE(SUM(active = 0 OR delisted_at_ms IS NOT NULL), 0)
                FROM universe_contract_master
                """
            ).fetchone()
            observed_keys = {(row.venue, row.symbol) for row in records}
            persisted_observed = 0
            if observed_keys:
                conn.execute(
                    "CREATE TEMP TABLE current_contract_metadata "
                    "(venue TEXT NOT NULL, symbol TEXT NOT NULL, "
                    "PRIMARY KEY (venue, symbol)) WITHOUT ROWID"
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO current_contract_metadata VALUES (?, ?)",
                    sorted(observed_keys),
                )
                persisted_observed = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM universe_contract_master AS master
                        JOIN current_contract_metadata AS current
                          ON current.venue = master.venue
                         AND current.symbol = master.symbol
                        """
                    ).fetchone()[0]
                )

        total = int(state[0] if state else 0)
        counts = {
            "observed_contracts": len(records),
            "persisted_total": total,
            "persisted_active": int(state[1] if state else 0),
            "persisted_inactive": int(state[2] if state else 0),
            "persisted_active_unknown": int(state[3] if state else 0),
            "persisted_inactive_or_delisted": int(state[4] if state else 0),
            "retained_not_seen_this_scan": max(0, total - persisted_observed),
        }
        fields = (
            "market_id",
            "active",
            "contract_size",
            "contract_multiplier",
            "price_tick",
            "amount_step",
            "min_notional",
            "listed_at_ms",
            "delisted_at_ms",
        )
        denominator = len(records)
        coverage = {
            name: (
                sum(getattr(row, name) is not None for row in records) / denominator
                if denominator
                else 0.0
            )
            for name in fields
        }
        return counts, coverage

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM universe_ticker_snapshots"
            ).fetchone()
            return int(row[0] if row else 0)

    def contract_count(self) -> int:
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT COUNT(*) FROM universe_contract_master"
            ).fetchone()
            return int(row[0] if row else 0)


def _finite_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _first_finite(*values: Any) -> Optional[float]:
    for value in values:
        number = _finite_float(value)
        if number is not None:
            return number
    return None


def _timestamp_ms(value: Any) -> Optional[int]:
    number = _finite_float(value)
    if number is None or number <= 0:
        return None
    # Seconds and microseconds occasionally leak through raw venue payloads.
    if number < 100_000_000_000:
        number *= 1000.0
    elif number > 100_000_000_000_000:
        number /= 1000.0
    return int(number)


def _iso_timestamp_ms(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_float(value: Any) -> Optional[float]:
    number = _finite_float(value)
    if number is None or number <= 0:
        return None
    return number


def _mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Get a metadata value without assuming one venue's key casing."""

    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    normalized = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = normalized.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _explicit_market_timestamp_ms(
    market: Mapping[str, Any],
    keys: Sequence[str],
) -> Optional[int]:
    """Read a venue-declared lifecycle timestamp, never an observation time."""

    info = market.get("info") if isinstance(market.get("info"), Mapping) else {}
    for source in (market, info):
        value = _mapping_value(source, *keys)
        parsed = _timestamp_ms(value)
        if parsed is None:
            parsed = _iso_timestamp_ms(value)
        if parsed is not None:
            return parsed
    return None


_LISTING_TIMESTAMP_KEYS = (
    "listed_at_ms",
    "listedAt",
    "listingTimestamp",
    "listingTime",
    "listTime",
    "onboardDate",
    "launchTime",
)
_DELISTING_TIMESTAMP_KEYS = (
    "delisted_at_ms",
    "delistedAt",
    "delistTime",
    "delistingTime",
    "offlineTime",
    "expiry",
    "expiryDatetime",
    "deliveryTime",
)


def _precision_increment(value: Any, precision_mode: Any) -> Optional[float]:
    """Convert CCXT precision metadata to an increment when unambiguous."""

    number = _finite_float(value)
    if number is None or number < 0:
        return None
    mode = str(precision_mode).strip().upper()
    # CCXT DECIMAL_PLACES is integer constant 2; TICK_SIZE is constant 4.
    if precision_mode == 2 or mode == "DECIMAL_PLACES":
        if number.is_integer():
            return 10.0 ** -int(number)
        return None
    if precision_mode == 3 or mode == "SIGNIFICANT_DIGITS":
        # A significant-digit count does not define one absolute tick.
        return None
    if precision_mode == 4 or mode == "TICK_SIZE" or precision_mode is None:
        return number if number > 0 else None
    return None


def normalize_contract_metadata(
    venue: str,
    raw_key: str,
    market: Mapping[str, Any],
    *,
    precision_mode: Any = None,
) -> Tuple[Optional[ContractMetadata], Optional[str]]:
    """Normalize one already-loaded market into the perpetual contract master."""

    if not isinstance(market, Mapping):
        return None, "invalid_market"
    is_swap = market.get("swap") is True or str(market.get("type") or "").lower() == "swap"
    if not is_swap:
        return None, "not_perpetual"
    if market.get("linear") is not True:
        return None, "not_explicitly_linear"
    quote = str(market.get("quote") or "").upper()
    settle = str(market.get("settle") or "").upper()
    if quote != "USDT" or settle != "USDT":
        return None, "not_usdt_settled"
    symbol = str(market.get("symbol") or raw_key or "")
    if not symbol.upper().endswith("/USDT:USDT"):
        return None, "noncanonical_symbol"
    base = str(market.get("base") or symbol.split("/", 1)[0]).upper()
    if not base or base == "USDT":
        return None, "invalid_base"

    active_raw = market.get("active")
    active = active_raw if isinstance(active_raw, bool) else None
    market_id_raw = market.get("id")
    market_id = (
        str(market_id_raw).strip()
        if market_id_raw not in (None, "") and str(market_id_raw).strip()
        else None
    )
    info = market.get("info") if isinstance(market.get("info"), Mapping) else {}
    precision = (
        market.get("precision") if isinstance(market.get("precision"), Mapping) else {}
    )
    limits = market.get("limits") if isinstance(market.get("limits"), Mapping) else {}
    cost_limits = limits.get("cost") if isinstance(limits.get("cost"), Mapping) else {}

    explicit_price_tick = _positive_float(
        _mapping_value(info, "tickSize", "priceTick", "priceStep")
    )
    if explicit_price_tick is None:
        # Bitget exposes the final-price digit and decimal-place count as two
        # separate explicit fields (for example 5 and 2 means a 0.05 tick).
        price_end_step = _positive_float(_mapping_value(info, "priceEndStep"))
        price_places = _finite_float(_mapping_value(info, "pricePlace"))
        if (
            price_end_step is not None
            and price_places is not None
            and price_places >= 0
            and price_places.is_integer()
        ):
            explicit_price_tick = price_end_step * (10.0 ** -int(price_places))
    explicit_amount_step = _positive_float(
        _mapping_value(
            info,
            "qtyStep",
            "quantityStep",
            "amountStep",
            "sizeMultiplier",
        )
    )
    price_tick = explicit_price_tick or _precision_increment(
        precision.get("price"), precision_mode
    )
    amount_step = explicit_amount_step or _precision_increment(
        precision.get("amount"), precision_mode
    )
    min_notional = _positive_float(cost_limits.get("min")) or _positive_float(
        _mapping_value(
            info,
            "minNotional",
            "minOrderValue",
            "minTradeUSDT",
        )
    )
    contract_size = _positive_float(market.get("contractSize")) or _positive_float(
        _mapping_value(info, "contractSize", "contractValue", "ctVal")
    )
    contract_multiplier = _positive_float(
        _mapping_value(
            market,
            "contractMultiplier",
            "multiplier",
        )
    ) or _positive_float(
        _mapping_value(
            info,
            "contractMultiplier",
            "multiplier",
        )
    )

    return ContractMetadata(
        venue=str(venue).strip().lower(),
        symbol=symbol,
        market_id=market_id,
        base=base,
        quote=quote,
        settle=settle,
        linear=True,
        swap=True,
        active=active,
        contract_size=contract_size,
        contract_multiplier=contract_multiplier,
        price_tick=price_tick,
        amount_step=amount_step,
        min_notional=min_notional,
        listed_at_ms=_explicit_market_timestamp_ms(market, _LISTING_TIMESTAMP_KEYS),
        delisted_at_ms=_explicit_market_timestamp_ms(
            market, _DELISTING_TIMESTAMP_KEYS
        ),
    ), None


def _loaded_markets(exchange: Any) -> Mapping[str, Any]:
    client = getattr(exchange, "exchange", exchange)
    markets = getattr(client, "markets", None) or {}
    return markets if isinstance(markets, Mapping) else {}


def _contract_metadata_rows(
    venue: str,
    exchange: Any,
) -> Tuple[list[ContractMetadata], int, int, int]:
    """Read the in-memory market map; this function performs no I/O."""

    client = getattr(exchange, "exchange", exchange)
    precision_mode = getattr(client, "precisionMode", None)
    markets = _loaded_markets(exchange)
    by_symbol: Dict[str, ContractMetadata] = {}
    nonqualifying = 0
    duplicate_aliases = 0
    for raw_key, market in markets.items():
        normalized, _reason = normalize_contract_metadata(
            venue,
            str(raw_key),
            market,
            precision_mode=precision_mode,
        )
        if normalized is None:
            nonqualifying += 1
            continue
        previous = by_symbol.get(normalized.symbol)
        if previous is not None:
            duplicate_aliases += 1
            # Alias-keyed maps occasionally contain a sparse and a full copy.
            # Keep the most complete observation without merging invented data.
            previous_score = sum(
                value is not None
                for value in (
                    previous.market_id,
                    previous.active,
                    previous.contract_size,
                    previous.contract_multiplier,
                    previous.price_tick,
                    previous.amount_step,
                    previous.min_notional,
                    previous.listed_at_ms,
                    previous.delisted_at_ms,
                )
            )
            normalized_score = sum(
                value is not None
                for value in (
                    normalized.market_id,
                    normalized.active,
                    normalized.contract_size,
                    normalized.contract_multiplier,
                    normalized.price_tick,
                    normalized.amount_step,
                    normalized.min_notional,
                    normalized.listed_at_ms,
                    normalized.delisted_at_ms,
                )
            )
            if normalized_score <= previous_score:
                continue
        by_symbol[normalized.symbol] = normalized
    return (
        sorted(by_symbol.values(), key=lambda row: row.symbol),
        len(markets),
        nonqualifying,
        duplicate_aliases,
    )


def _market_lookup(exchange: Any) -> Dict[str, Mapping[str, Any]]:
    markets = _loaded_markets(exchange)
    out: Dict[str, Mapping[str, Any]] = {}
    if not isinstance(markets, Mapping):
        return out
    for key, market in markets.items():
        if not isinstance(market, Mapping):
            continue
        for candidate in (key, market.get("symbol"), market.get("id")):
            if candidate:
                out[str(candidate)] = market
    return out


def _canonical_contract(
    raw_symbol: str,
    ticker: Mapping[str, Any],
    market: Optional[Mapping[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(symbol, base, rejection_reason)`` for a USDT linear perp."""

    if market is not None:
        is_swap = bool(market.get("swap") or market.get("type") == "swap")
        if not is_swap:
            return None, None, "not_perpetual"
        if market.get("active") is False:
            return None, None, "inactive_contract"
        if market.get("linear") is False:
            return None, None, "not_linear"
        quote = str(market.get("quote") or "").upper()
        settle = str(market.get("settle") or quote).upper()
        if quote != "USDT" or settle != "USDT":
            return None, None, "not_usdt_settled"
        symbol = str(market.get("symbol") or ticker.get("symbol") or raw_symbol)
        base = str(market.get("base") or symbol.split("/", 1)[0]).upper()
    else:
        symbol = str(ticker.get("symbol") or raw_symbol)
        # Without market metadata, only CCXT's unambiguous linear-swap form is
        # accepted.  ``BTC/USDT`` and ``BTCUSDT`` could be spot or dated future.
        if "/USDT:USDT" not in symbol.upper():
            return None, None, "unverified_contract_type"
        base = symbol.split("/", 1)[0].upper()
    if not base or base == "USDT":
        return None, None, "invalid_base"
    if not symbol.upper().endswith("/USDT:USDT"):
        return None, None, "not_usdt_settled"
    return symbol, base, None


def normalize_ticker(
    venue: str,
    raw_symbol: str,
    ticker: Mapping[str, Any],
    *,
    now_ms: int,
    config: UniverseMonitorConfig,
    market: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[NormalizedTicker], Optional[str]]:
    """Normalize one ticker, returning a stable rejection reason on failure."""

    if not isinstance(ticker, Mapping):
        return None, "invalid_ticker"
    symbol, base, reason = _canonical_contract(raw_symbol, ticker, market)
    if reason:
        return None, reason

    info = ticker.get("info") if isinstance(ticker.get("info"), Mapping) else {}
    price = _first_finite(
        ticker.get("last"), ticker.get("close"), info.get("lastPrice"),
        info.get("lastPr"), info.get("markPrice"),
    )
    if price is None or price <= 0:
        return None, "missing_or_invalid_price"

    quote_volume = _first_finite(
        ticker.get("quoteVolume"), info.get("quoteVolume"),
        info.get("turnover24h"), info.get("turnover24"),
        info.get("quoteVol"), info.get("usdtVolume"),
    )
    if quote_volume is None:
        base_volume = _first_finite(ticker.get("baseVolume"), info.get("baseVolume"))
        if base_volume is not None and base_volume >= 0:
            quote_volume = base_volume * price
    if quote_volume is None or quote_volume < 0:
        return None, "missing_or_invalid_quote_volume"
    if quote_volume < config.min_quote_volume_usdt:
        return None, "low_quote_volume"

    source_at_ms = _timestamp_ms(ticker.get("timestamp"))
    timestamp_basis = "exchange"
    if source_at_ms is None:
        source_at_ms = _iso_timestamp_ms(ticker.get("datetime"))
    if source_at_ms is None:
        # The batch response was just received, so receipt time is a valid
        # freshness bound even when an exchange omits per-ticker timestamps.
        source_at_ms = int(now_ms)
        timestamp_basis = "batch_received"
    age_ms = now_ms - source_at_ms
    if age_ms > config.max_ticker_age_s * 1000:
        return None, "stale_ticker"
    if age_ms < -config.max_future_skew_s * 1000:
        return None, "future_ticker_timestamp"

    ticker_return_24h = _finite_float(ticker.get("percentage"))
    if ticker_return_24h is None:
        open_price = _first_finite(ticker.get("open"), info.get("openPrice"))
        if open_price is not None and open_price > 0:
            ticker_return_24h = (price / open_price - 1.0) * 100.0
    if (
        ticker_return_24h is not None
        and abs(ticker_return_24h) > config.max_abs_return_pct
    ):
        ticker_return_24h = None

    return NormalizedTicker(
        venue=str(venue).strip().lower(),
        symbol=str(symbol),
        base=str(base),
        observed_at_ms=int(now_ms),
        source_at_ms=int(source_at_ms),
        timestamp_basis=timestamp_basis,
        price=float(price),
        quote_volume_usdt=float(quote_volume),
        ticker_return_24h_pct=ticker_return_24h,
    ), None


class UniverseMonitor:
    """Batched, persistent, bounded scanner for the shadow/research lane."""

    research_only = True

    def __init__(
        self,
        exchanges: Mapping[str, Any],
        *,
        db_path: Path = DEFAULT_DB_PATH,
        config: Optional[UniverseMonitorConfig] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ):
        self.exchanges = exchanges
        self.config = config or UniverseMonitorConfig()
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._store = _SnapshotStore(Path(db_path))
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._store.path

    def snapshot_count(self) -> int:
        return self._store.count()

    def contract_count(self) -> int:
        return self._store.contract_count()

    def scan(self, *, now_ms: Optional[int] = None) -> UniverseScanResult:
        """Fetch each venue once, persist valid tickers, and build a shortlist."""

        observed_at_ms = int(self._clock_ms() if now_ms is None else now_ms)
        with self._lock:
            return self._scan_locked(observed_at_ms)

    def _scan_locked(self, observed_at_ms: int) -> UniverseScanResult:
        rejections: Counter[str] = Counter()
        venue_errors: Dict[str, str] = {}
        records: list[NormalizedTicker] = []
        contract_records: list[ContractMetadata] = []
        raw_count = 0
        fetch_count = 0
        metadata_entries = 0
        metadata_nonqualifying = 0
        metadata_duplicate_aliases = 0

        for raw_venue, exchange in sorted((self.exchanges or {}).items()):
            venue = str(raw_venue).strip().lower()
            (
                venue_contracts,
                venue_metadata_entries,
                venue_nonqualifying,
                venue_duplicate_aliases,
            ) = _contract_metadata_rows(venue, exchange)
            contract_records.extend(venue_contracts)
            metadata_entries += venue_metadata_entries
            metadata_nonqualifying += venue_nonqualifying
            metadata_duplicate_aliases += venue_duplicate_aliases
            fetch_count += 1
            try:
                tickers = exchange.fetch_tickers(market_type="futures")
            except Exception as exc:
                venue_errors[venue] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"[UniverseMonitor] {venue} ticker batch failed: {exc}")
                continue
            if not isinstance(tickers, Mapping):
                venue_errors[venue] = "invalid ticker batch"
                continue
            market_lookup = _market_lookup(exchange)
            items = sorted(tickers.items(), key=lambda item: str(item[0]))
            raw_count += len(items)
            if len(items) > self.config.max_contracts_per_venue:
                rejections["venue_contract_cap"] += (
                    len(items) - self.config.max_contracts_per_venue
                )
                items = items[: self.config.max_contracts_per_venue]
            for raw_symbol, ticker in items:
                market = market_lookup.get(str(raw_symbol))
                if market is None and isinstance(ticker, Mapping):
                    market = market_lookup.get(str(ticker.get("symbol") or ""))
                normalized, reason = normalize_ticker(
                    venue,
                    str(raw_symbol),
                    ticker,
                    now_ms=observed_at_ms,
                    config=self.config,
                    market=market,
                )
                if normalized is None:
                    rejections[str(reason or "invalid_ticker")] += 1
                else:
                    records.append(normalized)

        contract_counts, contract_coverage = self._store.persist_contract_master(
            contract_records,
            now_ms=observed_at_ms,
        )
        contract_counts.update(
            {
                "metadata_entries_seen": metadata_entries,
                "metadata_nonqualifying": metadata_nonqualifying,
                "metadata_duplicate_aliases": metadata_duplicate_aliases,
            }
        )
        tolerance_ms = int(self.config.reference_tolerance_s * 1000)
        retention_ms = int(self.config.retention_days * 86_400_000)
        references = self._store.persist_and_load_references(
            records,
            now_ms=observed_at_ms,
            horizons_ms=self.config.horizons_ms,
            tolerance_ms=tolerance_ms,
            retention_ms=retention_ms,
        )
        observations = self._returns(records, references)
        shortlist = self._rank(records, observations)
        return UniverseScanResult(
            observed_at_ms=observed_at_ms,
            venues_requested=len(self.exchanges or {}),
            venue_fetches=fetch_count,
            raw_tickers=raw_count,
            accepted_tickers=len(records),
            rejection_counts=dict(sorted(rejections.items())),
            venue_errors=dict(sorted(venue_errors.items())),
            shortlist=tuple(shortlist),
            contract_counts=dict(sorted(contract_counts.items())),
            contract_metadata_coverage=dict(sorted(contract_coverage.items())),
        )

    def _returns(
        self,
        records: Sequence[NormalizedTicker],
        references: Mapping[Tuple[str, str, str], Tuple[int, float]],
    ) -> Dict[Tuple[str, str], Dict[str, ReturnPoint]]:
        out: Dict[Tuple[str, str], Dict[str, ReturnPoint]] = {}
        for row in records:
            by_horizon: Dict[str, ReturnPoint] = {}
            for label, _duration_ms in self.config.horizons_ms:
                reference = references.get((row.venue, row.symbol, label))
                if reference is not None:
                    reference_at_ms, reference_price = reference
                    if reference_price > 0:
                        value = (row.price / reference_price - 1.0) * 100.0
                        if math.isfinite(value) and abs(value) <= self.config.max_abs_return_pct:
                            by_horizon[label] = ReturnPoint(
                                value_pct=value,
                                source="point_in_time_snapshot",
                                reference_at_ms=reference_at_ms,
                            )
                if (
                    label == "24h"
                    and label not in by_horizon
                    and row.ticker_return_24h_pct is not None
                ):
                    by_horizon[label] = ReturnPoint(
                        value_pct=row.ticker_return_24h_pct,
                        source="exchange_ticker_24h_seed",
                        reference_at_ms=None,
                    )
            out[(row.venue, row.symbol)] = by_horizon
        return out

    def _rank(
        self,
        records: Sequence[NormalizedTicker],
        observations: Mapping[Tuple[str, str], Mapping[str, ReturnPoint]],
    ) -> list[ShortlistEntry]:
        # Consolidate duplicate venue listings by base and horizon using the
        # most liquid valid contract.  This prevents one asset consuming the
        # entire cap merely because it trades on several exchanges.
        by_base_horizon: Dict[Tuple[str, str], Tuple[NormalizedTicker, ReturnPoint]] = {}
        for row in records:
            returns = observations.get((row.venue, row.symbol), {})
            for horizon, point in returns.items():
                if point.value_pct == 0:
                    continue
                key = (row.base, horizon)
                current = by_base_horizon.get(key)
                if current is None or (
                    row.quote_volume_usdt,
                    row.venue,
                    row.symbol,
                ) > (
                    current[0].quote_volume_usdt,
                    current[0].venue,
                    current[0].symbol,
                ):
                    by_base_horizon[key] = (row, point)

        groups: Dict[Tuple[str, str], list[Tuple[NormalizedTicker, ReturnPoint]]] = {}
        for (_base, horizon), candidate in by_base_horizon.items():
            direction = "gainer" if candidate[1].value_pct > 0 else "loser"
            groups.setdefault((horizon, direction), []).append(candidate)
        for (horizon, direction), rows in groups.items():
            if direction == "gainer":
                rows.sort(
                    key=lambda item: (
                        -item[1].value_pct,
                        -item[0].quote_volume_usdt,
                        item[0].base,
                    )
                )
            else:
                rows.sort(
                    key=lambda item: (
                        item[1].value_pct,
                        -item[0].quote_volume_usdt,
                        item[0].base,
                    )
                )

        group_order = [
            (label, direction)
            for label, _ in self.config.horizons_ms
            for direction in ("gainer", "loser")
        ]
        cursors = {key: 0 for key in group_order}
        selected_per_group = {key: 0 for key in group_order}
        selected_bases: set[str] = set()
        selected: list[ShortlistEntry] = []
        limit = self.config.per_direction_per_horizon
        cap = self.config.max_shortlist

        while len(selected) < cap:
            made_progress = False
            for key in group_order:
                if len(selected) >= cap:
                    break
                if selected_per_group[key] >= limit:
                    continue
                candidates = groups.get(key, [])
                chosen: Optional[Tuple[NormalizedTicker, ReturnPoint]] = None
                while cursors[key] < len(candidates):
                    candidate = candidates[cursors[key]]
                    cursors[key] += 1
                    if candidate[0].base not in selected_bases:
                        chosen = candidate
                        break
                if chosen is None:
                    continue
                row, selected_point = chosen
                returns = observations.get((row.venue, row.symbol), {})
                selected.append(
                    ShortlistEntry(
                        venue=row.venue,
                        symbol=row.symbol,
                        base=row.base,
                        price=row.price,
                        quote_volume_usdt=row.quote_volume_usdt,
                        observed_at_ms=row.observed_at_ms,
                        selected_horizon=key[0],
                        direction=key[1],
                        selected_return_pct=selected_point.value_pct,
                        selected_return_source=selected_point.source,
                        returns_pct={
                            label: point.value_pct for label, point in returns.items()
                        },
                        return_sources={
                            label: point.source for label, point in returns.items()
                        },
                    )
                )
                selected_bases.add(row.base)
                selected_per_group[key] += 1
                made_progress = True
            if not made_progress:
                break
        return selected


__all__ = [
    "ContractMetadata",
    "DEFAULT_DB_PATH",
    "DEFAULT_HORIZONS_MS",
    "NormalizedTicker",
    "ReturnPoint",
    "ShortlistEntry",
    "UniverseMonitor",
    "UniverseMonitorConfig",
    "UniverseScanResult",
    "normalize_contract_metadata",
    "normalize_ticker",
]
