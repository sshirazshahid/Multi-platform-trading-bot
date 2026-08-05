"""Ops invariants for F1 carry — pin thresholds; never open on fresh+−EV."""
from __future__ import annotations

from research import funding_carry_lab as fc

# Must match research/funding_carry_lab.py — silent loosens are forbidden.
_PINNED_MIN_EDGE_BPS = 15.0
_PINNED_COST_MULT = 3.0

_GOOD = dict(
    funding_per_settlement=0.0004,
    hold_settlements=9,
    round_trip_cost_frac=0.0002,
    depth_ratio=30.0,
    liq_buffer_x=5.0,
    funding_age_sec=30.0,
    both_legs_fillable=True,
    trailing_funding_rates=[0.0004] * 21,
    perp_mark=100.1,
    spot_mid=100.0,
    spot_spread_bps=1.0,
    perp_spread_bps=1.0,
    time_to_next_funding_min=60.0,
    feeds_fresh=True,
)


def test_f1_min_edge_bps_pinned():
    assert fc.F1_MIN_EDGE_BPS == _PINNED_MIN_EDGE_BPS


def test_f1_cost_mult_pinned():
    assert fc.F1_COST_MULT == _PINNED_COST_MULT


def test_fresh_feeds_negative_edge_does_not_open():
    """Live 2026-08-04 pattern: feeds_fresh + strongly negative net edge → no open."""
    ok, reason, det = fc.f1_entry_gate(
        **{
            **_GOOD,
            # Tiny positive print but trailing mean / projection collapses edge
            "funding_per_settlement": 0.00001,
            "trailing_funding_rates": [0.00001] * 21,
            "round_trip_cost_frac": 0.004,  # ~40 bps RT → threshold >> edge
            "feeds_fresh": True,
        }
    )
    assert ok is False
    assert det.get("net_edge_bps", 0) < 0 or "edge" in (reason or "").lower() or "lower_bound" in (
        reason or ""
    ).lower()


def test_fresh_feeds_perp_below_spot_does_not_open():
    ok, reason, _ = fc.f1_entry_gate(
        **{**_GOOD, "perp_mark": 99.5, "spot_mid": 100.0, "feeds_fresh": True}
    )
    assert ok is False
    assert "perp_mark" in reason


def test_passing_fixture_still_opens():
    assert fc.f1_entry_gate(**_GOOD)[0] is True
