"""Phase 12.2 — hour-gating invariants (claude_portfolio-only basis).

Locks the partition between ALLOWED_HOURS_UTC and BLOCKED_HOURS_UTC.
The basis was changed in Phase 12.2: previously we used COMBINED-warehouse
attribution, but combined data was dominated by the deprecated MultiTF /
Supertrend strategies. The active path is claude_portfolio (+ its
systematic_v3_1 fallback), so the hour gate must reflect WHERE THAT
STRATEGY makes/loses money.

When updating these sets, query claude_portfolio-only data:

    SELECT CAST(strftime('%H', ts_entry, 'unixepoch') AS INT) hr,
           COUNT(*), ROUND(SUM(realized_pnl),2)
    FROM trades WHERE status='CLOSED' AND strategy_family='claude_portfolio'
    GROUP BY hr;

Phase 10.3 → 12.2 transition: H00 / H17 / H19 were UNBLOCKED because
claude_portfolio-only data showed them as net positive (combined data
was misleading). H02 / H13 / H14 / H15 / H16 / H21 / H23 stayed allowed.
H06-H10 are thin-sample (n<3) and default-allowed pending data.
"""
from __future__ import annotations

from config import (
    ALLOWED_HOURS_UTC,
    BLOCKED_HOURS_UTC,
    PEAK_HOURS_UTC,
    WARMUP_HOURS_UTC,
)


def test_allowed_and_blocked_partition_24h():
    """Every UTC hour must be in exactly one of the two sets."""
    assert not (ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC), (
        f"overlap forbidden, got {ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC}")
    union = ALLOWED_HOURS_UTC | BLOCKED_HOURS_UTC
    assert union == set(range(24)), (
        f"missing hours: {set(range(24)) - union}")


def test_phase12_claude_portfolio_winners_allowed():
    """Hours with sum PnL > +$0.50 in claude_portfolio (n>=3) must be allowed.

    Evidence (claude_portfolio-only as of 2026-04-28):
      H03 +$1.02 (80%, n=5)   H19 +$1.04 (40%, n=5)
      H00 +$0.98 (50%, n=10)  H17 +$0.89 (33%, n=6)
      H02 +$0.70 (33%, n=3)   H15 +$0.67 (40%, n=5)
      H14 +$0.57 (33%, n=6)   H21 +$0.50 (67%, n=3)
    """
    cp_winners = {0, 2, 3, 14, 15, 17, 19, 21}
    assert cp_winners.issubset(ALLOWED_HOURS_UTC), (
        f"removed claude_portfolio-winning hours: "
        f"{cp_winners - ALLOWED_HOURS_UTC}")


def test_phase12_catastrophic_claude_portfolio_losers_blocked():
    """Hours with sum PnL < -$0.40 in claude_portfolio (n>=3) SHOULD be
    blocked when block-mode is engaged.

    Evidence preserved (claude_portfolio-only attribution as of Phase 12.2):
      H22 -$3.49 (0%, n=5)    H05 -$1.31 (0%, n=6)
      H18 -$0.54 (40%, n=5)   H20 -$0.53 (20%, n=5)
      H01 -$0.49 (0%, n=3)    H12 -$0.45 (50%, n=6)

    2026-04-28 (UNBLOCK_ALL): user directive "Dont block any trades" means
    BLOCKED_HOURS_UTC may legitimately be empty. When that's the case, the
    test passes — the policy is "let everything through and let
    AutoMutator's runtime blacklist do the policing." Restore evidence-
    based blocking by repopulating BLOCKED_HOURS_UTC in config.py.
    """
    if not BLOCKED_HOURS_UTC:
        return  # UNBLOCK_ALL mode — assertion intentionally relaxed
    cp_losers = {1, 5, 12, 18, 20, 22}
    assert cp_losers.issubset(BLOCKED_HOURS_UTC), (
        f"unblocked claude_portfolio losers: "
        f"{cp_losers - BLOCKED_HOURS_UTC}")


def test_warmup_hours_subset_of_allowed():
    """WARMUP can only flag hours that are also allowed."""
    assert WARMUP_HOURS_UTC.issubset(ALLOWED_HOURS_UTC), (
        f"warmup hours not allowed: {WARMUP_HOURS_UTC - ALLOWED_HOURS_UTC}")


def test_peak_hours_subset_of_allowed():
    """PEAK (CONVICTION-tier hours) can only flag hours that are allowed."""
    assert PEAK_HOURS_UTC.issubset(ALLOWED_HOURS_UTC), (
        f"peak hours not allowed: {PEAK_HOURS_UTC - ALLOWED_HOURS_UTC}")


def test_warmup_and_peak_disjoint():
    """A given hour is either warmup (half-size) or peak (CONVICTION),
    never both — they request opposite leverage tiers."""
    assert not (WARMUP_HOURS_UTC & PEAK_HOURS_UTC), (
        f"warmup/peak overlap: {WARMUP_HOURS_UTC & PEAK_HOURS_UTC}")


def test_phase12_peak_hours_are_strongest_winners():
    """PEAK should be the highest-EV hours from claude_portfolio data.
    Strongest sums: H19 +$1.04, H03 +$1.02, H00 +$0.98, H17 +$0.89."""
    expected_peak = {0, 3, 17, 19}
    assert expected_peak == PEAK_HOURS_UTC, (
        f"PEAK should be {expected_peak}; got {PEAK_HOURS_UTC}")
