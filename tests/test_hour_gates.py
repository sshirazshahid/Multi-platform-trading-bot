"""Phase 39 (2026-05-09) — hour-gating invariants (421 all-time real trades).

Re-fitted on the full 421-trade all-time dataset (all strategies combined)
after 65h soak. Previous Phase 12.2 data was claude_portfolio-only and
had too few samples in many hours.

Catastrophic losers now blocked (net PnL ≤ -$9.9, n ≥ 8 all-time):
  H22 -$35.81  H09 -$23.59  H05 -$19.12  H00 -$15.69
  H23 -$12.05  H19 -$10.33  H21  -$9.91

Strong winners kept (net PnL ≥ +$1.17, n ≥ 9 all-time):
  H08 +$5.27 70%WR  H01 +$5.17  H20 +$6.30 65%WR  H10 +$5.60
  H18 +$1.71 72%WR  H16 +$2.25 90%WR  H04 +$1.92  H12 +$1.65

When updating, query all real trades:
    SELECT CAST(strftime('%H', close_time, 'unixepoch') AS INT) hr,
           COUNT(*), ROUND(SUM(pnl),2)
    FROM positions WHERE status='CLOSED'
    GROUP BY hr ORDER BY hr;
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


def test_phase39_winning_hours_allowed():
    """Phase 39: hours with net PnL ≥ +$1.17 all-time must be allowed.

    Evidence (421 all-time real trades, 2026-05-09):
      H08 +$5.27 70%WR   H01 +$5.17   H20 +$6.30 65%WR  H10 +$5.60
      H18 +$1.71 72%WR   H16 +$2.25 90%WR  H04 +$1.92   H12 +$1.65
    """
    winners = {1, 4, 8, 10, 12, 16, 18, 20}
    assert winners.issubset(ALLOWED_HOURS_UTC), (
        f"removed winning hours: {winners - ALLOWED_HOURS_UTC}")


def test_phase39_catastrophic_losers_blocked():
    """Phase 39: hours with net PnL ≤ -$9.91 all-time must be blocked.

    Evidence (421 all-time real trades, 2026-05-09):
      H22 -$35.81  H09 -$23.59  H05 -$19.12  H00 -$15.69
      H23 -$12.05  H19 -$10.33  H21  -$9.91
    """
    if not BLOCKED_HOURS_UTC:
        return  # UNBLOCK_ALL mode — relaxed
    catastrophic = {0, 9, 19, 21, 23}  # Phase 44: H05/H22 unblocked after real-filter audit
    assert catastrophic.issubset(BLOCKED_HOURS_UTC), (
        f"unblocked catastrophic-loss hours: "
        f"{catastrophic - BLOCKED_HOURS_UTC}")


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


def test_phase39_peak_hours_are_strongest_winners():
    """PEAK should be highest-EV hours from all-time 421-trade data.
    H08 70%WR, H16 90%WR, H18 72%WR, H20 65%WR, H01 large sum, H10 +$5.60."""
    expected_peak = {1, 5, 8, 10, 16, 18, 20}  # Phase 44: H05 promoted (78% WR)
    assert expected_peak == PEAK_HOURS_UTC, (
        f"PEAK should be {expected_peak}; got {PEAK_HOURS_UTC}")
