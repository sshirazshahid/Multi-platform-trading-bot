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
    # May this feed's ill health BLOCK live entries? Defaults to True so a feed
    # added later gates unless someone opts it out deliberately (fail-closed).
    # Opting out is only defensible when nothing in the live decision path reads
    # the feed -- verify that, and record why, at the call site.
    gates_entries: bool = True


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
        # 2026-08-22: RESEARCH-ONLY -- monitored and alerted on, but it does not
        # gate entries.
        #
        # THE REASON, stated narrowly because a first pass at this comment got
        # three things wrong and they are corrected here:
        #   * `total_polls` is a PER-PROCESS counter (harvest_skew.py:205 sets
        #     `polls = 0` inside main()), so `total_polls: 0` means "this
        #     process has not polled yet", NOT "never worked". The feed has
        #     produced data for weeks -- data/skew_history.jsonl is ~341KB.
        #   * `open_hours` is `len(buckets)` (harvest_skew.py:185), a count of
        #     in-memory hourly buckets awaiting flush. It is NOT market hours,
        #     and Deribit crypto options trade 24/7.
        #   * the soft-stale share is 13.3% ACROSS 2026-08-15..22, not 42%.
        #     Skew-healthy days run 1.4-2.5%; 45-60% is the Aug 21-22 outage
        #     window alone.
        #
        # What actually justifies the opt-out is narrower and survives: NOTHING
        # IN THE LIVE DECISION PATH CONSUMES THIS FEED. Every `skew` reference
        # under core/ is CLOCK skew (max_future_skew_seconds, max_clock_skew_ms)
        # or statistical SKEWNESS (_skew feeding the deflated Sharpe). The
        # options-skew data exists for research/screen_skew_shock_drift.py.
        # A feed the live path never reads must not be able to refuse a trade --
        # during its Aug 21-22 outage it refused 45-60% of entry decisions.
        # It stays in FORWARD_FEEDS so monitoring, alerting and the self-healing
        # restart all keep working.
        gates_entries=False,
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


def entry_gating_feeds(records: list[dict[str, Any]]) -> list[str]:
    """Unhealthy feeds that are ALLOWED to block new entries.

    ``unhealthy_forward_feeds`` answers "is this feed healthy?" and drives
    monitoring. This answers the narrower question the soft-stale latch actually
    needs: "is an unhealthy feed that the live path DEPENDS ON?" Conflating the
    two is what let a research-only feed refuse 42% of a day's trades.

    Fail-closed twice over: a feed absent from ``FORWARD_FEEDS`` is treated as
    gating (a rename must not silently drop protection), and ``gates_entries``
    defaults to True so only a deliberate opt-out removes a block.
    """
    opted_out = {s.name for s in FORWARD_FEEDS if not s.gates_entries}
    return [n for n in unhealthy_forward_feeds(records) if n not in opted_out]


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
