"""Owner-approved removal of anti-predictive amplifiers (2026-06-06).

The MCP score is anti-predictive (r=-0.285; score>=85 = worst cohort), yet confidence rose with
score and unlocked higher leverage tiers (phase51: score 85+ -> AGGRESSIVE 10x). Owner approved
capping that escalation (leverage <= STANDARD regardless of confidence) and reverting the
unvalidated VWAP +15 bonus to +5. Neither creates edge — both cut loss amplification on a NO_EDGE
book. Reversible via config.CONFIDENCE_LEVERAGE_ESCALATION.
"""
from __future__ import annotations

from tests.mcp_brain_source import mcp_brain_source_for_grep

import re

from config import CONFIDENCE_LEVERAGE_ESCALATION
from core.bot_engine import _tier_blocked_by_cap


def test_confidence_leverage_escalation_defaults_off():
    assert CONFIDENCE_LEVERAGE_ESCALATION is False


def test_escalation_off_blocks_tiers_above_standard():
    # With escalation OFF, confidence/score cannot reach leverage tiers above STANDARD.
    for tier in ("AGGRESSIVE", "CONVICTION", "STRONG"):
        assert _tier_blocked_by_cap(tier, tier_cap=None, escalation_enabled=False) is True
    for tier in ("STANDARD", "SCALP"):
        assert _tier_blocked_by_cap(tier, tier_cap=None, escalation_enabled=False) is False


def test_escalation_on_allows_high_tiers():
    assert _tier_blocked_by_cap("AGGRESSIVE", tier_cap=None, escalation_enabled=True) is False
    assert _tier_blocked_by_cap("CONVICTION", tier_cap=None, escalation_enabled=True) is False


def test_consec_loss_throttle_still_caps_with_escalation_on():
    # The existing consec-loss STANDARD throttle must keep working independently of the flag.
    assert _tier_blocked_by_cap("AGGRESSIVE", tier_cap="STANDARD", escalation_enabled=True) is True
    assert _tier_blocked_by_cap("STANDARD", tier_cap="STANDARD", escalation_enabled=True) is False


def test_vwap_bonus_reverted_to_5():
    src = mcp_brain_source_for_grep()
    m = re.search(r"B7: VWAP[\s\S]+?SMART MONEY", src)
    assert m, "B7 VWAP block not found"
    block = m.group(0)
    assert "score += 15" not in block, "VWAP bonus still +15 — must revert to +5"
    assert "score += 5" in block, "VWAP bonus should award +5"
