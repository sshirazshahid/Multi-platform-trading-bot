"""Phase 13.1 — AGE_LOSS exit rule decision logic.

The rule fires inside core.order_manager monitor loop. This test exercises
the predicate logic without spinning up the full monitor — confirms the
ordering against AGE_LIMIT / STALE / no-action.

Truth table the predicate enforces:

  age_h >= max_age_h (6) AND net_pnl < 0           → AGE_LIMIT  (severe)
  age_h >= max_loss_age_h (3) AND net_pct <= -0.5% → AGE_LOSS   (intermediate)
  age_h >= max_stale_h (4) AND net_pct in [-0.3, +0.3] → STALE  (flat)
  otherwise                                          → no action

Edge cases covered: 3h boundary, 6h boundary, simultaneous AGE_LIMIT+
AGE_LOSS conditions (AGE_LIMIT wins), tiny losses just above -0.5%,
profits.
"""
from __future__ import annotations

import pytest


def _decide(age_hours: float, net_pct: float, *,
            max_age_h: float = 6.0,
            max_loss_age_h: float = 3.0,
            max_loss_age_pct: float = 0.5,
            max_stale_h: float = 4.0) -> str:
    """Pure replica of the order_manager rule chain — same ordering."""
    net_pnl_proxy = -1.0 if net_pct < 0 else 1.0
    if age_hours >= max_age_h and net_pnl_proxy < 0:
        return "AGE_LIMIT"
    if age_hours >= max_loss_age_h and net_pct <= -max_loss_age_pct:
        return "AGE_LOSS"
    if age_hours >= max_stale_h and -0.3 <= net_pct <= 0.3:
        return "STALE"
    return "HOLD"


# ── AGE_LOSS positive cases ────────────────────────────────────────────

def test_age_loss_fires_at_3h_minus_0_5_pct():
    assert _decide(3.0, -0.5) == "AGE_LOSS"


def test_age_loss_fires_at_3h_minus_1_pct():
    assert _decide(3.0, -1.0) == "AGE_LOSS"


def test_age_loss_fires_at_3_5h_minus_0_8_pct():
    assert _decide(3.5, -0.8) == "AGE_LOSS"


def test_age_loss_fires_just_past_3h():
    assert _decide(3.01, -0.51) == "AGE_LOSS"


# ── AGE_LOSS negative cases (must NOT fire) ────────────────────────────

def test_no_age_loss_just_under_3h_at_minus_2_pct():
    """Age below threshold even if loss is large — give the trade room."""
    assert _decide(2.99, -2.0) == "HOLD"


def test_no_age_loss_at_3h_minus_0_4_pct():
    """Loss too small — leave it; STALE will catch flat trades at 4h."""
    assert _decide(3.0, -0.4) == "HOLD"


def test_no_age_loss_when_in_profit():
    """Profitable trade should not be force-closed by AGE_LOSS regardless of age."""
    assert _decide(5.0, 0.5) == "HOLD"


# ── Rule ordering (AGE_LIMIT wins over AGE_LOSS) ───────────────────────

def test_age_limit_supersedes_age_loss_at_6h():
    """At 6h with -2% loss, BOTH AGE_LIMIT and AGE_LOSS conditions hold,
    but AGE_LIMIT is checked first (it's the more severe rule)."""
    assert _decide(6.5, -2.0) == "AGE_LIMIT"


def test_age_limit_at_6h_tiny_loss():
    """6h + tiny loss: AGE_LIMIT (which only requires net_pnl < 0)."""
    assert _decide(6.1, -0.05) == "AGE_LIMIT"


# ── Rule ordering (AGE_LOSS wins over STALE) ───────────────────────────

def test_age_loss_supersedes_stale_at_4h_minus_0_5_pct():
    """At 4h with -0.5% loss, AGE_LOSS fires first — STALE only catches
    near-zero PnL [-0.3, +0.3]."""
    assert _decide(4.0, -0.5) == "AGE_LOSS"


def test_stale_still_fires_at_4h_with_flat_pnl():
    """STALE retains its niche — flat trades at 4h."""
    assert _decide(4.0, -0.1) == "STALE"
    assert _decide(4.0, 0.2) == "STALE"


# ── Config plumbing ────────────────────────────────────────────────────

def test_config_carries_phase13_knobs():
    """RISK['max_loss_age_hours'] and RISK['max_loss_age_pct'] must exist
    and have the documented defaults so monitor loop can pick them up."""
    from config import RISK
    assert RISK["max_loss_age_hours"] == 3.0
    assert RISK["max_loss_age_pct"] == 0.5


def test_phase13_thresholds_consistent_with_other_age_gates():
    """max_loss_age_hours (3h) must sit between min entry hold (often
    no minimum) and max_stale_hours (4h) — otherwise AGE_LOSS either
    never fires (>= 4h would always be STALE first) or pre-empts other
    rules unintentionally."""
    from config import RISK
    assert RISK["max_loss_age_hours"] < RISK["max_stale_hours"]
    assert RISK["max_loss_age_hours"] < RISK["max_position_age_hours"]
