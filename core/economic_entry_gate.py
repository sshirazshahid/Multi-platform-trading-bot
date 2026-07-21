"""Pure economic admission rule for the MCP directional PAPER lane.

The scorer may keep emitting candidates and shadow observations when a model is
missing or a trade has negative expected value.  This module governs only the
last order-producing boundary: a directional futures entry needs a currently
promoted model probability and must clear a stressed, after-cost break-even
probability by a predeclared margin.

All returns are fractions of entry notional (``0.01`` means one percent).  The
calculation deliberately treats round-trip costs as paid in both outcomes:

    E[r] = p_win * target - (1 - p_win) * stop - stressed_round_trip_cost

No realized labels, historical win-rate buckets, or target/stop geometry are
changed by this gate.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EconomicEntryDecision:
    """JSON-safe audit result for one proposed entry."""

    allowed: bool
    reason: str
    model_version: str | None = None
    promoted_model_version: str | None = None
    p_win: float | None = None
    stop_frac: float | None = None
    target_frac: float | None = None
    entry_fee_frac: float | None = None
    entry_slippage_frac: float | None = None
    exit_fee_frac: float | None = None
    exit_slippage_frac: float | None = None
    fee_stress_multiplier: float | None = None
    slippage_stress_multiplier: float | None = None
    stressed_entry_cost_frac: float | None = None
    stressed_exit_cost_frac: float | None = None
    stressed_round_trip_cost_frac: float | None = None
    breakeven_p_win: float | None = None
    probability_margin: float | None = None
    required_p_win: float | None = None
    stressed_expectancy_frac: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _version(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in {
        "none",
        "null",
        "fallback",
        "rule_only",
        "rule-only",
        "unpromoted",
    }:
        return None
    return normalized


def _evaluate_stressed_breakeven_fallback(
    *,
    base: dict,
    stop_frac: Any,
    target_frac: Any,
    entry_fee_frac: Any,
    entry_slippage_frac: Any,
    exit_fee_frac: Any,
    exit_slippage_frac: Any,
    stressed_exit_cost_floor_frac: Any,
    fee_stress_multiplier: Any,
    slippage_stress_multiplier: Any,
    probability_margin: Any,
) -> EconomicEntryDecision:
    """PAPER-profile geometric fallback when NO promoted model exists.

    The model-probability term is skipped — no probability is fabricated —
    but the bracket must still clear the SAME stressed round-trip costs:
    admission requires breakeven WR < 1.0, i.e. the TP pays more than the
    stressed costs so a winner is a net winner.  This is NOT an edge claim;
    it only refuses entries whose bracket cannot clear stressed costs even
    at a 100% hit rate.
    """

    stop = _finite_number(stop_frac)
    target = _finite_number(target_frac)
    if stop is None or target is None or stop <= 0.0 or target <= 0.0:
        return EconomicEntryDecision(
            False,
            "economic_gate_geometry_invalid",
            stop_frac=stop,
            target_frac=target,
            **base,
        )

    entry_fee = _finite_number(entry_fee_frac)
    entry_slip = _finite_number(entry_slippage_frac)
    exit_fee = _finite_number(exit_fee_frac)
    exit_slip = _finite_number(exit_slippage_frac)
    exit_floor = _finite_number(stressed_exit_cost_floor_frac)
    fee_stress = _finite_number(fee_stress_multiplier)
    slip_stress = _finite_number(slippage_stress_multiplier)
    margin = _finite_number(probability_margin)
    costs = (entry_fee, entry_slip, exit_fee, exit_slip, exit_floor)
    if any(value is None or value < 0.0 for value in costs):
        return EconomicEntryDecision(
            False,
            "economic_gate_cost_invalid",
            stop_frac=stop,
            target_frac=target,
            **base,
        )
    if (
        fee_stress is None
        or slip_stress is None
        or fee_stress < 1.0
        or slip_stress < 1.0
        or margin is None
        or not 0.0 <= margin <= 1.0
    ):
        return EconomicEntryDecision(
            False,
            "economic_gate_config_invalid",
            stop_frac=stop,
            target_frac=target,
            fee_stress_multiplier=fee_stress,
            slippage_stress_multiplier=slip_stress,
            probability_margin=margin,
            **base,
        )

    stressed_entry = fee_stress * entry_fee + slip_stress * entry_slip
    stressed_exit = max(exit_floor, fee_stress * exit_fee + slip_stress * exit_slip)
    round_trip = stressed_entry + stressed_exit
    breakeven = (stop + round_trip) / (stop + target)
    audit = {
        **base,
        "stop_frac": stop,
        "target_frac": target,
        "entry_fee_frac": entry_fee,
        "entry_slippage_frac": entry_slip,
        "exit_fee_frac": exit_fee,
        "exit_slippage_frac": exit_slip,
        "fee_stress_multiplier": fee_stress,
        "slippage_stress_multiplier": slip_stress,
        "stressed_entry_cost_frac": stressed_entry,
        "stressed_exit_cost_frac": stressed_exit,
        "stressed_round_trip_cost_frac": round_trip,
        "breakeven_p_win": breakeven,
        "probability_margin": margin,
    }
    if breakeven >= 1.0:
        return EconomicEntryDecision(
            False, "economic_gate_stressed_breakeven", **audit
        )
    return EconomicEntryDecision(
        True, "economic_gate_paper_fallback_pass", **audit
    )


def evaluate_directional_entry(
    *,
    model_version: Any,
    promoted_model_version: Any,
    p_win: Any,
    stop_frac: Any,
    target_frac: Any,
    entry_fee_frac: Any,
    entry_slippage_frac: Any,
    exit_fee_frac: Any,
    exit_slippage_frac: Any,
    stressed_exit_cost_floor_frac: Any,
    fee_stress_multiplier: Any,
    slippage_stress_multiplier: Any,
    probability_margin: Any,
    paper_fallback: bool = False,
) -> EconomicEntryDecision:
    """Evaluate one MCP directional futures entry with no side effects.

    ``promoted_model_version`` is the version of the bundle already admitted by
    the repository's authoritative promotion-pointer validator.  Requiring an
    exact match prevents a caller from supplying an arbitrary or stale version
    string merely to bypass the rule-only fallback refusal.

    ``paper_fallback`` (2026-07-21, profile-gated by the caller to
    PAPER + MAX_FLOW_BAND): when True AND no promoted model exists, admission
    is decided by :func:`_evaluate_stressed_breakeven_fallback` instead of
    refusing outright.  Whenever a promoted model IS available, the original
    model path below runs unchanged in both modes.
    """

    version = _version(model_version)
    promoted = _version(promoted_model_version)
    base = {
        "model_version": version,
        "promoted_model_version": promoted,
    }
    if paper_fallback and promoted is None:
        return _evaluate_stressed_breakeven_fallback(
            base=base,
            stop_frac=stop_frac,
            target_frac=target_frac,
            entry_fee_frac=entry_fee_frac,
            entry_slippage_frac=entry_slippage_frac,
            exit_fee_frac=exit_fee_frac,
            exit_slippage_frac=exit_slippage_frac,
            stressed_exit_cost_floor_frac=stressed_exit_cost_floor_frac,
            fee_stress_multiplier=fee_stress_multiplier,
            slippage_stress_multiplier=slippage_stress_multiplier,
            probability_margin=probability_margin,
        )
    if version is None:
        return EconomicEntryDecision(False, "economic_gate_model_missing", **base)
    if promoted is None:
        return EconomicEntryDecision(
            False, "economic_gate_promoted_model_unavailable", **base
        )
    if version != promoted:
        return EconomicEntryDecision(False, "economic_gate_model_not_promoted", **base)

    probability = _finite_number(p_win)
    if probability is None or not 0.0 <= probability <= 1.0:
        return EconomicEntryDecision(
            False, "economic_gate_p_win_invalid", p_win=probability, **base
        )

    stop = _finite_number(stop_frac)
    target = _finite_number(target_frac)
    if stop is None or target is None or stop <= 0.0 or target <= 0.0:
        return EconomicEntryDecision(
            False,
            "economic_gate_geometry_invalid",
            p_win=probability,
            stop_frac=stop,
            target_frac=target,
            **base,
        )

    entry_fee = _finite_number(entry_fee_frac)
    entry_slip = _finite_number(entry_slippage_frac)
    exit_fee = _finite_number(exit_fee_frac)
    exit_slip = _finite_number(exit_slippage_frac)
    exit_floor = _finite_number(stressed_exit_cost_floor_frac)
    fee_stress = _finite_number(fee_stress_multiplier)
    slip_stress = _finite_number(slippage_stress_multiplier)
    margin = _finite_number(probability_margin)
    costs = (entry_fee, entry_slip, exit_fee, exit_slip, exit_floor)
    if any(value is None or value < 0.0 for value in costs):
        return EconomicEntryDecision(
            False,
            "economic_gate_cost_invalid",
            p_win=probability,
            stop_frac=stop,
            target_frac=target,
            **base,
        )
    if (
        fee_stress is None
        or slip_stress is None
        or fee_stress < 1.0
        or slip_stress < 1.0
        or margin is None
        or not 0.0 <= margin <= 1.0
    ):
        return EconomicEntryDecision(
            False,
            "economic_gate_config_invalid",
            p_win=probability,
            stop_frac=stop,
            target_frac=target,
            fee_stress_multiplier=fee_stress,
            slippage_stress_multiplier=slip_stress,
            probability_margin=margin,
            **base,
        )

    stressed_entry = fee_stress * entry_fee + slip_stress * entry_slip
    stressed_exit = max(exit_floor, fee_stress * exit_fee + slip_stress * exit_slip)
    round_trip = stressed_entry + stressed_exit
    breakeven = (stop + round_trip) / (stop + target)
    required = breakeven + margin
    expectancy = probability * target - (1.0 - probability) * stop - round_trip
    audit = {
        **base,
        "p_win": probability,
        "stop_frac": stop,
        "target_frac": target,
        "entry_fee_frac": entry_fee,
        "entry_slippage_frac": entry_slip,
        "exit_fee_frac": exit_fee,
        "exit_slippage_frac": exit_slip,
        "fee_stress_multiplier": fee_stress,
        "slippage_stress_multiplier": slip_stress,
        "stressed_entry_cost_frac": stressed_entry,
        "stressed_exit_cost_frac": stressed_exit,
        "stressed_round_trip_cost_frac": round_trip,
        "breakeven_p_win": breakeven,
        "probability_margin": margin,
        "required_p_win": required,
        "stressed_expectancy_frac": expectancy,
    }
    if expectancy <= 0.0:
        return EconomicEntryDecision(
            False, "economic_gate_negative_expectancy", **audit
        )
    if probability < required:
        return EconomicEntryDecision(
            False, "economic_gate_probability_margin_not_met", **audit
        )
    return EconomicEntryDecision(True, "economic_gate_pass", **audit)


__all__ = ["EconomicEntryDecision", "evaluate_directional_entry"]
