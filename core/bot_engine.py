"""
core/bot_engine.py — Permanent facade for the engine package.

All implementation lives under core/engine/; this module re-exports the
public API so existing imports (main, tests, strategies) keep working.
"""
from core.engine.engine import BotEngine
from core.engine.helpers import (
    CLAUDE_PORTFOLIO,
    LEARN_INTERVAL,
    MAX_ACTIONS_PER_CYCLE,
    MAX_PER_EXCHANGE,
    MAX_TOTAL_POSITIONS,
    PORTFOLIO_CYCLE_SEC,
    _STRUCTURAL_ERRORS,
    _UNIFIED_EXCHANGES,
    _boot_profile_log_lines,
    _canonical_exit_reason,
    _deployable_total,
    _effective_tp_threshold,
    _is_mcp_directional_paper_futures,
    _live_entry_clock_drift_rejection,
    _tier_blocked_by_cap,
    console,
    sample_clock_drift_ms,
    smart_money_entry_rejection,
    time,
)
from config import DRY_RUN

__all__ = [
    "BotEngine",
    "CLAUDE_PORTFOLIO",
    "DRY_RUN",
    "LEARN_INTERVAL",
    "MAX_ACTIONS_PER_CYCLE",
    "MAX_PER_EXCHANGE",
    "MAX_TOTAL_POSITIONS",
    "PORTFOLIO_CYCLE_SEC",
    "_STRUCTURAL_ERRORS",
    "_UNIFIED_EXCHANGES",
    "_boot_profile_log_lines",
    "_canonical_exit_reason",
    "_deployable_total",
    "_effective_tp_threshold",
    "_is_mcp_directional_paper_futures",
    "_live_entry_clock_drift_rejection",
    "_tier_blocked_by_cap",
    "console",
    "sample_clock_drift_ms",
    "smart_money_entry_rejection",
    "time",
]
