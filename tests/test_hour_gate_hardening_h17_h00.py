"""Hour gate tests (2026-05-27).

454-trade evidence: H17 (27% WR, -$34.94) and H00 (26% WR, -$20.76)
are catastrophic — noted for sizing. UNBLOCK directive: all 24 hours
allowed, no hard blocks. PEAK/WARMUP retained as sizing hints.
"""
from __future__ import annotations

from config import (
    ALLOWED_HOURS_UTC,
    BLOCKED_HOURS_UTC,
    PEAK_HOURS_UTC,
    WARMUP_HOURS_UTC,
)


def test_all_24_hours_allowed():
    """UNBLOCK directive: no hours blocked if analysis is correct."""
    assert BLOCKED_HOURS_UTC == set(), f"Expected empty blocked set; got {BLOCKED_HOURS_UTC}"
    assert ALLOWED_HOURS_UTC == set(range(24))


def test_allowed_and_blocked_still_partition_24h():
    """ALLOWED ∪ BLOCKED must cover all 24 hours with no overlap."""
    union = ALLOWED_HOURS_UTC | BLOCKED_HOURS_UTC
    assert union == set(range(24)), f"missing hours: {set(range(24)) - union}"
    assert not (ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC), (
        f"overlap: {ALLOWED_HOURS_UTC & BLOCKED_HOURS_UTC}"
    )


def test_warmup_is_subset_of_allowed():
    """All warmup hours must be allowed hours (invariant)."""
    not_allowed = WARMUP_HOURS_UTC - ALLOWED_HOURS_UTC
    assert not not_allowed, f"WARMUP hours not in ALLOWED: {not_allowed}"


def test_peak_is_subset_of_allowed():
    """All peak hours must be allowed hours (invariant)."""
    not_allowed = PEAK_HOURS_UTC - ALLOWED_HOURS_UTC
    assert not not_allowed, f"PEAK hours not in ALLOWED: {not_allowed}"
