"""Per-venue funding observations and settlements for F1 (PAPER/research).

Rolling per-venue funding series recorded by ``scripts/harvest_funding_carry.py``
(single-shot, scheduled hourly) and read back as the 7-day average that the F1
entry gate's ``avg_funding_7d`` regime check needs. Until this existed the gate
input was ``None`` (documented Rev-5 pass-through) — the check simply never ran.

Storage: ``data/funding_carry/<venue>_<COIN>.csv`` with header
``ts,rate,interval_hours,next_funding_ts`` (epoch-second floats; ``rate`` is the
per-interval funding fraction). One row per funding period: a row is appended
only when ``next_funding_ts`` differs from the last recorded one, so an hourly
harvest of an 8h-interval venue stays one-row-per-settlement. Fail-honest reads:
if history is missing/short, ``avg_7d`` returns None and the gate skips exactly
as it does today — never a fabricated average.

Realized venue settlements are a separate data source under
``data/funding_history/<venue>_<COIN>.csv`` with columns
``ts,funding_rate,venue,symbol``. ``load_realized_settlements`` deliberately
accepts only that realized schema. Forward observations must never be used to
reconstruct missed payments because their latest rate can differ from every
historical settlement.
"""
from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path("data/funding_carry")
DEFAULT_REALIZED_DIR = Path("data/funding_history")
HEADER = ("ts", "rate", "interval_hours", "next_funding_ts")
SEVEN_DAYS_SEC = 7 * 24 * 3600.0
# Below this many recorded periods a 7d average is noise, not a regime signal
# (8h intervals -> 21 periods/week; variable-interval venues may have more).
MIN_PERIODS_FOR_AVG = 6


@dataclass(frozen=True, slots=True)
class FundingSettlement:
    """One funding rate actually settled by a venue at ``settlement_ts``."""

    settlement_ts: float
    rate: float


def _path(venue: str, coin: str, base_dir: Path | str = DEFAULT_DIR) -> Path:
    return Path(base_dir) / f"{venue.lower()}_{coin.upper()}.csv"


def _canonical_settlement_ts(value) -> float:
    """Canonicalize venue millisecond timestamps represented as seconds."""
    ts = float(value)
    if not math.isfinite(ts) or ts < 0.0:
        raise ValueError("invalid settlement timestamp")
    return int(round(ts * 1000.0)) / 1000.0


def load_realized_settlements(
    venue: str,
    coin: str,
    *,
    start_ts: float,
    end_ts: float,
    base_dir: Path | str = DEFAULT_REALIZED_DIR,
) -> tuple[FundingSettlement, ...] | None:
    """Load authoritative realized settlements in the inclusive time range.

    ``None`` means the source cannot establish a trustworthy history: the file
    is absent/unreadable, has the forward-observation schema, contains invalid
    rows, identifies another venue/symbol, or has conflicting duplicate
    timestamps. An empty tuple means a valid realized-history file contains no
    rows in the requested range. Callers that expect an overdue settlement must
    treat either result as unavailable and defer without partial accrual.
    """
    try:
        start = _canonical_settlement_ts(start_ts)
        end = _canonical_settlement_ts(end_ts)
    except (TypeError, ValueError, OverflowError):
        return None
    if end < start:
        return ()

    path = _path(venue, coin, base_dir)
    if not path.is_file():
        return None

    by_ts: dict[float, float] = {}
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = set(reader.fieldnames or ())
            if not {"ts", "funding_rate"}.issubset(fields):
                return None
            for row in reader:
                settlement_ts = _canonical_settlement_ts(row["ts"])
                rate = float(row["funding_rate"])
                if not math.isfinite(rate):
                    return None

                row_venue = (row.get("venue") or "").strip().lower()
                if row_venue and row_venue != venue.lower():
                    return None
                row_symbol = (row.get("symbol") or "").strip()
                if row_symbol and row_symbol.split("/", 1)[0].upper() != coin.upper():
                    return None

                prior = by_ts.get(settlement_ts)
                if prior is not None and prior != rate:
                    return None
                by_ts[settlement_ts] = rate
    except (OSError, KeyError, TypeError, ValueError, OverflowError, csv.Error):
        return None

    return tuple(
        FundingSettlement(settlement_ts=ts, rate=by_ts[ts])
        for ts in sorted(by_ts)
        if start <= ts <= end
    )


