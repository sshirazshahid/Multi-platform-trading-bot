#!/usr/bin/env python3
"""Validate deterministic invariants in a serialized futures protection plan.

This tool does not contact an exchange and cannot prove live order coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

INTENT_STATES = {"pending", "acknowledged", "verified", "repair_required"}
TRIGGER_SOURCES = {"mark", "last", "index"}
STOP_TYPES = {"market", "stop_market", "conditional_market"}
TARGET_TYPES = {"limit", "market", "take_profit_limit", "take_profit_market"}


def _decimal(value: Any, path: str, errors: List[str]) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        errors.append("%s must be a positive decimal" % path)
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors.append("%s must be a decimal" % path)
        return None
    if not result.is_finite() or result <= 0:
        errors.append("%s must be a positive finite decimal" % path)
        return None
    return result


def _mapping(value: Any, path: str, errors: List[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append("%s must be an object" % path)
        return {}
    return value


def _quantized(value: Optional[Decimal], step: Optional[Decimal]) -> bool:
    if value is None or step is None:
        return False
    return value % step == 0


def _timestamp(value: Any, path: str, errors: List[str]) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        errors.append("%s must be an ISO-8601 timestamp" % path)
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        errors.append("%s must be an ISO-8601 timestamp" % path)
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append("%s must include a UTC offset" % path)
        return None
    return parsed


def _normalized_type(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def validate_plan(plan: Any) -> Dict[str, Any]:
    errors: List[str] = []
    root = _mapping(plan, "plan", errors)
    instrument = _mapping(root.get("instrument"), "instrument", errors)
    position = _mapping(root.get("position"), "position", errors)
    stop = _mapping(root.get("stop"), "stop", errors)
    intent = _mapping(root.get("intent"), "intent", errors)

    tick = _decimal(instrument.get("price_tick"), "instrument.price_tick", errors)
    qty_step = _decimal(instrument.get("qty_step"), "instrument.qty_step", errors)
    min_qty = None
    if instrument.get("min_qty") is not None:
        min_qty = _decimal(instrument.get("min_qty"), "instrument.min_qty", errors)
    min_notional = None
    if instrument.get("min_notional") is not None:
        min_notional = _decimal(
            instrument.get("min_notional"), "instrument.min_notional", errors
        )
    enforce_exit_min_notional = instrument.get("enforce_exit_min_notional") is True
    contract_size = Decimal("1")
    if instrument.get("contract_size") is not None:
        contract_size = _decimal(
            instrument.get("contract_size"), "instrument.contract_size", errors
        ) or Decimal("1")

    side = str(position.get("side") or "").strip().lower()
    if side not in {"long", "short"}:
        errors.append("position.side must be long or short")
    entry = _decimal(position.get("entry_price"), "position.entry_price", errors)
    qty_field = "remaining_qty" if position.get("remaining_qty") is not None else "filled_qty"
    remaining_qty = _decimal(position.get(qty_field), "position.%s" % qty_field, errors)
    # An average fill price can legitimately fall between ticks after multiple
    # partial fills. Only order prices, not the computed average, must align.
    if remaining_qty is not None and not _quantized(remaining_qty, qty_step):
        errors.append("position.%s is not quantized to instrument.qty_step" % qty_field)

    order_ids: List[str] = []

    def validate_order(
        order: Mapping[str, Any], path: str, allowed_types: set, require_qty: bool
    ) -> Dict[str, Optional[Decimal]]:
        order_type = _normalized_type(order.get("order_type"))
        if order_type not in allowed_types:
            errors.append("%s.order_type is unsupported" % path)
        if order.get("reduce_only") is not True:
            errors.append("%s.reduce_only must be true" % path)
        trigger_source = str(order.get("trigger_source") or "").strip().lower()
        if trigger_source not in TRIGGER_SOURCES:
            errors.append("%s.trigger_source must be mark, last, or index" % path)
        client_id = str(order.get("client_order_id") or "").strip()
        if not client_id:
            errors.append("%s.client_order_id is required" % path)
        else:
            order_ids.append(client_id)

        trigger = _decimal(order.get("trigger_price"), "%s.trigger_price" % path, errors)
        if trigger is not None and not _quantized(trigger, tick):
            errors.append("%s.trigger_price is not quantized to instrument.price_tick" % path)

        qty = None
        if require_qty:
            qty = _decimal(order.get("qty"), "%s.qty" % path, errors)
            if qty is not None and not _quantized(qty, qty_step):
                errors.append("%s.qty is not quantized to instrument.qty_step" % path)
            if qty is not None and min_qty is not None and qty < min_qty:
                errors.append("%s.qty is below instrument.min_qty" % path)

        limit_price = None
        if order_type in {"limit", "take_profit_limit"}:
            limit_price = _decimal(order.get("limit_price"), "%s.limit_price" % path, errors)
            if limit_price is not None and not _quantized(limit_price, tick):
                errors.append("%s.limit_price is not quantized to instrument.price_tick" % path)

        effective_price = limit_price or trigger
        if (
            qty is not None
            and effective_price is not None
            and min_notional is not None
            and enforce_exit_min_notional
            and qty * effective_price * contract_size < min_notional
        ):
            errors.append("%s notional is below instrument.min_notional" % path)
        return {"trigger": trigger, "qty": qty}

    stop_closes_position = stop.get("close_position") is True
    if stop_closes_position and stop.get("qty") is not None:
        errors.append("stop must use either close_position or qty, not both")
    stop_values = validate_order(
        stop, "stop", STOP_TYPES, require_qty=not stop_closes_position
    )
    stop_price = stop_values["trigger"]
    stop_qty = stop_values["qty"]
    if stop_qty is not None and remaining_qty is not None and stop_qty != remaining_qty:
        errors.append("stop.qty must equal the remaining position quantity")

    targets_raw = root.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        errors.append("targets must be a non-empty list")
        targets: List[Mapping[str, Any]] = []
    else:
        targets = []
        for index, value in enumerate(targets_raw):
            targets.append(_mapping(value, "targets[%d]" % index, errors))

    target_values = [
        validate_order(target, "targets[%d]" % index, TARGET_TYPES, require_qty=True)
        for index, target in enumerate(targets)
    ]

    if entry is not None and stop_price is not None:
        if side == "long" and stop_price >= entry:
            errors.append("long stop.trigger_price must be below position.entry_price")
        if side == "short" and stop_price <= entry:
            errors.append("short stop.trigger_price must be above position.entry_price")
    for index, values in enumerate(target_values):
        trigger = values["trigger"]
        if entry is None or trigger is None:
            continue
        if side == "long" and trigger <= entry:
            errors.append("targets[%d].trigger_price must be above long entry" % index)
        if side == "short" and trigger >= entry:
            errors.append("targets[%d].trigger_price must be below short entry" % index)

    quantities = [values["qty"] for values in target_values if values["qty"] is not None]
    if remaining_qty is not None and len(quantities) == len(target_values):
        if sum(quantities, Decimal("0")) != remaining_qty:
            errors.append("target quantities must sum exactly to the remaining position quantity")

    if len(order_ids) != len(set(order_ids)):
        errors.append("client_order_id values must be unique")

    if intent.get("persisted") is not True:
        errors.append("intent.persisted must be true")
    state = str(intent.get("state") or "").strip().lower()
    if state not in INTENT_STATES:
        errors.append("intent.state is unsupported")
    observed_at = _timestamp(intent.get("observed_at"), "intent.observed_at", errors)
    received_at = _timestamp(intent.get("received_at"), "intent.received_at", errors)
    if observed_at is not None and received_at is not None and received_at < observed_at:
        errors.append("intent.received_at must not precede intent.observed_at")
    if state == "verified":
        evidence = _mapping(intent.get("venue_evidence"), "intent.venue_evidence", errors)
        evidence_source = str(evidence.get("source") or "").strip().lower()
        if evidence_source not in {"private_stream", "rest_query"}:
            errors.append(
                "intent.venue_evidence.source must be private_stream or rest_query when verified"
            )
        venue_order_ids = evidence.get("venue_order_ids")
        if (
            not isinstance(venue_order_ids, list)
            or not venue_order_ids
            or any(not str(value).strip() for value in venue_order_ids)
        ):
            errors.append(
                "intent.venue_evidence.venue_order_ids must be a non-empty list when verified"
            )
        _timestamp(
            evidence.get("verified_at"), "intent.venue_evidence.verified_at", errors
        )

    return {
        "valid": not errors,
        "errors": errors,
        "target_count": len(targets),
        "intent_state": state or None,
        "scope": "offline plan invariants only; venue coverage not verified",
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="Path to protection-plan JSON")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    path = Path(args.plan)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("could not read protection plan: %s" % exc, file=sys.stderr)
        return 2
    result = validate_plan(plan)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("protection plan: %s" % ("PASS" if result["valid"] else "FAIL"))
        for error in result["errors"]:
            print("- %s" % error)
        print("scope: %s" % result["scope"])
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
