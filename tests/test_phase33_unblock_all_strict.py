"""Phase 33 — UNBLOCK_ALL_STRICT (2026-05-05).

User directive: "Remove any blocks, blacklist. Trade ANY PAIR whether
SPOT/FUTURE if the bot analyzes its going to be profitable."

Audit of remaining gates:
  ✅ Already removed: BLACKLIST_HARD, ALLOWED_HOURS, BLOCKED_HOURS,
     SHORTS_REQUIRE_BTC_BEAR, caution flags
  ❌ Still gating before Phase 33:
     - EXPECTANCY_FILTER.whitelist (DOGE bypass — operator override)
     - ShortGate (rolling 30-trade SELL WR<45% blanket pause)
     - Spec §12 per-symbol pause (2 consec losses → 6h)
     - Spec §12 per-family pause (3 consec losses → 12h)

Phase 33 disables the four remaining static-rule blocks. The
following stay ON because they ARE the per-trade analysis the user
wants:
  - Phase 23 calibrator hard-refuse <40% (data-driven)
  - Phase 27 graduated EV per (symbol, side) (data-driven)
  - Phase 29 post-SL cooldown (time-based protection, 30min/6h)

The catastrophic SAFETY RAIL stays ON regardless of UNBLOCK_ALL:
  - Spec §12 GLOBAL halt (5 consec losses → 4h cooldown)
  - Drawdown halt (Phase 25 — 60min cooldown after extreme DD)
  - daily loss circuit breaker

Implementation: three new config flags (SHORT_GATE_ENABLED,
SPEC12_SYMBOL_PAUSE_ENABLED, SPEC12_FAMILY_PAUSE_ENABLED) all default
False. The existing code paths now check these flags before firing.
"""
from __future__ import annotations

from pathlib import Path

# ─── Config flags ─────────────────────────────────────────────────────


def test_short_gate_disabled_phase33():
    from config import SHORT_GATE_ENABLED
    assert SHORT_GATE_ENABLED is False


def test_spec12_symbol_pause_disabled_phase33():
    from config import SPEC12_SYMBOL_PAUSE_ENABLED
    assert SPEC12_SYMBOL_PAUSE_ENABLED is False


def test_spec12_family_pause_disabled_phase33():
    from config import SPEC12_FAMILY_PAUSE_ENABLED
    assert SPEC12_FAMILY_PAUSE_ENABLED is False


def test_expectancy_whitelist_emptied_phase33():
    """Operator override removed — DOGE evaluated on data terms now."""
    from config import EXPECTANCY_FILTER
    wl = EXPECTANCY_FILTER.get("whitelist") or {}
    # Empty dict / set / list — no entries
    assert len(wl) == 0


# ─── Static lists confirmed clear ─────────────────────────────────────


def test_blacklist_hard_empty_under_unblock_all():
    """2026-05-21 UNBLOCK_ALL: BLACKLIST_HARD cleared per user directive
    ("Clear all blacklist and blocked coins"). This SUPERSEDES the Phase-39
    (2026-05-09) re-enablement that the prior version of this test pinned —
    Phase 39 re-added {SOL,XRP,APT,ETH,DOGE,BTC}, then UNBLOCK_ALL removed
    them again and the test was never updated. Matches this file's own
    docstring (line 7) + memory feedback_unblock_all_trades_2026_04_28.
    NOTE: the SCALP_TIER_ENABLED=false kill switch restores the Phase-39
    set; this asserts the DEFAULT (SCALP on) state."""
    from config import BLACKLIST_HARD
    assert BLACKLIST_HARD == set(), (
        f"UNBLOCK_ALL requires empty BLACKLIST_HARD; got {BLACKLIST_HARD}")


def test_all_hours_allowed_unblock_directive():
    """UNBLOCK directive (2026-05-27): all 24 hours allowed. No hour blocks
    if MCP Brain analysis is correct and TP is set accurately."""
    from config import ALLOWED_HOURS_UTC, BLOCKED_HOURS_UTC
    assert ALLOWED_HOURS_UTC == set(range(24))
    assert BLOCKED_HOURS_UTC == set()


def test_blocked_hours_empty_per_unblock_all():
    """2026-05-21 UNBLOCK_ALL: no hour-of-day entry blocks remain."""
    from config import BLOCKED_HOURS_UTC
    assert BLOCKED_HOURS_UTC == set(), (
        f"BLOCKED_HOURS_UTC must be empty under UNBLOCK_ALL, got {BLOCKED_HOURS_UTC}")


def test_shorts_require_btc_bear_off():
    from config import SHORTS_REQUIRE_BTC_BEAR
    assert SHORTS_REQUIRE_BTC_BEAR is False


# ─── Source-level: existing gate code paths now check the flags ──────


def test_short_gate_block_gated_by_flag_in_bot_engine():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    sg_idx = src.index("Short gate — 2026-04-24")
    block = src[sg_idx:sg_idx + 1500]
    assert "SHORT_GATE_ENABLED" in block, (
        "ShortGate block must check SHORT_GATE_ENABLED before firing")


def test_symbol_pause_gated_by_flag_in_risk_manager():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    # The per-symbol pause block must reference the flag
    sym_idx = src.index("Per-symbol pause")
    block = src[max(0, sym_idx - 1000):sym_idx + 1500]
    assert "SPEC12_SYMBOL_PAUSE_ENABLED" in block


def test_family_pause_gated_by_flag_in_risk_manager():
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    fam_idx = src.index("Per-family pause")
    block = src[max(0, fam_idx - 2000):fam_idx + 1500]
    assert "SPEC12_FAMILY_PAUSE_ENABLED" in block


def test_global_spec12_halt_still_present():
    """5-loss safety rail must NOT be removed — it's the catastrophic
    backstop that protects against runaway losses."""
    src = Path("core/risk_manager.py").read_text(encoding="utf-8")
    assert "5 global consec" in src
    assert "SPEC_GLOBAL_LOSSES_TO_REVIEW" in src


# ─── Behavioral: data-driven analysis layers UNCHANGED ───────────────


def test_phase23_calibrator_refuse_flag_gated():
    """2026-06-11 (owner: "Don't block any trades") SUPERSEDES the Phase-33
    carve-out that kept the calibrator hard-refuse: the refuse is now opt-in
    via RISK['calibrator_hard_refuse_enabled'] (default off); the Phase 18
    soft mult (0.7 floor) carries the calibrator information instead."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_calibrated < 0.30" in src
    assert "Phase 40 hard-refuse" in src
    assert "calibrator_hard_refuse_enabled" in src


def test_unblock_2026_06_11_edge_blocks_flag_gated():
    """All remaining edge-opinion hard blocks are opt-in (default OFF):
    EV catastrophic, regime counter-trend, dynamic post-mortem blacklist."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "ev_catastrophic_block_enabled" in src
    assert "regime_countertrend_block_enabled" in src
    assert "auto_mutator_block_enabled" in src


def test_phase27_graduated_ev_still_active():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER.get("enabled") is True


def test_phase29_post_sl_cooldown_still_active():
    """Post-SL cooldown is time-based protection, kept under UNBLOCK.
    2026-06-11 (owner-approved "ship it all"): 30 → 180 min and re-armed
    as a block after the Jun-11 tape (ADA/DOT/BNB/APT re-shorted 9-12x
    into 70 stop-losses while the cooldown was advisory-only)."""
    from core.risk_manager import RiskManager
    assert hasattr(RiskManager, "is_sl_cooldown_active")
    assert RiskManager.POST_SL_SHORT_COOLDOWN_MIN == 180
