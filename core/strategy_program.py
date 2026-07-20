"""Frozen runtime strategy catalog for the correctness rebuild.

Research code may describe additional ideas, but only this catalog can declare
a strategy eligible for an order-producing runtime. Unknown IDs fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.contracts import StrategySpec


class StrategyProgramStatus(str, Enum):
    PAPER_CANDIDATE = "PAPER_CANDIDATE"
    SHADOW_ONLY = "SHADOW_ONLY"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"
    FILTER_RESEARCH_ONLY = "FILTER_RESEARCH_ONLY"
    REJECTED_DISABLED = "REJECTED_DISABLED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True, slots=True)
class StrategyProgramEntry:
    spec: StrategySpec
    status: StrategyProgramStatus
    aliases: tuple[str, ...] = ()
    paper_eligible: bool = False
    live_eligible: bool = False


@dataclass(frozen=True, slots=True)
class StrategyProgramDecision:
    allowed: bool
    reason: str
    strategy_id: str
    status: str


_VENUES = ("binance", "bybit", "bitget")


def _spec(
    strategy_id: str,
    version: str,
    family: str,
    market_types: tuple[str, ...],
    status: StrategyProgramStatus,
    *,
    role: str,
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        strategy_version=version,
        family=family,
        market_types=market_types,
        venues=_VENUES,
        entry_rules={"role": role},
        exit_rules={"deterministic": True},
        sizing={"self_promotion": False},
        risk_limits={"runtime_status": status.value},
        validation_status="edge_unproven",
        promotion_status=status.value,
    )


_ENTRIES = (
    StrategyProgramEntry(
        _spec(
            "F1",
            "correctness-v1",
            "delta_neutral_carry",
            ("spot", "perpetual"),
            StrategyProgramStatus.PAPER_CANDIDATE,
            role="same-venue long spot plus equal-notional short USDT perpetual",
        ),
        StrategyProgramStatus.PAPER_CANDIDATE,
        aliases=("spot_perp_carry", "carry"),
        paper_eligible=True,
    ),
    StrategyProgramEntry(
        # Owner directive 2026-07-18: aggressive directional PAPER research.
        # PAPER ONLY: the mcp score is measured non-predictive (corr ~ -0.008)
        # and the lane is currently negative after costs. It is named as
        # directional research rather than an "accuracy band" because runtime
        # correctly keeps the old hit-rate geometry disabled. live_eligible
        # stays False; promotion requires positive after-cost forward evidence.
        _spec(
            "MCP_DIRECTIONAL_PAPER",
            "directional-research-v1",
            "algorithmic_scorer_directional",
            ("perpetual",),
            StrategyProgramStatus.PAPER_CANDIDATE,
            role="mcp deterministic scorer, PAPER-only directional research cohort",
        ),
        StrategyProgramStatus.PAPER_CANDIDATE,
        # "algo_det" = the same deterministic-scorer lane as presented by
        # order_manager's raw-id defense-in-depth check (advisory off).
        aliases=("mcp_registry", "algo_det"),
        paper_eligible=True,
    ),
    StrategyProgramEntry(
        _spec(
            "F2_XVENUE_FUNDING_SPREAD",
            "shadow-v1",
            "cross_venue_relative_value",
            ("perpetual",),
            StrategyProgramStatus.SHADOW_ONLY,
            role="prefunded cross-venue normalized-funding spread",
        ),
        StrategyProgramStatus.SHADOW_ONLY,
        aliases=("F2", "cross_venue_spread"),
    ),
    StrategyProgramEntry(
        _spec(
            "D1_UNLOCK_SHORT",
            "w1-w2-forward-v1",
            "event_short",
            ("perpetual",),
            StrategyProgramStatus.SHADOW_ONLY,
            role="non-insider cliff unlock W1/W2 forward probe",
        ),
        StrategyProgramStatus.SHADOW_ONLY,
        aliases=("D1", "pre_unlock_short_capital_scaled"),
    ),
    StrategyProgramEntry(
        _spec(
            "SPOT_S1",
            "sma200-atr14-v1",
            "spot_risk_control",
            ("spot",),
            StrategyProgramStatus.RECOMMENDATION_ONLY,
            role="BTC/ETH SMA200 plus-or-minus 0.5 ATR14 defensive allocation",
        ),
        StrategyProgramStatus.RECOMMENDATION_ONLY,
        aliases=("S1", "spot_s1"),
    ),
    StrategyProgramEntry(
        _spec(
            "SPOT_S2_ROTATION",
            "momentum28-sma50-v1",
            "spot_rotation",
            ("spot",),
            StrategyProgramStatus.SHADOW_ONLY,
            role="liquid top-2-to-4 spot momentum rotation in supportive regimes",
        ),
        StrategyProgramStatus.SHADOW_ONLY,
        aliases=("S2", "spot_s2"),
    ),
)

_REJECTED_ALIASES = frozenset({
    # "mcp_registry" maps to the PAPER-only directional research entry.
    "machine_registry",
    "tsmom_registry",
    "s3_registry",
    "ema_rsi",
    "stochastic",
    "harmonic",
    "ict",
    "asian_breakout",
    "bollinger_squeeze",
    "dow_swing",
    "kalman_pairs",
    "pca_mean_reversion",
    "kronos",
    "grid",
    "market_making",
    "vpin_alpha",
    "f3_relative_value",
})
_FILTER_ONLY_ALIASES = frozenset({"hmm_filter", "vpin_filter"})
_OUT_OF_SCOPE_ALIASES = frozenset({"earnings_iv_crush", "options_dispersion"})

_LOOKUP: dict[str, StrategyProgramEntry] = {}
for _entry in _ENTRIES:
    for _name in (_entry.spec.strategy_id, *_entry.aliases):
        _LOOKUP[_name.strip().lower()] = _entry


def strategy_program_entry(strategy_id: str) -> StrategyProgramEntry | None:
    return _LOOKUP.get(str(strategy_id or "").strip().lower())


def strategy_program_decision(
    strategy_id: str,
    *,
    target: str,
) -> StrategyProgramDecision:
    """Authorize catalog eligibility only; entry policy remains a separate gate."""
    normalized = str(strategy_id or "").strip()
    key = normalized.lower()
    entry = strategy_program_entry(normalized)
    if entry is None:
        if key in _REJECTED_ALIASES:
            status = StrategyProgramStatus.REJECTED_DISABLED
        elif key in _FILTER_ONLY_ALIASES:
            status = StrategyProgramStatus.FILTER_RESEARCH_ONLY
        elif key in _OUT_OF_SCOPE_ALIASES:
            status = StrategyProgramStatus.OUT_OF_SCOPE
        else:
            return StrategyProgramDecision(
                False, "strategy_not_in_frozen_program", normalized, "UNKNOWN"
            )
        return StrategyProgramDecision(
            False,
            f"strategy_runtime_status_{status.value.lower()}",
            normalized,
            status.value,
        )

    target_name = str(target or "").strip().upper()
    eligible = (
        entry.paper_eligible
        if target_name == "PAPER"
        else entry.live_eligible if target_name == "LIVE" else False
    )
    if not eligible:
        return StrategyProgramDecision(
            False,
            f"strategy_runtime_status_{entry.status.value.lower()}",
            entry.spec.strategy_id,
            entry.status.value,
        )
    return StrategyProgramDecision(
        True,
        f"strategy_program_{target_name.lower()}_eligible",
        entry.spec.strategy_id,
        entry.status.value,
    )


def all_strategy_program_entries() -> tuple[StrategyProgramEntry, ...]:
    return _ENTRIES


__all__ = [
    "StrategyProgramDecision",
    "StrategyProgramEntry",
    "StrategyProgramStatus",
    "all_strategy_program_entries",
    "strategy_program_decision",
    "strategy_program_entry",
]
