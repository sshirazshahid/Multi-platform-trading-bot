"""Phase 10.3 — hour-gating invariants.

Locks the partition between ALLOWED_HOURS_UTC and BLOCKED_HOURS_UTC and
enforces that the data-driven winners stay allowed and catastrophic
losers stay blocked. If you're updating these sets, run
`python scripts/diagnostic_report.py --since <30d-ago>` first and
update the membership tests with the latest evidence.
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


def test_phase10_attributed_winners_allowed():
    """Hours with sum PnL > +$1.00 over the 30-day refresh must stay allowed.

    Evidence from data/warehouse.sqlite as of 2026-04-27:
      H02 +$1.50  H03 +$1.45  H13 +$1.18  H14 +$5.58
      H15 +$6.25  H16 +$3.61  H21 +$3.16
    """
    attributed_winners = {2, 3, 13, 14, 15, 16, 21}
    assert attributed_winners.issubset(ALLOWED_HOURS_UTC), (
        f"removed winning hours: {attributed_winners - ALLOWED_HOURS_UTC}")


def test_phase10_catastrophic_losers_blocked():
    """Hours with sum PnL < -$5 over the 30-day refresh must stay blocked.

    Evidence:
      H00 -$20.04  H01 -$8.92  H04 -$11.21  H17 -$26.08  H19 -$7.83
    """
    catastrophic = {0, 1, 4, 17, 19}
    assert catastrophic.issubset(BLOCKED_HOURS_UTC), (
        f"unblocked catastrophic hours: {catastrophic - BLOCKED_HOURS_UTC}")


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