def load_recent_realized_settlements(
    venue: str,
    coin: str,
    *,
    limit: int = 21,
    before_ts: float | None = None,
    base_dir: Path | str = DEFAULT_REALIZED_DIR,
) -> tuple[FundingSettlement, ...] | None:
    """Return the latest authoritative settlements at or before ``before_ts``.

    This deliberately reads the realized-history schema, never the forward
    observation file used by the hourly funding harvester.  ``None`` means the
    source is invalid/unavailable or cannot supply the complete requested
    window; callers must defer rather than infer missing rates.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    end = time.time() if before_ts is None else before_ts
    rows = load_realized_settlements(
        venue,
        coin,
        start_ts=0.0,
        end_ts=end,
        base_dir=base_dir,
    )
    if rows is None or len(rows) < limit:
        return None
    return rows[-limit:]


def _observed_interval_hours(value) -> float | str:
    """The venue-reported funding interval, or "" when it did not report one.

    Never substitutes a default: the interval is time-varying per symbol (bases
    have switched 8h -> 4h mid-history), so a fabricated literal would enter the
    permanent record indistinguishable from an observation.
    """
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(hours) or hours <= 0.0:
        return ""
    return hours


def record(venue: str, coin: str, frame: dict, *,
           base_dir: Path | str = DEFAULT_DIR,
           now_fn=time.time) -> bool:
    """Append one funding observation; dedupe by funding period.

    ``frame`` is a MarketDataLedger funding dict: needs ``rate``; uses
    ``interval_hours`` and ``next_funding_ts`` when present. Returns True iff a
    row was written (False = duplicate period, stale frame, or unusable data).
    """
    rate = frame.get("rate")
    if rate is None or frame.get("stale", False):
        return False
    next_ts = frame.get("next_funding_ts")
    p = _path(venue, coin, base_dir)
    if p.exists() and next_ts is not None:
        try:
            with open(p, newline="", encoding="utf-8") as f:
                last = None
                for last in csv.DictReader(f):
                    pass
            if last is not None and last.get("next_funding_ts"):
                if float(last["next_funding_ts"]) == float(next_ts):
                    return False  # same funding period already recorded
        except (OSError, ValueError):
            pass  # unreadable tail -> fall through and append
    p.parent.mkdir(parents=True, exist_ok=True)
    new_file = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerow([
            float(now_fn()),
            float(rate),
            _observed_interval_hours(frame.get("interval_hours")),
            float(next_ts) if next_ts is not None else "",
        ])
    return True


def avg_7d(venue: str, coin: str, *,
           base_dir: Path | str = DEFAULT_DIR,
           now: float | None = None) -> float | None:
    """Mean per-interval funding over the trailing 7 days, or None.

    None (gate pass-through, same as before this module existed) when the file
    is missing, unreadable, or holds fewer than MIN_PERIODS_FOR_AVG rows in the
    window — an honest "not enough history", never a guess.
    """
    p = _path(venue, coin, base_dir)
    if not p.exists():
        return None
    cutoff = (time.time() if now is None else float(now)) - SEVEN_DAYS_SEC
    rates: list[float] = []
    try:
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    if float(row["ts"]) >= cutoff:
                        rates.append(float(row["rate"]))
                except (KeyError, ValueError):
                    continue  # malformed row: skip, don't poison the average
    except OSError:
        return None
    if len(rates) < MIN_PERIODS_FOR_AVG:
        return None
    return sum(rates) / len(rates)
