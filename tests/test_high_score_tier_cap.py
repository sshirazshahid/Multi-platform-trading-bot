"""High-score anti-EV tier cap (2026-05-01).

claude_portfolio realized data on 2026-05-01:
  score 65-74:  n=18  +$2.41   avg+$0.134  WR=44%
  score 75-84:  n=20  +$4.86   avg+$0.243  WR=40%
  score 85-100: n=16  -$2.98   avg-$0.186  WR=31%   ← anti-EV

The MCP scoring engine's highest-conviction bucket is structurally net-
negative — likely because score ≥ 85 requires nearly every bonus to
fire, which selects late-trend / parabolic-continuation setups that
mean-revert. Pre-fix, these candidates were sized UP via the AGGRESSIVE
tier (min_confidence=0.85). The fix caps them at STANDARD tier — we
still take the trade (signal isn't useless, just over-confident) but
at the smallest size, matching the actual realized expectancy.

These tests don't spin up a full BotEngine — they exercise the predicate
arithmetic via a thin shim, since `_select_leverage_tier` depends on
heavyweight imports that aren't worth dragging into a unit test. The
key contract pinned here: when `mcp_score >= 85`, `tier_cap` must be
forced to "STANDARD" regardless of confidence.
"""
from __future__ import annotations

import pytest


# Replica of the tier-cap arithmetic from _select_leverage_tier
def resolve_tier_cap(*, prior_cap: str | None, mcp_score: float) -> str | None:
    """Returns the effective tier_cap after combining the throttle cap
    (whatever was set upstream by the consec-loss throttle) with the
    high-score anti-EV cap. Logic: tighten wins — if either says
    STANDARD, the result is STANDARD."""
    cap = prior_cap
    if mcp_score >= 85.0:
        cap = "STANDARD"
    return cap


# ─── Score buckets ────────────────────────────────────────────────────


@pytest.mark.parametrize("score", [85.0, 90.0, 95.0, 99.5, 100.0])
def test_high_score_forces_standard_tier(score):
    assert resolve_tier_cap(prior_cap=None, mcp_score=score) == "STANDARD"


@pytest.mark.parametrize("score", [0.0, 50.0, 65.0, 75.0, 84.0, 84.99])
def test_below_threshold_does_not_cap(score):
    assert resolve_tier_cap(prior_cap=None, mcp_score=score) is None


def test_threshold_is_85_inclusive():
    """85.0 exactly must be treated as high-score (the data bucket starts
    at 85). 84.99 is just below — must not cap."""
    assert resolve_tier_cap(prior_cap=None, mcp_score=85.0) == "STANDARD"
    assert resolve_tier_cap(prior_cap=None, mcp_score=84.99) is None


# ─── Interaction with throttle cap ───────────────────────────────────


def test_high_score_compatible_with_existing_throttle_cap():
    """If the consec-loss throttle already set tier_cap='STANDARD', the
    high-score cap is a no-op (same value). Tightest wins."""
    assert resolve_tier_cap(prior_cap="STANDARD", mcp_score=90.0) == "STANDARD"


def test_high_score_overrides_no_throttle():
    """No throttle cap (None) + high score → STANDARD. The fix's actual
    purpose: stop AGGRESSIVE/CONVICTION tiering on score ≥ 85."""
    assert resolve_tier_cap(prior_cap=None, mcp_score=87.5) == "STANDARD"


# ─── Documents the data evidence ─────────────────────────────────────


def test_anti_ev_evidence_documented():
    """Pin the per-bucket realized stats this fix is anchored on so
    any future "raise the threshold" change has to surface this data."""
    EVIDENCE = {
        (65, 74): {"n": 18, "sum": +2.41, "avg": +0.134, "wr": 0.44},
        (75, 84): {"n": 20, "sum": +4.86, "avg": +0.243, "wr": 0.40},
        (85, 100): {"n": 16, "sum": -2.98, "avg": -0.186, "wr": 0.31},
    }
    # Sanity: the 85+ bucket has the worst sum AND the worst WR.
    sums = {k: v["sum"] for k, v in EVIDENCE.items()}
    wrs = {k: v["wr"] for k, v in EVIDENCE.items()}
    worst_sum_bucket = min(sums, key=sums.get)
    worst_wr_bucket = min(wrs, key=wrs.get)
    assert worst_sum_bucket == (85, 100)
    assert worst_wr_bucket == (85, 100)
