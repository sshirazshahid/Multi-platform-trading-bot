"""Per-venue funding-rate history for the F1 carry gate (PAPER/research).

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
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

DEFAULT_DIR = Path("data/funding_carry")
HEADER = ("ts", "rate", "interval_hours", "next_funding_ts")
SEVEN_DAYS_SEC = 7 * 24 * 3600.0
# Below this many recorded periods a 7d average is noise, not a regime signal
# (8h intervals -> 21 periods/week; variable-interval venues may have more).
MIN_PERIODS_FOR_AVG = 6


def _path(venue: str, coin: str, base_dir: Path | str = DEFAULT_DIR) -> Path:
    return Path(base_dir) / f"{venue.lower()}_{coin.upper()}.csv"


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
            float(frame.get("interval_hours") or 8.0),
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
