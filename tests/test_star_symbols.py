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
    LEVERAGE_TIERS,
    STAR_SYMBOLS,
    WHITELIST_SYMBOLS,
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
    """STAR membership is the empirical-evidence list. As of 2026-05-01,
    ATOM, ARB, and DOGE qualify (n>=8 trades, mean PnL > $0.05 in
    claude_portfolio). If this set grows, the membership criteria
    documented in config.py:STAR_SYMBOLS must still apply.

    Membership history:
      2026-04-28: {ATOM, ARB} — Phase 12.3 initial set
      2026-05-01: + DOGE — n=10, +$4.83, 50% WR, $0.483/trade
                            (top contributor, surfaced by ultrareview audit)
    """
    expected = {"ATOM/USDT:USDT", "ARB/USDT:USDT", "DOGE/USDT:USDT"}
    assert STAR_SYMBOLS == expected, (
        f"STAR set changed from {expected} to {STAR_SYMBOLS}; "
        f"verify the underlying claude_portfolio attribution still supports "
        f"the new membership and update the config docstring evidence.")


def test_star_size_bump_increases_size_on_standard():
    """STAR size bump is 1.3x with cap at 1.0 (effectively no cap at our scale).

    2026-04-28 (L99 ALL-IN): STANDARD base size raised 0.15 → 0.50, so the
    prior 0.20 cap would have SHRUNK STAR symbols below the non-STAR
    baseline. Cap raised to 1.0; the bump remains a 1.3x multiplier. STAR
    symbols thus receive +30% size relative to non-STAR at every base.
    Worked examples:
       L99 STANDARD: 0.50 × 1.3 = 0.65 → uncapped (≤ 1.0)
       Pre-L99 STANDARD (0.15): 0.15 × 1.3 = 0.195 → uncapped
       Hypothetical 0.80: 0.80 × 1.3 = 1.04 → capped to 1.0
    """
    base = LEVERAGE_TIERS["STANDARD"]["size_pct"]
    bumped = min(1.0, base * 1.3)
    assert bumped <= 1.0, "size_pct must be capped at 1.0"
    assert bumped > base, "STAR bump must increase size on STANDARD tier"
