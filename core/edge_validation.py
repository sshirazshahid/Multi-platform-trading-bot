"""Fail-closed final PAPER edge validation.

This module evaluates evidence; it never authorizes or places an order. Missing
provenance, costs, grouping, or forward observations is a failed gate rather
than an inferred default.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from core.stat_tests import deflated_sharpe, sharpe

VALIDATION_SCHEMA = "paper-edge-v1"
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_PREREGISTRATION_FIELDS = frozenset(
    {
        "hypothesis",
        "rules",
        "universe",
        "parameter_trials",
        "costs",
        "split_dates",
    }
)


def _profit_factor(values: np.ndarray) -> float:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _moving_block_paths(
    values: np.ndarray,
    *,
    n_resamples: int,
    block_length: int,
    seed: int,
) -> np.ndarray:
    """Circular moving-block bootstrap preserving local dependence."""
    n = int(values.size)
    if n == 0:
        return np.empty((0, 0), dtype=float)
    block = max(1, min(int(block_length), n))
    n_blocks = math.ceil(n / block)
    rng = np.random.default_rng(int(seed))
    starts = rng.integers(0, n, size=(int(n_resamples), n_blocks))
    offsets = np.arange(block, dtype=int)
    indices = (starts[..., None] + offsets) % n
    return values[indices.reshape(int(n_resamples), -1)[:, :n]]


def _bootstrap_bounds(
    values: np.ndarray,
    *,
    n_resamples: int,
    block_length: int,
    seed: int,
) -> tuple[float, float]:
    if values.size < 2 or not np.isfinite(values).all():
        point = float(values.mean()) if values.size else float("-inf")
        return point, _profit_factor(values) if values.size else 0.0
    paths = _moving_block_paths(
        values,
        n_resamples=n_resamples,
        block_length=block_length,
        seed=seed,
    )
    means = paths.mean(axis=1)
    gains = np.where(paths > 0, paths, 0.0).sum(axis=1)
    losses = -np.where(paths < 0, paths, 0.0).sum(axis=1)
    factors = np.divide(
        gains,
        losses,
        out=np.full_like(gains, np.inf),
        where=losses > 0,
    )
    lower_index = max(0, math.ceil(0.05 * len(paths)) - 1)
    return (
        float(np.sort(means)[lower_index]),
        float(np.sort(factors)[lower_index]),
    )


def _bh_adjusted(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted q-values in original order."""
    values = np.asarray(list(p_values), dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        return []
    if np.any((values < 0) | (values > 1)):
        return []
    order = np.argsort(values)
    ranked = values[order]
    m = len(values)
    adjusted = np.minimum.accumulate(
        (ranked * m / np.arange(1, m + 1))[::-1]
    )[::-1]
    out = np.empty(m, dtype=float)
    out[order] = np.minimum(1.0, adjusted)
    return out.tolist()


def _class_requirements(strategy_class: str) -> tuple[int, int]:
    key = str(strategy_class).strip().lower()
    if key in {"event", "event_driven", "unlock", "listing"}:
        return 50, 365
    if key in {"carry", "funding_carry", "basis_carry"}:
        return 60, 90
    if key in {"scalp", "execution", "exec"}:
        return 500, 90
    return 200, 90


def _group_diagnostics(
    returns: np.ndarray,
    labels: Mapping[str, Sequence[str]] | None,
    *,
    max_share: float,
) -> tuple[bool, dict[str, Any]]:
    diagnostics: dict[str, Any] = {}
    all_pass = True
    for dimension in ("symbol", "venue", "regime"):
        raw = labels.get(dimension) if isinstance(labels, Mapping) else None
        if raw is None or len(raw) != len(returns):
            diagnostics[dimension] = {
                "pass": False,
                "reason": "labels_missing_or_misaligned",
            }
            all_pass = False
            continue
        normalized = [str(value).strip() for value in raw]
        if any(not value for value in normalized):
            diagnostics[dimension] = {
                "pass": False,
                "reason": "blank_label",
            }
            all_pass = False
            continue
        grouped = {
            label: float(returns[np.asarray(normalized) == label].sum())
            for label in sorted(set(normalized))
        }
        denominator = sum(abs(value) for value in grouped.values())
        shares = {
            label: (abs(value) / denominator if denominator > 0 else 1.0)
            for label, value in grouped.items()
        }
        max_contribution = max(shares.values(), default=1.0)
        leave_one = {}
        for label in grouped:
            kept = returns[np.asarray(normalized) != label]
            leave_one[label] = bool(kept.size and float(kept.mean()) > 0)
        passed = bool(
            grouped
            and max_contribution <= max_share
            and leave_one
            and all(leave_one.values())
        )
        diagnostics[dimension] = {
            "pass": passed,
            "group_pnl": grouped,
            "shares": shares,
            "max_share": max_contribution,
            "leave_one_positive": leave_one,
        }
        all_pass = all_pass and passed
    return all_pass, diagnostics


def validate_paper_edge(
    *,
    forward_returns: Sequence[float] | None,
    strategy_class: str,
    confirmation_days: float,
    fee_slippage_costs: Sequence[float] | None,
    pbo_value: float | None,
    n_trials: int,
    family_p_values: Sequence[float] | None,
    candidate_family_index: int | None,
    group_labels: Mapping[str, Sequence[str]] | None,
    independent_event_ids: Sequence[str] | None,
    preregistration: Mapping[str, Any] | None,
    holdout_hash: str | None,
    parameter_plateau_pass: bool,
    lookahead_analysis_pass: bool,
    recursive_indicator_analysis_pass: bool,
    n_resamples: int = 2_000,
    block_length: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Evaluate every final promotion condition over resolved forward rows."""
    try:
        returns = np.asarray(
            list(() if forward_returns is None else forward_returns),
            dtype=float,
        )
    except (TypeError, ValueError):
        returns = np.asarray([], dtype=float)
    finite = bool(returns.size and np.isfinite(returns).all())
    min_observations, min_days = _class_requirements(strategy_class)
    block = block_length or max(2, round(max(1, returns.size) ** (1 / 3)))
    expectancy_lb, pf_lb = _bootstrap_bounds(
        returns,
        n_resamples=n_resamples,
        block_length=block,
        seed=seed,
    )
    pf_point = _profit_factor(returns) if finite else 0.0

    ids = list(independent_event_ids or ())
    independent_pass = bool(
        len(ids) == len(returns)
        and len(set(str(value) for value in ids)) == len(ids)
        and all(str(value).strip() for value in ids)
    )
    sample_pass = bool(
        finite
        and len(returns) >= min_observations
        and float(confirmation_days) >= min_days
        and independent_pass
    )

    honest_trials = max(1, int(n_trials))
    sr = sharpe(returns) if finite else 0.0
    dsr_probability = float(
        deflated_sharpe(
            sr_observed=sr,
            n_trials=honest_trials,
            n_obs=max(2, len(returns)),
            sr_var=1.0 / max(2, len(returns)),
        )
    )
    dsr_p = 1.0 - dsr_probability
    dsr_pass = bool(dsr_p < 0.05)
    pbo_pass = bool(
        pbo_value is not None
        and math.isfinite(float(pbo_value))
        and float(pbo_value) <= 0.20
    )

    raw_family_p = list(() if family_p_values is None else family_p_values)
    q_values = _bh_adjusted(raw_family_p)
    fdr_q = None
    if (
        q_values
        and len(q_values) >= honest_trials
        and candidate_family_index is not None
        and 0 <= int(candidate_family_index) < len(q_values)
        and math.isclose(
            float(raw_family_p[int(candidate_family_index)]),
            dsr_p,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
    ):
        fdr_q = float(q_values[int(candidate_family_index)])
    fdr_pass = bool(fdr_q is not None and fdr_q <= 0.05)

    costs_valid = False
    stressed = np.asarray([], dtype=float)
    try:
        costs = np.asarray(
            list(() if fee_slippage_costs is None else fee_slippage_costs),
            dtype=float,
        )
        costs_valid = bool(
            finite
            and costs.shape == returns.shape
            and np.isfinite(costs).all()
            and np.all(costs >= 0)
        )
        if costs_valid:
            # Forward returns are already net of 1x cost; subtract one more
            # observed cost vector to model 2x fee/slippage stress.
            stressed = returns - costs
    except (TypeError, ValueError):
        costs_valid = False
    stress_pf = _profit_factor(stressed) if costs_valid else 0.0
    stress_pass = bool(
        costs_valid
        and stressed.size
        and float(stressed.mean()) > 0
        and stress_pf > 1.0
    )

    groups_pass, groups = _group_diagnostics(
        returns,
        group_labels,
        max_share=0.35,
    )
    prereg_missing = sorted(
        field
        for field in _PREREGISTRATION_FIELDS
        if not isinstance(preregistration, Mapping) or not preregistration.get(field)
    )
    prereg_pass = bool(
        not prereg_missing
        and isinstance(holdout_hash, str)
        and _SHA256.fullmatch(holdout_hash)
        and preregistration.get("holdout_hash") == holdout_hash
    )
    diagnostics_pass = bool(
        parameter_plateau_pass
        and lookahead_analysis_pass
        and recursive_indicator_analysis_pass
    )

    gates = {
        "sample_and_duration": {
            "pass": sample_pass,
            "n": len(returns),
            "min_n": min_observations,
            "days": float(confirmation_days),
            "min_days": min_days,
            "independent_ids": independent_pass,
        },
        "expectancy_lower_bound": {
            "pass": bool(expectancy_lb > 0),
            "value": expectancy_lb,
            "threshold": 0.0,
            "method": "95pct_circular_moving_block_bootstrap",
            "block_length": block,
        },
        "profit_factor": {
            "pass": bool(pf_point >= 1.20 and pf_lb > 1.0),
            "point": pf_point,
            "lower_bound": pf_lb,
            "point_floor": 1.20,
            "lower_bound_floor": 1.0,
        },
        "deflated_sharpe": {
            "pass": dsr_pass,
            "p": dsr_p,
            "threshold": 0.05,
            "n_trials": honest_trials,
        },
        "pbo": {"pass": pbo_pass, "value": pbo_value, "ceiling": 0.20},
        "family_fdr": {
            "pass": fdr_pass,
            "q": fdr_q,
            "ceiling": 0.05,
            "family_size": len(q_values),
        },
        "cost_stress_2x": {
            "pass": stress_pass,
            "mean": float(stressed.mean()) if stressed.size else None,
            "profit_factor": stress_pf,
        },
        "concentration_and_leave_one_out": {
            "pass": groups_pass,
            "max_share": 0.35,
            "dimensions": groups,
        },
        "preregistration_and_holdout": {
            "pass": prereg_pass,
            "missing": prereg_missing,
            "holdout_hash": holdout_hash,
        },
        "bias_diagnostics": {
            "pass": diagnostics_pass,
            "parameter_plateau": bool(parameter_plateau_pass),
            "lookahead_analysis": bool(lookahead_analysis_pass),
            "recursive_indicator_analysis": bool(
                recursive_indicator_analysis_pass
            ),
        },
    }
    passed = all(item["pass"] for item in gates.values())
    return {
        "validation_schema": VALIDATION_SCHEMA,
        "pass": bool(passed),
        "gates": gates,
    }


__all__ = ["VALIDATION_SCHEMA", "validate_paper_edge"]
