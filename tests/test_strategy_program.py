from __future__ import annotations

from core.strategy_program import (
    StrategyProgramStatus,
    all_strategy_program_entries,
    strategy_program_decision,
    strategy_program_entry,
)


def test_paper_eligible_set_is_frozen_and_none_are_live_eligible():
    # F1 (validated carry) + MCP_DIRECTIONAL_PAPER (owner directive 2026-07-18,
    # PAPER-only research cohort). Any further addition must update this
    # pin deliberately.
    entries = all_strategy_program_entries()
    assert {entry.spec.strategy_id for entry in entries if entry.paper_eligible} == {
        "F1",
        "MCP_DIRECTIONAL_PAPER",
    }
    assert not any(entry.live_eligible for entry in entries)


def test_f2_unlock_and_spot_lanes_are_not_order_eligible():
    for strategy_id in (
        "F2_XVENUE_FUNDING_SPREAD",
        "cross_venue_spread",
        "D1",
        "SPOT_S1",
        "SPOT_S2_ROTATION",
    ):
        decision = strategy_program_decision(strategy_id, target="PAPER")
        assert decision.allowed is False


def test_rejected_filter_only_and_unknown_strategies_fail_closed():
    assert strategy_program_decision(
        "ema_rsi", target="PAPER"
    ).status == StrategyProgramStatus.REJECTED_DISABLED.value
    assert strategy_program_decision(
        "vpin_filter", target="PAPER"
    ).status == StrategyProgramStatus.FILTER_RESEARCH_ONLY.value
    assert strategy_program_decision(
        "future_plugin", target="PAPER"
    ).reason == "strategy_not_in_frozen_program"


def test_aliases_resolve_to_the_canonical_immutable_spec():
    entry = strategy_program_entry("carry")
    assert entry is not None
    assert entry.spec.strategy_id == "F1"
    assert entry.spec.strategy_version == "correctness-v1"


def test_mcp_directional_paper_is_paper_only_owner_directive_2026_07_18():
    """Owner directive: directional research trades PAPER only, never live."""
    d_paper = strategy_program_decision("mcp_registry", target="PAPER")
    assert d_paper.allowed is True
    assert d_paper.strategy_id == "MCP_DIRECTIONAL_PAPER"
    d_live = strategy_program_decision("mcp_registry", target="LIVE")
    assert d_live.allowed is False


def test_algo_det_raw_id_resolves_to_paper_only_band_entry():
    """order_manager's raw-id check presents algo_det; must resolve identically."""
    d = strategy_program_decision("algo_det", target="PAPER")
    assert d.allowed is True and d.strategy_id == "MCP_DIRECTIONAL_PAPER"
    assert strategy_program_decision("algo_det", target="LIVE").allowed is False
