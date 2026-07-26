"""Bounded machine-strategy mutator for the PAPER self-improvement loop (R5 / R6).

Extends ``SelfHealingSupervisor``'s candidate grid toward SHORT lookbacks (R6 — the
edge, if any, concentrates at short horizons; never hardcode volume-weighting or a
preprint trailing-stop, let the gate decide) and runs each variant through the
EXISTING T+1, cost-realistic replay (``scripts.machine_strategy_replay``) to produce
an embargoed chronological validation slice for ``promotion_loop.Candidate``.

This module writes NOTHING — ``promotion_loop`` owns every (PAPER-latched) write.
Every grid config is bounded to ``adaptive_config.TOP_LEVEL_RANGES`` so a promoted
variant is always sanitizer-valid. Candidate construction requires one variant ID
to be fixed before replay. Missing/ambiguous OHLCV, reused-sample evidence, or any
replay error is reported and yields no candidate, which is the correct fail-closed
outcome under the confirmed NO_EDGE regime.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.decision.promotion_loop import Candidate

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# R6: weight the grid toward SHORT horizons (small scalp_time_stop_bars first).
_SHORT_TIME_STOPS = (6, 12, 24)
_MIN_SCORES = (0.34, 0.45)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayEvidence:
    """Disjoint replay slices and their audit trail.

    Only ``validation_trades`` may populate ``Candidate.replay_trades``. A plain
    list is deliberately not accepted because it cannot prove that parameter
    selection and evaluation used different chronological samples.
    """

    selection_trades: tuple[float, ...] = ()
    validation_trades: tuple[float, ...] = ()
    independent_validation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


_LAST_BUILD_REPORT: dict[str, Any] = {
    "status": "NOT_RUN",
    "reasons": ["build_candidates has not run"],
}


def last_build_report() -> dict[str, Any]:
    """Return the most recent fail-closed mutator audit without exposing mutation."""
    return copy.deepcopy(_LAST_BUILD_REPORT)


def _records_digest(records: list[dict]) -> str:
    payload = "\n".join(
        f"{record.get('symbol')}|{record.get('ts')}|{record.get('side')}"
        for record in sorted(
            records,
            key=lambda row: (
                float(row.get("ts") or 0.0),
                str(row.get("symbol") or ""),
                str(row.get("side") or ""),
            ),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mutate_machine_configs() -> list[dict[str, Any]]:
    """Short-lookback-weighted machine-strategy variants (bounded, sanitizer-valid)."""
    grid: list[dict[str, Any]] = []
    for tstop in _SHORT_TIME_STOPS:
        for min_score in _MIN_SCORES:
            unconfirmed = max(min_score, 0.45)
            grid.append(
                {
                    "id": f"mut_ts{tstop}_ms{int(min_score * 100)}",
                    "args": {
                        "min_score": min_score,
                        "unconfirmed_min_score": unconfirmed,
                        "rr": 2.0,
                        "atr_stop_mult": 2.0,
                    },
                    "config": {
                        "min_score": min_score,
                        "unconfirmed_min_score": unconfirmed,
                        "rr": 2.0,
                        "atr_stop_mult": 2.0,
                        "scalp_time_stop_bars": tstop,
                    },
                }
            )
    return grid


def _default_replay(
    entry: dict[str, Any],
    *,
    cache_dir: str = "data/ohlcv_cache",
    timeframe: str = "15m",
    max_files: int = 8,
    max_bars: int = 1500,
) -> ReplayEvidence:
    """Run one config through the existing T+1, cost-realistic replay in-process.

    Returns disjoint chronological selection/validation evidence. Fail-soft means
    an ineligible ``ReplayEvidence`` with explicit reasons; it never relabels spot
    files as futures and never turns missing data into promotion evidence."""
    try:
        import argparse

        from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine
        from scripts.machine_strategy_replay import (
            prepare_replay_datasets,
            replay_chronological_holdout,
        )

        a = entry["args"]
        cfg = MachineStrategyConfig(
            min_score=float(a["min_score"]),
            rr=float(a["rr"]),
            atr_stop_mult=float(a["atr_stop_mult"]),
            scalp_time_stop_bars=int(entry["config"].get("scalp_time_stop_bars", 12)),
            confirmation_detectors=("ema_rsi",),
            unconfirmed_min_score=float(a["unconfirmed_min_score"]),
        )
        engine = MachineStrategyEngine(cfg)
        args = argparse.Namespace(
            cache_dir=cache_dir,
            timeframe=timeframe,
            market_type="futures",
            exact=False,
            step=1,
            min_bars=80,
            max_files=max_files,
            max_bars=max_bars,
            minimum_symbols=max(1, min(int(max_files), 8)) if max_files > 0 else 8,
            minimum_history_bars=500,
            validation_fraction=0.30,
            embargo_bars=max(24, int(entry["config"].get("scalp_time_stop_bars", 12))),
            fee_bps_side=6.0,
            entry_slip_bps=5.0,
            tp_slip_bps=5.0,
            stop_slip_bps=10.0,
        )
        datasets, dataset_audit = prepare_replay_datasets(args)
        selected_records: list[dict] = []
        validated_records: list[dict] = []
        split_protocols: list[dict] = []
        for dataset, frame in datasets:
            selected, validated, protocol = replay_chronological_holdout(
                frame, dataset.symbol, engine, args
            )
            selected_records.extend(selected)
            validated_records.extend(validated)
            split_protocols.append({"symbol": dataset.symbol, **protocol})
        selected_records.sort(
            key=lambda row: (float(row.get("ts") or 0.0), str(row.get("symbol") or ""))
        )
        validated_records.sort(
            key=lambda row: (float(row.get("ts") or 0.0), str(row.get("symbol") or ""))
        )
        reasons = list(dataset_audit.get("reasons") or [])
        reasons.extend(
            f"{row['symbol']}: {row.get('reason')}"
            for row in split_protocols
            if not row.get("eligible")
        )
        return ReplayEvidence(
            selection_trades=tuple(float(row["ret"]) for row in selected_records),
            validation_trades=tuple(float(row["ret"]) for row in validated_records),
            independent_validation=not reasons,
            metadata={
                "eligible": not reasons,
                "market_type": "futures",
                "protocol": "chronological_holdout_v1",
                "dataset_audit": dataset_audit,
                "split_protocols": split_protocols,
                "selection_event_sha256": _records_digest(selected_records),
                "validation_event_sha256": _records_digest(validated_records),
                "reasons": reasons,
            },
        )
    except Exception as exc:
        return ReplayEvidence(
            metadata={
                "eligible": False,
                "market_type": "futures",
                "protocol": "chronological_holdout_v1",
                "reasons": [f"replay_error: {type(exc).__name__}: {exc}"],
            }
        )


def build_candidates(
    replay_fn: Callable[[dict], ReplayEvidence] | None = None,
    *,
    min_trades: int = 30,
    selected_variant_id: str | None = None,
) -> list[Candidate]:
    """Produce promotion-ready candidates from the mutated grid.

    ``selected_variant_id`` must be fixed before replay. This prevents the
    self-healing loop from testing every variant on a shared validation sample and
    then calling the winner out-of-sample. The replay must return ``ReplayEvidence``
    with an independent, embargoed validation slice; legacy flat return lists are
    blocked as reused-sample evidence. ``n_configs_tested`` remains the full grid
    size for an honest Deflated-Sharpe haircut.
    """
    global _LAST_BUILD_REPORT
    replay_fn = replay_fn or _default_replay
    grid = mutate_machine_configs()
    n_tested = len(grid)
    selected = next(
        (entry for entry in grid if entry["id"] == str(selected_variant_id or "")),
        None,
    )
    if selected is None:
        reason = (
            "selected_variant_id must name one preregistered grid variant before replay"
            if not selected_variant_id
            else f"unknown selected_variant_id: {selected_variant_id}"
        )
        _LAST_BUILD_REPORT = {
            "status": "BLOCKED",
            "reasons": [reason],
            "available_variant_ids": [entry["id"] for entry in grid],
            "n_configs_tested": n_tested,
        }
        logger.warning("Mutator candidate blocked: %s", reason)
        return []

    try:
        evidence = replay_fn(selected)
    except Exception as exc:
        evidence = ReplayEvidence(metadata={"eligible": False, "reasons": [str(exc)]})
    if not isinstance(evidence, ReplayEvidence):
        reason = "legacy flat replay results are reused-sample evidence and are not eligible"
        _LAST_BUILD_REPORT = {
            "status": "BLOCKED",
            "variant_id": selected["id"],
            "reasons": [reason],
            "n_configs_tested": n_tested,
        }
        logger.warning("Mutator candidate blocked: %s", reason)
        return []

    reasons = list(evidence.metadata.get("reasons") or [])
    if not evidence.metadata.get("eligible"):
        reasons.append("replay dataset/protocol is ineligible")
    if not evidence.independent_validation:
        reasons.append("validation is not proven independent from parameter selection")
    validation = list(evidence.validation_trades)
    if len(validation) < int(min_trades):
        reasons.append(f"validation_trades {len(validation)} < {int(min_trades)}")
    if reasons:
        _LAST_BUILD_REPORT = {
            "status": "BLOCKED",
            "variant_id": selected["id"],
            "reasons": list(dict.fromkeys(reasons)),
            "selection_trades": len(evidence.selection_trades),
            "validation_trades": len(validation),
            "n_configs_tested": n_tested,
            "replay": copy.deepcopy(evidence.metadata),
        }
        logger.warning("Mutator candidate blocked: %s", "; ".join(_LAST_BUILD_REPORT["reasons"]))
        return []

    candidate_evidence = {
        "source": "research.mutator",
        "market_type": "futures",
        "protocol": "chronological_holdout_v1",
        "preregistered_variant_id": selected["id"],
        "selection_n_trades": len(evidence.selection_trades),
        "validation_n_trades": len(validation),
        "candidate_returns_role": "untouched_validation_only",
        "replay": copy.deepcopy(evidence.metadata),
    }
    _LAST_BUILD_REPORT = {
        "status": "ELIGIBLE_FOR_PROMOTION_GATE",
        "variant_id": selected["id"],
        "reasons": [],
        "selection_trades": len(evidence.selection_trades),
        "validation_trades": len(validation),
        "n_configs_tested": n_tested,
    }
    return [
        Candidate(
            variant_id=selected["id"],
            config=selected["config"],
            replay_trades=validation,
            n_configs_tested=n_tested,
            evidence=candidate_evidence,
        )
    ]
