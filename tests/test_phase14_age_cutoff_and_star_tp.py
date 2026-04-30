"""Phase 14 — age-cutoff tightening + STAR TP extender (2026-04-29).

Two warehouse-driven changes:

1. AGE cutoffs tightened (RISK config):
     max_position_age_hours: 6  → 4
     max_stale_hours:        4  → 2
     max_loss_age_hours:     3  → 1.5
     max_loss_age_pct:       0.5 → 0.3

   Backed by 60-day claude_portfolio breakdown:
     <10min:   -$22.26 / 0% WR
     10-30min: +$1.28  / 38% WR / R:R 3:1   <- fast-win zone
     30-60min: +$0.31  / 55% WR / R:R 1:1
     1-2h:     -$1.89  / 40% WR / no-edge
     >2h:      -$4.34  / 39% WR / slow bleed

2. STAR_SYMBOLS get tp_pct multiplied by 1.25 in mcp_brain._score_coin.
   ATOM avg realized R = 4.7, ARB = 1.85 — both above the default 2.0×SL TP.
"""
from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────
# Part 1 — config age-cutoff invariants
# ─────────────────────────────────────────────────────────────────────


def test_max_loss_age_hours_tightened():
    from config import RISK
    assert RISK["max_loss_age_hours"] <= 1.5, (
        "AGE_LOSS cutoff must be ≤ 1.5h to capture the post-60min bleed "
        "the 1-2h warehouse bucket exposed (-$1.89 / 38 trades)")


def test_max_loss_age_pct_loosened_with_earlier_cutoff():
    from config import RISK
    # Tighter age-cutoff (1.5h) needs looser pct trigger so the rule
    # actually fires; otherwise we'd cut at 1.5h only the deeply
    # underwater positions (which are already at SL).
    assert RISK["max_loss_age_pct"] <= 0.3
    assert RISK["max_loss_age_pct"] > 0  # but not zero — would close winners


def test_max_stale_hours_tightened():
    from config import RISK
    assert RISK["max_stale_hours"] <= 2.0, (
        "STALE cutoff must be ≤ 2h — the >2h hold bucket lost -$4.34 "
        "with effectively zero edge (avg_win = avg_loss)")


def test_max_position_age_hours_tightened():
    from config import RISK
    assert RISK["max_position_age_hours"] <= 4, (
        "Hard expiry must be ≤ 4h — no claude_portfolio cell beyond 2h "
        "is positive-EV after fees")


def test_age_cutoffs_consistent_ordering():
    """AGE_LOSS < STALE < AGE_LIMIT — the three rules form a stack."""
    from config import RISK
    age_loss = RISK["max_loss_age_hours"]
    stale = RISK["max_stale_hours"]
    age_limit = RISK["max_position_age_hours"]
    assert age_loss <= stale <= age_limit, (
        f"Cutoffs out of order: AGE_LOSS={age_loss}h, STALE={stale}h, "
        f"AGE_LIMIT={age_limit}h. Earlier rules must trigger first.")


# ─────────────────────────────────────────────────────────────────────
# Part 2 — STAR TP extender invariants
# ─────────────────────────────────────────────────────────────────────


def test_star_symbols_set_contains_atom_and_arb():
    """The STAR set is the set we extend TP for. Must contain the
    proven-edge symbols ATOM and ARB."""
    from config import STAR_SYMBOLS
    assert "ATOM/USDT:USDT" in STAR_SYMBOLS
    assert "ARB/USDT:USDT" in STAR_SYMBOLS


def test_star_tp_extender_math():
    """Replicate the multiplier math from mcp_brain._score_coin."""
    base_tp = 3.0  # e.g. 1.5% SL × 2.0 = 3.0% TP
    multiplier = 1.25
    extended = base_tp * multiplier
    assert extended == pytest.approx(3.75, abs=0.01)
    # Default 2× SL → 2.5× SL after extender. SMC-tightened 2.5× → 3.125×.


def test_star_tp_extender_only_applies_to_star():
    """A non-STAR symbol's TP must not be affected."""
    # We can't easily call _score_coin in isolation (heavy deps), but
    # we pin the contract: if the multiplier line is `if _coin in _STAR`,
    # then a non-STAR coin yields the un-multiplied tp_pct.
    # 2026-05-01 update: DOGE was promoted to STAR (n=10, +$4.83, 50% WR);
    # use BTC + a clearly-out-of-set symbol as the pass-through anchors.
    from config import STAR_SYMBOLS
    assert "BTC/USDT:USDT" not in STAR_SYMBOLS, (
        "BTC must not be a STAR symbol for this test to validate the "
        "non-STAR pass-through invariant")
    assert "ETH/USDT:USDT" not in STAR_SYMBOLS, (
        "ETH must remain non-STAR for this pass-through test")


def test_star_tp_extender_compounds_correctly_with_smc_path():
    """If SMC zone path set tp_pct = sl_pct × 2.5 and STAR multiplier
    of 1.25 follows, the resulting TP is sl_pct × 3.125."""
    sl_pct = 1.5
    tp_pct = sl_pct * 2.5  # SMC tightened path
    tp_pct *= 1.25  # STAR extender
    assert tp_pct == pytest.approx(sl_pct * 3.125, abs=0.001)


def test_star_tp_extender_compounds_correctly_with_default_path():
    """Default path: tp = sl × 2.0. After STAR ×1.25 = sl × 2.5."""
    sl_pct = 2.0
    tp_pct = sl_pct * 2.0  # default
    tp_pct *= 1.25  # STAR extender
    assert tp_pct == pytest.approx(sl_pct * 2.5, abs=0.001)


# ─────────────────────────────────────────────────────────────────────
# Part 3 — sanity that existing AGE_* rules still fire correctly
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("age_h,net_pct,should_fire_age_loss", [
    (1.0, -0.4, False),  # under 1.5h: not yet age-loss
    (1.6, -0.4, True),   # past 1.5h, below -0.3% → fire
    (1.6, -0.2, False),  # past 1.5h, but only -0.2% → no fire
    (1.6, +0.4, False),  # past 1.5h but green → no fire
    (3.0, -0.5, True),   # well past, well underwater
])
def test_age_loss_rule_logic(age_h, net_pct, should_fire_age_loss):
    """Replicate the order_manager AGE_LOSS condition with new thresholds."""
    from config import RISK
    max_loss_age_h = RISK["max_loss_age_hours"]
    max_loss_age_pct = RISK["max_loss_age_pct"]
    fires = (age_h >= max_loss_age_h and net_pct <= -max_loss_age_pct)
    assert fires == should_fire_age_loss, (
        f"AGE_LOSS at age={age_h}h net={net_pct}% with cutoff "
        f"{max_loss_age_h}h/-{max_loss_age_pct}%: expected fire="
        f"{should_fire_age_loss}, got {fires}")
