"""Append-only terminal provenance for entry candidates."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.contracts import (
    DecisionAction,
    DecisionOutcome,
    DecisionRecord,
    InstrumentId,
    MarketDataKind,
    MarketDatum,
    MarketSnapshot,
    MarketType,
    OrderSide,
)
from core.entry_policy import current_git_sha


def _venue(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    for venue in ("binance", "bybit", "bitget"):
        if venue in normalized:
            return venue
    raise ValueError(f"unsupported venue for provenance: {value!r}")


def _market(value: Any) -> MarketType:
    normalized = str(value or "").strip().lower()
    if normalized in {"futures", "future", "perp", "perpetual", "swap"}:
        return MarketType.PERPETUAL
    if normalized == "spot":
        return MarketType.SPOT
    raise ValueError(f"unsupported market type for provenance: {value!r}")


def _instrument(action: Mapping[str, Any]) -> InstrumentId:
    market = _market(action.get("market_type", "futures"))
    symbol = str(action.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("candidate symbol is missing")
    if market is MarketType.PERPETUAL and ":" not in symbol:
        symbol = f"{symbol}:USDT"
    exchange_symbol = str(action.get("exchange_symbol") or symbol).strip()
    return InstrumentId(
        venue=_venue(action.get("exchange")),
        market=market,
        canonical_symbol=symbol,
        exchange_symbol=exchange_symbol,
    )


def runtime_config_hash() -> str:
    """Hash execution-sensitive, non-secret runtime configuration."""
    import config

    names = (
        "OPERATING_MODE",
        "ENTRY_POLICY",
        "APPROVED_PAPER_STRATEGIES",
        "APPROVED_LIVE_STRATEGIES",
        "MAX_PORTFOLIO_EXPOSURE_PCT",
        "MAX_AGGREGATE_OPEN_RISK_PCT",
        "STRESSED_EXIT_COST_FRAC",
        "MAX_ENTRY_SLIPPAGE_BPS",
        "EXECUTION_BOOK_MAX_AGE_SEC",
        "EXECUTION_BOOK_DEPTH_LEVELS",
        "SIGNAL_SOURCE",
    )
    values = {name: getattr(config, name, None) for name in names}
    config_path = Path(config.__file__).resolve()
    file_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    encoded = json.dumps(
        {"config_file_sha256": file_hash, "runtime": values},
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: sorted(value) if isinstance(value, (set, frozenset)) else str(value),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def outcome_for_rejection(reason: str, stage: str) -> DecisionOutcome:
    normalized = str(reason or "").lower()
    if stage == "cycle_cap":
        return DecisionOutcome.EXPIRED
    if "pending" in normalized or "cooldown" in normalized:
        return DecisionOutcome.DEFERRED
    if "uncertain" in normalized or normalized.endswith("_error"):
        return DecisionOutcome.ERROR
    return DecisionOutcome.REJECTED


def _confidence(value: Any):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 1 else None


def _action_type(action: Mapping[str, Any]) -> tuple[DecisionAction, OrderSide | None]:
    if str(action.get("type") or "").upper() == "OPEN":
        side = str(action.get("side") or "").lower()
        if side == "buy":
            return DecisionAction.ENTER_LONG, OrderSide.BUY
        if side == "sell":
            return DecisionAction.ENTER_SHORT, OrderSide.SELL
    if str(action.get("type") or "").upper() == "CLOSE":
        return DecisionAction.EXIT, None
    return DecisionAction.SKIP, None


def _snapshot_from_action(
    warehouse: Any,
    action: Mapping[str, Any],
    instrument: InstrumentId,
    decision_id: str,
    now: datetime,
) -> MarketSnapshot:
    payload = action.get("execution_snapshot")
    if isinstance(payload, Mapping) and isinstance(payload.get("snapshot"), Mapping):
        snapshot = MarketSnapshot.from_dict(payload["snapshot"])
        if snapshot.instrument != instrument:
            raise ValueError("execution snapshot instrument does not match candidate")
        return snapshot

    snapshot_id = f"unavailable-{decision_id}"
    existing = warehouse.query_market_snapshots(snapshot_id=snapshot_id)
    if existing:
        return existing[0]
    return MarketSnapshot(
        snapshot_id=snapshot_id,
        instrument=instrument,
        observed_at=now,
        received_at=now,
        data=(MarketDatum(
            instrument=instrument,
            kind=MarketDataKind.AVAILABILITY,
            value=0,
            observed_at=now,
            received_at=now,
            source="candidate.snapshot_unavailable",
            context={"is_fresh": False},
        ),),
        source="candidate.snapshot_unavailable",
        context={
            "freshness_status": "unavailable",
            "is_fresh": False,
            "reason": "candidate terminated before venue execution snapshot",
        },
    )


def record_terminal_decision(
    warehouse: Any,
    action: dict[str, Any],
    *,
    outcome: DecisionOutcome | str,
    reason: str,
    stage: str,
    repo_root: str | Path = ".",
) -> str:
    """Persist one terminal decision and its explicit venue snapshot state.

    Repeated calls for the same decision are idempotent.  A prior terminal row
    wins; terminal decisions are immutable and cannot be rewritten after the
    observed outcome is known.
    """
    import uuid

    decision_id = str(action.get("decision_id") or "").strip()
    if not decision_id:
        decision_id = str(uuid.uuid4())
        action["decision_id"] = decision_id
    existing = warehouse.query_decision_events(decision_id=decision_id)
    if existing:
        return existing[0].event_id

    terminal_outcome = (
        outcome if isinstance(outcome, DecisionOutcome) else DecisionOutcome(outcome)
    )
    now = datetime.now(timezone.utc)
    instrument = _instrument(action)
    snapshot = _snapshot_from_action(warehouse, action, instrument, decision_id, now)
    warehouse.append_market_snapshot(snapshot)

    decision_action, order_side = _action_type(action)
    reason_code = str(reason or "unspecified")[:255]
    stage_code = str(stage or "unknown")[:255]
    context = {
        "terminal_outcome": terminal_outcome.value,
        "terminal_stage": stage_code,
        "reason": reason_code,
        # Deferred execution mechanisms (for example PAPER maker-first) must
        # resolve through a *new* immutable decision instead of trying to
        # rewrite the original terminal DEFERRED candidate.  Persist the
        # append-only lineage on the child so a trade can point at the FILLED
        # resolution while the original candidate remains historically true.
        "parent_decision_id": str(action.get("parent_decision_id") or ""),
        "resolution_kind": str(action.get("resolution_kind") or ""),
        "entry_policy": str(action.get("entry_policy") or ""),
        "entry_policy_reason": str(action.get("entry_policy_reason") or ""),
        "strategy_version": str(
            action.get("strategy_version") or action.get("model_version") or ""
        ),
        "expected_entry_fee_usdt": action.get("expected_entry_fee_usdt"),
        "expected_entry_slippage_usdt": action.get("expected_entry_slippage_usdt"),
        "expected_funding_usdt": action.get("expected_funding_usdt"),
        "config_sha256": runtime_config_hash(),
        "git_sha": current_git_sha(repo_root),
        "candidate_id": action.get("candidate_id"),
    }
    record = DecisionRecord(
        decision_id=decision_id,
        instrument=instrument,
        strategy_id=str(
            action.get("strategy_id")
            or action.get("strategy")
            or action.get("source")
            or "unassigned"
        ),
        action=decision_action,
        decided_at=now,
        snapshot_id=snapshot.snapshot_id,
        side=order_side,
        confidence=_confidence(action.get("confidence")),
        model_manifest_id=action.get("model_manifest_id"),
        outcome=terminal_outcome,
        reason_codes=tuple(dict.fromkeys((reason_code, stage_code))),
        context=context,
    )
    return warehouse.append_decision_event(record)
