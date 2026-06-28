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
