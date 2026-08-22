"""Fail-closed validator for perpetual-futures research evidence.

This utility is intentionally stdlib-only and read-only.  It never changes a
strategy registry, approval artifact, environment variable, or trading mode.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

REQUIRED_TRUE_PATHS = (
    "universe.point_in_time_master",
    "universe.includes_delisted",
    "data.event_time_observed",
    "data.received_time_recorded",
    "data.mark_index_funding",
    "data.side_specific_book",
    "data.closed_bars_only",
    "replay.causal",
    "replay.next_event_fills",
    "replay.chronological_portfolio",
    "replay.funding_basis_liquidation",
    "replay.partials_rejects_latency",
    "replay.purged_walk_forward",
    "replay.untouched_holdout",
    "replay.venue_holdout",
    "replay.parameter_plateau",
    "metrics.stressed_expectancy_positive",
)

REQUIRED_RANK_FEATURES = {
    "return_pct",
    "atr_z",
    "quote_volume_usd",
    "spread_bps",
    "depth_usd",
}


def _lookup(document: dict[str, Any], dotted_path: str) -> Any:
    value: Any = document
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _finite_number(value: Any) -> float | None:
    """Return a finite JSON number without coercing strings or booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _finite_integer(value: Any) -> float | None:
    """Return an integer-valued finite JSON number as a float."""

    number = _finite_number(value)
    if number is None or not number.is_integer():
        return None
    return number


def _input_failure(message: str) -> dict[str, Any]:
    return {
        "candidate_id": "",
        "decision": "RESEARCH_ONLY",
        "failures": [message],
        "warnings": [],
        "observed": {},
    }


def evaluate_evidence(document: Any) -> dict[str, Any]:
    """Return a deterministic promotion-review decision with exact failures."""

    if not isinstance(document, dict):
        return _input_failure("candidate evidence must be a JSON object")

    failures: list[str] = []
    warnings: list[str] = []

    market_type = document.get("market_type")
    if not isinstance(market_type, str) or market_type.lower() != "futures":
        failures.append("market_type must be genuine futures")
    candidate_id = document.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        failures.append("candidate_id is required")

    for path in REQUIRED_TRUE_PATHS:
        if _lookup(document, path) is not True:
            failures.append(f"{path} must be true")

    raw_features = _lookup(document, "universe.rank_features")
    if not isinstance(raw_features, list) or not all(
        isinstance(item, str) for item in raw_features
    ):
        failures.append("universe.rank_features must be an array of strings")
        features: set[str] = set()
    else:
        features = {item.strip().lower() for item in raw_features}
    missing_features = sorted(REQUIRED_RANK_FEATURES - features)
    if missing_features:
        failures.append("universe.rank_features missing: " + ", ".join(missing_features))

    raw_stress = _lookup(document, "replay.cost_stress_multipliers")
    if not isinstance(raw_stress, list) or any(
        _finite_number(item) is None for item in raw_stress
    ):
        failures.append("replay.cost_stress_multipliers must be an array of finite numbers")
        stress: set[float] = set()
    else:
        stress = {round(float(item), 3) for item in raw_stress}
    if not {1.0, 1.5, 2.0}.issubset(stress):
        failures.append("replay.cost_stress_multipliers must include 1.0, 1.5, and 2.0")

    numeric_rules = (
        ("metrics.independent_trades", 100.0, None, False, True),
        ("metrics.profit_factor", 1.20, None, False, False),
        ("metrics.bootstrap_expectancy_lower", 0.0, None, True, False),
        ("metrics.pbo", 0.0, 0.25, False, False),
        ("metrics.dsr", 0.95, 1.0, False, False),
        ("metrics.dominant_symbol_pnl_fraction", 0.0, 0.25, False, False),
    )
    observed: dict[str, float | None] = {}
    for path, lower, upper, strict_lower, integer_required in numeric_rules:
        parser = _finite_integer if integer_required else _finite_number
        value = parser(_lookup(document, path))
        observed[path] = value
        if value is None:
            kind = "finite integer" if integer_required else "finite number"
            failures.append(f"{path} must be a {kind}")
        elif strict_lower and value <= lower:
            failures.append(f"{path} must be > {lower:g}")
        elif value < lower:
            failures.append(f"{path} must be >= {lower:g}")
        elif upper is not None and value > upper:
            failures.append(f"{path} must be <= {upper:g}")

    positive_folds = _finite_integer(_lookup(document, "metrics.positive_folds"))
    total_folds = _finite_integer(_lookup(document, "metrics.total_folds"))
    observed["metrics.positive_folds"] = positive_folds
    observed["metrics.total_folds"] = total_folds
    if total_folds is None:
        failures.append("metrics.total_folds must be a finite integer")
    elif total_folds < 4:
        failures.append("metrics.total_folds must be >= 4")
    if positive_folds is None:
        failures.append("metrics.positive_folds must be a finite integer")
    elif positive_folds < 0:
        failures.append("metrics.positive_folds must be >= 0")
    elif total_folds is not None and positive_folds > total_folds:
        failures.append("metrics.positive_folds must be <= metrics.total_folds")
    elif total_folds is not None and total_folds >= 4 and positive_folds / total_folds < 0.75:
        failures.append("at least 75% of walk-forward folds must be positive")

    shadow_failures: list[str] = []
    if _lookup(document, "shadow.matured") is not True:
        shadow_failures.append("shadow.matured must be true")
    shadow_rules = (
        ("shadow.days", 30.0, None),
        ("shadow.independent_resolved", 100.0, None),
        ("shadow.mismatch_count", 0.0, 0.0),
    )
    for path, lower, upper in shadow_rules:
        value = _finite_integer(_lookup(document, path))
        observed[path] = value
        if value is None:
            shadow_failures.append(f"{path} must be a finite integer")
        elif value < lower:
            shadow_failures.append(f"{path} must be >= {lower:g}")
        elif upper is not None and value > upper:
            shadow_failures.append(f"{path} must be <= {upper:g}")

    if failures:
        decision = "RESEARCH_ONLY"
        failures.extend(shadow_failures)
    elif shadow_failures:
        decision = "ELIGIBLE_FOR_SHADOW"
        warnings.extend(shadow_failures)
    else:
        decision = "ELIGIBLE_FOR_MANUAL_CONTROLLED_LIVE_REVIEW"
        warnings.append("manual review is required; this result never authorizes live trading")

    return {
        "candidate_id": candidate_id if isinstance(candidate_id, str) else "",
        "decision": decision,
        "failures": failures,
        "warnings": warnings,
        "observed": observed,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="candidate evidence JSON")
    return parser


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def main() -> int:
    args = _build_parser().parse_args()
    try:
        raw_document = args.input.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        result = _input_failure("input JSON could not be read")
    else:
        try:
            document = json.loads(raw_document, parse_constant=_reject_nonstandard_constant)
        except (json.JSONDecodeError, ValueError):
            result = _input_failure("input must be valid standards-compliant JSON")
        else:
            result = evaluate_evidence(document)

    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    return 0 if result["decision"] != "RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
