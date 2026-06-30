"""Operational health helpers for forward market-data harvesters."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ForwardFeedSpec:
    name: str
    script: str
    status_path: str
    max_age_sec: int


FORWARD_FEEDS: tuple[ForwardFeedSpec, ...] = (
    ForwardFeedSpec(
        name="liquidations",
        script="scripts\\harvest_liquidations.py",
        status_path="data\\liquidations_status.json",
        max_age_sec=20 * 60,
    ),
    ForwardFeedSpec(
        name="skew",
        script="scripts\\harvest_skew.py",
        status_path="data\\skew_status.json",
        max_age_sec=20 * 60,
    ),
    ForwardFeedSpec(
        name="l2",
        script="scripts\\harvest_l2.py",
        status_path="data\\l2_status.json",
        max_age_sec=20 * 60,
    ),
    ForwardFeedSpec(
        name="tv",
        script="scripts\\harvest_tv.py",
        status_path="data\\tv_status.json",
        max_age_sec=20 * 60,
    ),
)


def _rooted(root: Path, relative: str) -> Path:
    parts = str(relative).replace("/", "\\").split("\\")
    return root.joinpath(*[p for p in parts if p])


def read_forward_feed_status(
    root: Path | str = Path("."),
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return one normalized health record per forward-feed status file.

    The harvester status payloads share `updated` and `connected` fields, but
    counters differ (`total_events` for liquidations, `total_polls` for REST
    feeds). This helper keeps audit/watchdog semantics consistent.
    """
    base = Path(root)
    current = time.time() if now is None else float(now)
    out: list[dict[str, Any]] = []
    for spec in FORWARD_FEEDS:
        path = _rooted(base, spec.status_path)
        rec: dict[str, Any] = {
            "name": spec.name,
            "script": spec.script,
            "status_path": str(path),
            "exists": path.exists(),
            "connected": False,
            "updated": None,
            "age_sec": None,
            "fresh": False,
            "max_age_sec": spec.max_age_sec,
            "total_events": None,
            "total_polls": None,
            "error": None,
        }
        if not path.exists():
            rec["error"] = "missing_status"
            out.append(rec)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated = float(payload.get("updated") or path.stat().st_mtime)
            age_sec = max(0.0, current - updated)
            rec.update(
                {
                    "connected": bool(payload.get("connected")),
                    "updated": updated,
                    "age_sec": round(age_sec, 2),
                    "fresh": age_sec <= spec.max_age_sec,
                    "total_events": payload.get("total_events"),
                    "total_polls": payload.get("total_polls"),
                }
            )
        except Exception as exc:
            rec["error"] = f"parse_error:{type(exc).__name__}"
        out.append(rec)
    return out


@dataclass(frozen=True)
class FeedFreshness:
    """Per-venue freshness frame for a fill-time staleness gate.

    Each timestamp is the epoch-seconds of the last observed update for that
    feed (None = never observed). New sims build one of these immediately
    before simulating a fill and call `stale_fill_reason` to reject a fill
    whose required feeds are older than `max_age_sec`.
    """

    venue: str
    mark_ts: float | None = None
    index_ts: float | None = None
    funding_ts: float | None = None
    book_ts: float | None = None


def stale_fill_reason(
    snap: FeedFreshness,
    now: float,
    *,
    max_age_sec: float = 10.0,
    require: tuple[str, ...] = ("mark",),
) -> str | None:
    """Return a reason string if any required feed is stale/missing, else None.

    A fill must be rejected when the venue's mark/index/funding/book feed it
    relies on is older than `max_age_sec` (or never observed). `require` lists
    which feeds gate the fill; `mark` alone by default.
    """
    field_map = {
        "mark": snap.mark_ts,
        "index": snap.index_ts,
        "funding": snap.funding_ts,
        "book": snap.book_ts,
    }
    for feed in require:
        ts = field_map.get(feed)
        if ts is None:
            return f"{feed}_missing"
        try:
            age = float(now) - float(ts)
        except (TypeError, ValueError):
            return f"{feed}_bad_ts"
        if age > float(max_age_sec):
            return f"{feed}_stale:{age:.1f}s>{max_age_sec:.1f}s"
    return None


def is_source_available(
    source_ts: float, now: float, publish_lag_sec: float
) -> bool:
    """Causality guard for lagged feeds (OI / taker-flow / etc.).

    A sample stamped at `source_ts` only becomes observable after its
    publish lag elapses: available iff `source_ts + publish_lag_sec <= now`.
    A causal feature builder must not consume a sample before this is True.
    """
    try:
        return float(source_ts) + float(publish_lag_sec) <= float(now)
    except (TypeError, ValueError):
        return False


def unhealthy_forward_feeds(records: list[dict[str, Any]]) -> list[str]:
    bad: list[str] = []
    for rec in records:
        if (
            not rec.get("exists")
            or not rec.get("connected")
            or not rec.get("fresh")
            or rec.get("error")
        ):
            bad.append(str(rec.get("name") or "?"))
    return bad
