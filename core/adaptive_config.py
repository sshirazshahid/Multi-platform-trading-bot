"""Validated adaptive machine-strategy config overrides.

The self-healing supervisor may write ``data/adaptive_machine_config.json``.
This module is the only path that runtime code uses to consume that file. It
intentionally accepts a small allowlist of PAPER-safe machine strategy knobs and
rejects anything that could alter exchange routing, leverage, sizing, or live
promotion gates.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ADAPTIVE_MACHINE_CONFIG_PATH = Path("data/adaptive_machine_config.json")

DETECTOR_NAMES = {
    "valuation",
    "harmonic",
    "stochastic",
    "ict",
    "asian_range",
    "dow_swing",
    "bb_squeeze",
    "ema_rsi",
}

TOP_LEVEL_RANGES = {
    "min_score": (0.20, 0.80),
    "unconfirmed_min_score": (0.20, 0.90),
    "rr": (1.0, 5.0),
    "atr_stop_mult": (0.5, 4.0),
    "min_stop_pct": (0.001, 0.05),
    "max_stop_pct": (0.002, 0.08),
    "scalp_time_stop_bars": (2, 96),
}

EMA_RSI_RANGES = {
    "fast_ema": (3, 50),
    "slow_ema": (5, 100),
    "trend_ema": (10, 240),
    "rsi_period": (3, 50),
    "rsi_signal_ema": (2, 30),
    "trend_slope_bars": (1, 24),
    "min_trend_slope": (0.0, 0.01),
    "min_ema_spread_pct": (0.0, 0.02),
    "max_ema_spread_pct": (0.002, 0.20),
    "long_rsi_floor": (1.0, 70.0),
    "long_rsi_ceiling": (40.0, 99.0),
    "short_rsi_floor": (1.0, 60.0),
    "short_rsi_ceiling": (30.0, 99.0),
    "hold_bars": (1, 24),
}


@dataclass(frozen=True)
class AdaptiveConfigResult:
    applied: bool
    reason: str
    overrides: dict[str, Any]
    source: str | None = None
    candidate_id: str | None = None


def _float_in_range(name: str, value: Any, lo: float, hi: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not lo <= out <= hi:
        raise ValueError(f"{name}={out} outside [{lo}, {hi}]")
    return out


def _int_in_range(name: str, value: Any, lo: float, hi: float) -> int:
    out = _float_in_range(name, value, lo, hi)
    if int(out) != out:
        raise ValueError(f"{name} must be an integer")
    return int(out)


def _parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _validate_weights(raw: Any) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("weights must be an object")
    out: dict[str, float] = {}
    for name, value in raw.items():
        key = str(name)
        if key not in DETECTOR_NAMES:
            raise ValueError(f"unknown detector weight {key!r}")
        out[key] = _float_in_range(f"weights.{key}", value, 0.0, 1.0)
    if out and sum(out.values()) <= 0.0:
        raise ValueError("weights sum must be positive")
    return out


def _validate_confirmation_detectors(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("confirmation_detectors must be a list")
    out: list[str] = []
    for value in raw:
        key = str(value)
        if key not in DETECTOR_NAMES:
            raise ValueError(f"unknown confirmation detector {key!r}")
        if key not in out:
            out.append(key)
    return tuple(out)


def _validate_detector_params(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("detector_params must be an object")
    out: dict[str, dict[str, Any]] = {}
    for detector, params in raw.items():
        name = str(detector)
        if name != "ema_rsi":
            raise ValueError(f"adaptive params for {name!r} are not allowed")
        if not isinstance(params, dict):
            raise ValueError(f"detector_params.{name} must be an object")
        clean: dict[str, Any] = {}
        for key, value in params.items():
            k = str(key)
            if k not in EMA_RSI_RANGES:
                raise ValueError(f"unknown ema_rsi param {k!r}")
            lo, hi = EMA_RSI_RANGES[k]
            if k in {"min_trend_slope", "min_ema_spread_pct", "max_ema_spread_pct"}:
                clean[k] = _float_in_range(f"ema_rsi.{k}", value, lo, hi)
            elif "rsi" in k and k not in {"rsi_period", "rsi_signal_ema"}:
                clean[k] = _float_in_range(f"ema_rsi.{k}", value, lo, hi)
            else:
                clean[k] = _int_in_range(f"ema_rsi.{k}", value, lo, hi)
        out[name] = clean
    return out


def sanitize_adaptive_machine_payload(
    payload: dict[str, Any],
    *,
    now: float | None = None,
) -> AdaptiveConfigResult:
    if not isinstance(payload, dict):
        return AdaptiveConfigResult(False, "payload_not_object", {})
    if payload.get("enabled") is False:
        return AdaptiveConfigResult(False, "disabled", {})
    if str(payload.get("apply_scope", "paper")).lower() not in {"paper", "paper_shadow"}:
        return AdaptiveConfigResult(False, "non_paper_scope_rejected", {})

    current = time.time() if now is None else float(now)
    expires_at = _parse_ts(payload.get("expires_at"))
    if expires_at is not None and expires_at <= current:
        return AdaptiveConfigResult(False, "expired", {})

    raw_cfg = payload.get("config") or {}
    if not isinstance(raw_cfg, dict):
        return AdaptiveConfigResult(False, "config_not_object", {})

    try:
        clean: dict[str, Any] = {}
        for key, value in raw_cfg.items():
            if key in TOP_LEVEL_RANGES:
                lo, hi = TOP_LEVEL_RANGES[key]
                if key == "scalp_time_stop_bars":
                    clean[key] = _int_in_range(key, value, lo, hi)
                else:
                    clean[key] = _float_in_range(key, value, lo, hi)
            elif key == "weights":
                weights = _validate_weights(value)
                if weights:
                    clean[key] = weights
            elif key == "confirmation_detectors":
                clean[key] = _validate_confirmation_detectors(value)
            elif key == "detector_params":
                params = _validate_detector_params(value)
                if params:
                    clean[key] = params
            else:
                raise ValueError(f"{key!r} is not adaptive-override eligible")
        if "min_stop_pct" in clean and "max_stop_pct" in clean:
            if clean["min_stop_pct"] > clean["max_stop_pct"]:
                raise ValueError("min_stop_pct cannot exceed max_stop_pct")
        if "min_score" in clean and "unconfirmed_min_score" in clean:
            if clean["unconfirmed_min_score"] < clean["min_score"]:
                raise ValueError("unconfirmed_min_score cannot be below min_score")
    except ValueError as exc:
        return AdaptiveConfigResult(False, f"invalid:{exc}", {})

    if not clean:
        return AdaptiveConfigResult(False, "empty_config", {})
    return AdaptiveConfigResult(
        True,
        "applied",
        clean,
        source=str(payload.get("source") or ""),
        candidate_id=str(payload.get("candidate_id") or ""),
    )


def load_adaptive_machine_config(
    path: Path | str = ADAPTIVE_MACHINE_CONFIG_PATH,
    *,
    now: float | None = None,
) -> AdaptiveConfigResult:
    p = Path(path)
    if not p.exists():
        return AdaptiveConfigResult(False, "missing", {})
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return AdaptiveConfigResult(False, f"read_error:{type(exc).__name__}", {})
    return sanitize_adaptive_machine_payload(payload, now=now)


def merge_machine_config(
    base: dict[str, Any],
    result: AdaptiveConfigResult,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(base or {}))
    if not result.applied:
        return out
    overrides = result.overrides
    for key, value in overrides.items():
        if key == "weights":
            merged = dict(out.get("weights") or {})
            merged.update(value)
            out["weights"] = merged
        elif key == "detector_params":
            merged = copy.deepcopy(dict(out.get("detector_params") or {}))
            for detector, params in value.items():
                current = dict(merged.get(detector) or {})
                current.update(params)
                merged[detector] = current
            out["detector_params"] = merged
        else:
            out[key] = value
    out["_adaptive"] = {
        "applied": True,
        "source": result.source,
        "candidate_id": result.candidate_id,
        "reason": result.reason,
    }
    return out
