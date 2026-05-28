"""454-trade hardening (2026-05-27) — H17 and H00 blocked.

Evidence from 454 real trades:
  H17: 30 trades, 27% WR, -$34.94 PnL  (worst hour by far)
  H00: 35 trades, 26% WR, -$20.76 PnL  (second worst)

Best hours kept allowed:
  H03: 57% WR, +$0.97
  H13: 63% WR, +$1.49
  H15: 59% WR, +$5.19
  H21: 45% WR, +$3.08
"""
from __future__ import annotations

from config import (
    ALLOWED_HOURS_UTC,
    BLOCKED_HOURS_UTC,
    PEAK_HOURS_UTC,
    WARMUP_HOURS_UTC,
)


def test_h17_is_blocked():
    """H17 catastrophic: 30 trades, 27% WR, -$34.94. Must be blocked."""
    assert 17 in BLOCKED_HOURS_UTC, "H17 must be in BLOCKED_HOURS_UTC (-$34.94, 27% WR)"
    assert 17 not in ALLOWED_HOURS_UTC, "H17 must not be in ALLOWED_HOURS_UTC"


def test_h00_is_blocked():
    """H00 catastrophic: 35 trades, 26% WR, -$20.76. Must be blocked."""
    assert 0 in BLOCKED_HOURS_UTC, "H00 must be in BLOCKED_HOURS_UTC (-$20.76, 26% WR)"
    assert 0 not in ALLOWED_HOURS_UTC, "H00 must not be in ALLOWED_HOURS_UTC"


def test_allowed_and_blocked_still_partition_24h():
    """ALLOWED ∪ BLOCKED must cover all 24 hours with no overlap."""
    union = ALLOWED_HOURS_UTC | BLOCKED_HOURS_UTC
    assert union == set(range(24)), f"missing hours: {set(range(24)) - union}"
    assert not (ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC), (
        f"overlap: {ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC}"
    )


def test_best_hours_remain_allowed():
    """Best-performing hours from 454-trade data must stay allowed."""
    best = {3, 13, 15, 21}
    blocked_best = best & BLOCKED_HOURS_UTC
    assert not blocked_best, f"Profitable hours were accidentally blocked: {blocked_best}"
    assert best.issubset(ALLOWED_HOURS_UTC), (
        f"Best hours not in ALLOWED: {best - ALLOWED_HOURS_UTC}"
    )


def test_h17_removed_from_warmup():
    """H17 was previously a warmup hour. It must not appear in WARMUP after blocking."""
    assert 17 not in WARMUP_HOURS_UTC, (
        "H17 is now blocked but still in WARMUP_HOURS_UTC — update the set"
    )


def test_warmup_is_subset_of_allowed():
    """All warmup hours must be allowed hours (invariant)."""
    not_allowed = WARMUP_HOURS_UTC - ALLOWED_HOURS_UTC
    assert not not_allowed, f"WARMUP hours not in ALLOWED: {not_allowed}"


def test_peak_is_subset_of_allowed():
    """All peak hours must be allowed hours (invariant)."""
    not_allowed = PEAK_HOURS_UTC - ALLOWED_HOURS_UTC
    assert not not_allowed, f"PEAK hours not in ALLOWED: {not_allowed}"
