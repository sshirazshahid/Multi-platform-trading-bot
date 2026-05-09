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


def test_blacklist_hard_evidence_based():
    """Phase 39 (2026-05-09): BLACKLIST_HARD re-enabled from 421-trade data."""
    from config import BLACKLIST_HARD
    expected = {"SOL/USDT:USDT", "XRP/USDT:USDT", "APT/USDT:USDT",
                "ETH/USDT:USDT", "DOGE/USDT:USDT", "BTC/USDT:USDT"}
    assert expected == BLACKLIST_HARD, f"BLACKLIST_HARD mismatch: got {BLACKLIST_HARD}"


def test_allowed_hours_evidence_based():
    """Phase 39: 7 catastrophic loss hours blocked from 421-trade data."""
    from config import ALLOWED_HOURS_UTC, BLOCKED_HOURS_UTC
    assert len(ALLOWED_HOURS_UTC) == 19  # Phase 44: H05+H22 unblocked
    assert len(BLOCKED_HOURS_UTC) == 5  # Phase 44: H05+H22 unblocked


def test_blocked_hours_are_catastrophic_losers():
    """Phase 39: blocked hours have combined -$107+ loss all-time."""
    from config import BLOCKED_HOURS_UTC
    catastrophic = {0, 9, 19, 21, 23}  # Phase 44: H05/H22 unblocked
    assert catastrophic == BLOCKED_HOURS_UTC, f"BLOCKED_HOURS_UTC mismatch: got {BLOCKED_HOURS_UTC}"


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


def test_phase23_calibrator_refuse_still_active():
    """Phase 40 hard-refuse <40% calibrator stays — that IS the analysis."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "_calibrated < 0.30" in src
    assert "Phase 40 hard-refuse" in src


def test_phase27_graduated_ev_still_active():
    from config import EXPECTANCY_FILTER
    assert EXPECTANCY_FILTER.get("enabled") is True


def test_phase29_post_sl_cooldown_still_active():
    """Post-SL cooldown is protection, not a block. Stays."""
    from core.risk_manager import RiskManager
    assert hasattr(RiskManager, "is_sl_cooldown_active")
    assert RiskManager.POST_SL_SHORT_COOLDOWN_MIN == 30
