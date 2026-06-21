"""Audit 2026-06-21 B5: _effective_tp_threshold — the mcp_take_profit near-target
gate. The critical property is UNIT correctness: net_pnl_pct is LEVERAGED, the
planned-TP distance is unleveraged price %, so the effective threshold must scale
by leverage. The leverage cases below FAIL if the *lev factor is dropped.
"""
from __future__ import annotations

import pytest

from core.bot_engine import _effective_tp_threshold


# ── Flag OFF: byte-identical to the old fixed threshold ──────────────────────
def test_flag_off_returns_base_nonstar():
    assert _effective_tp_threshold(110.0, 100.0, "buy", 5, 0.5, False) == 0.5


def test_flag_off_returns_base_star():
    assert _effective_tp_threshold(110.0, 100.0, "buy", 5, 1.5, False) == 1.5


# ── Flag ON: leverage-reconciled near-target threshold ───────────────────────
def test_leverage_scaling_buy():
    # tp_dist = 10% price, lev 5, K 0.8 -> 0.8*10*5 = 40.0 (leveraged-%).
    # A units-naive impl that drops *lev would return 8.0.
    out = _effective_tp_threshold(110.0, 100.0, "buy", 5, 0.5, True, 0.8)
    assert out == pytest.approx(40.0)


def test_spot_leverage_one():
    out = _effective_tp_threshold(110.0, 100.0, "buy", 1, 0.5, True, 0.8)
    assert out == pytest.approx(8.0)


def test_short_side_scaling():
    # sell: tp below entry. tp_dist = 10%, lev 3 -> 0.8*10*3 = 24.0
    out = _effective_tp_threshold(90.0, 100.0, "sell", 3, 0.5, True, 0.8)
    assert out == pytest.approx(24.0)


def test_concrete_3x_gate_boundary():
    # entry 100, tp 102 -> 2% price, lev 3, K 0.8 -> 0.8*2*3 = 4.8 (leveraged-%).
    # At +2% price (= +6% leveraged net) the gate fires; at +1% (+3%) it does not.
    thr = _effective_tp_threshold(102.0, 100.0, "buy", 3, 0.5, True, 0.8)
    assert thr == pytest.approx(4.8)
    assert (6.0 - 0.07) >= thr      # +2% price, ~80% to TP -> fires
    assert not ((3.0 - 0.07) >= thr)  # +1% price -> does NOT fire


# ── Fallbacks: a missing/wrong TP can never make the gate fire trivially ─────
def test_missing_tp_falls_back_to_base():
    assert _effective_tp_threshold(0.0, 100.0, "buy", 5, 0.5, True, 0.8) == 0.5
    assert _effective_tp_threshold(None, 100.0, "buy", 5, 0.5, True, 0.8) == 0.5


def test_wrong_side_tp_falls_back():
    # long with tp BELOW entry -> tp_dist <= 0 -> base
    assert _effective_tp_threshold(95.0, 100.0, "buy", 5, 0.5, True, 0.8) == 0.5
    # short with tp ABOVE entry -> tp_dist <= 0 -> base
    assert _effective_tp_threshold(105.0, 100.0, "sell", 5, 0.5, True, 0.8) == 0.5


def test_zero_entry_no_divide_by_zero():
    assert _effective_tp_threshold(110.0, 0.0, "buy", 5, 0.5, True, 0.8) == 0.5


def test_leverage_none_or_zero_treated_as_one():
    assert _effective_tp_threshold(110.0, 100.0, "buy", None, 0.5, True, 0.8) == pytest.approx(8.0)
    assert _effective_tp_threshold(110.0, 100.0, "buy", 0, 0.5, True, 0.8) == pytest.approx(8.0)


def test_star_base_preserved_on_fallback():
    # invalid TP under flag ON must still fall back to the STAR base (1.5).
    assert _effective_tp_threshold(0.0, 100.0, "buy", 5, 1.5, True, 0.8) == 1.5
