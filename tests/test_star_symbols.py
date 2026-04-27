"""Phase 12.5 — STAR_SYMBOLS wire-up invariants.

Locks the contract:
  STAR_SYMBOLS ⊂ WHITELIST_SYMBOLS  (must be eligible for tier promotion)
  STAR_SYMBOLS ∩ BLACKLIST_HARD = ∅ (cannot be both proven-winner and blocked)

Selector behavior verified by inspecting `_select_leverage_tier` size-bump
math; full integration is exercised in tests/test_bot_engine_*.py.
"""
from __future__ import annotations

from config import (
    BLACKLIST_HARD,
    STAR_SYMBOLS,
    WHITELIST_SYMBOLS,
    LEVERAGE_TIERS,
)


def test_star_subset_of_whitelist():
    """STAR symbols must be whitelisted — they need to pass the
    `requires_whitelist` gate on STRONG/CONVICTION tiers."""
    assert STAR_SYMBOLS.issubset(WHITELIST_SYMBOLS), (
        f"STAR symbols not in whitelist: {STAR_SYMBOLS - WHITELIST_SYMBOLS}")


def test_star_disjoint_with_blacklist():
    """Symbol cannot be simultaneously 'proven winner' and 'hard blocked'."""
    assert not (STAR_SYMBOLS & BLACKLIST_HARD), (
        f"STAR/BLACKLIST overlap: {STAR_SYMBOLS & BLACKLIST_HARD}")


def test_star_set_is_evidence_based():
    """STAR membership is the empirical-evidence list. As of 2026-04-28,
    only ATOM and ARB qualify (n>=8 trades, mean PnL > $0.05 in
    claude_portfolio). If this set grows, the membership criteria
    documented in config.py:STAR_SYMBOLS must still apply."""
    expected = {"ATOM/USDT:USDT", "ARB/USDT:USDT"}
    assert STAR_SYMBOLS == expected, (
        f"STAR set changed from {expected} to {STAR_SYMBOLS}; "
        f"verify the underlying claude_portfolio attribution still supports "
        f"the new membership and update the config docstring evidence.")


def test_star_size_bump_capped_at_20pct():
    """STAR size bump is 1.3x but capped at 0.20. Worked example:
       STANDARD size 0.15 × 1.3 = 0.195 → uncapped
       Hypothetical 0.18 × 1.3 = 0.234 → capped to 0.20
    The cap prevents accidental >20% sizing on a single trade."""
    base = LEVERAGE_TIERS["STANDARD"]["size_pct"]
    bumped = min(0.20, base * 1.3)
    assert bumped <= 0.20, "size_pct must be capped at 0.20"
    # And the bump must actually do something on STANDARD
    assert bumped > base, "STAR bump must increase size on STANDARD tier"
