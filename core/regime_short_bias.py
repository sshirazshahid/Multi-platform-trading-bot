"""F&G + 24h long-liquidation SHORT-bias — log-only measurement (prereg 61).

HONESTY: does NOT place orders, does NOT feed mcp_brain / entry_policy.
De-Emotion remains binding. See _workspace/strategy_pipeline/61_prereg_fng_liq_short_bias.md.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PREREG_ID = "61_fng_liq_short_bias"
PREREG_SHA256 = "6fe2cca96f791b21a6363c44891b47041ff23e62a094fa1f75a19fc125b4bdbc"
LIVE_SHORT_AUTHORIZED = False
LOG_ONLY = True
FNG_FIRE_MAX = 30
LIQ_THETAS_USD = (25_000_000.0, 50_000_000.0, 100_000_000.0, 200_000_000.0)
WINDOW_HOURS = 24


def completed_hour_start(now_ts: float | None = None) -> int:
    """Unix seconds at the top of the last fully completed UTC hour."""
    now = float(now_ts if now_ts is not None else time.time())
    this_hour = int(now // 3600 * 3600)
    return this_hour - 3600


def sum_long_liq_usd(
    history_path: Path,
    *,
    end_hour_inclusive: int,
    window_hours: int = WINDOW_HOURS,
) -> dict[str, Any]:
    """Sum symbol=ALL long_usd over [end_hour - (window-1)*3600, end_hour]."""
    if window_hours < 1:
        raise ValueError("window_hours must be >= 1")
    start = end_hour_inclusive - (window_hours - 1) * 3600
    total = 0.0
    short_total = 0.0
    count = 0
    hours_seen: set[int] = set()
    if not history_path.is_file():
        return {
            "long_usd_24h": 0.0,
            "short_usd_24h": 0.0,
            "event_count_24h": 0,
            "hours_present": 0,
            "start_hour": start,
            "end_hour": end_hour_inclusive,
            "missing_history": True,
        }
    with history_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("symbol") != "ALL":
                continue
            try:
                hour = int(row["hour"])
            except (KeyError, TypeError, ValueError):
                continue
            if hour < start or hour > end_hour_inclusive:
                continue
            hours_seen.add(hour)
            total += float(row.get("long_usd") or 0.0)
            short_total += float(row.get("short_usd") or 0.0)
            count += int(row.get("count") or 0)
    return {
        "long_usd_24h": round(total, 2),
        "short_usd_24h": round(short_total, 2),
        "event_count_24h": count,
        "hours_present": len(hours_seen),
        "start_hour": start,
        "end_hour": end_hour_inclusive,
        "missing_history": False,
    }


def evaluate_short_bias(
    *,
    fng_value: int | None,
    long_usd_24h: float,
    thetas: tuple[float, ...] = LIQ_THETAS_USD,
    fng_max: int = FNG_FIRE_MAX,
) -> dict[str, Any]:
    """Return measurement snapshot; never an order intent."""
    fng_ok = fng_value is not None and int(fng_value) <= int(fng_max)
    cells = []
    any_fire = False
    for theta in thetas:
        liq_ok = float(long_usd_24h) >= float(theta)
        fired = bool(fng_ok and liq_ok)
        any_fire = any_fire or fired
        cells.append(
            {
                "theta_usd": theta,
                "liq_ok": liq_ok,
                "fired": fired,
            }
        )
    return {
        "prereg_id": PREREG_ID,
        "prereg_sha256": PREREG_SHA256,
        "log_only": True,
        "live_short_authorized": False,
        "fng_value": fng_value,
        "fng_max": fng_max,
        "fng_ok": fng_ok,
        "long_usd_24h": float(long_usd_24h),
        "cells": cells,
        "any_cell_fired": any_fire,
        "narrative": (
            "SHORT_BIAS_ENV"
            if any_fire
            else ("FEAR_ONLY" if fng_ok else ("LIQ_ONLY" if any(c["liq_ok"] for c in cells) else "QUIET"))
        ),
    }


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, separators=(",", ":")) + "\n")
