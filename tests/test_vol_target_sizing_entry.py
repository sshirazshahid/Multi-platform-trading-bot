"""Vol-target risk-budget sizing on the live entry path (2026-06-11).

Owner: make the bot "technically/mathematically/financially advanced".

The sizing chain previously produced per-trade risk that varied 2.3x
purely with SL width (margin = mtype_bal x size_fraction regardless of
the ATR-based SL: at 1.5% SL/3x a trade risked 0.27% of the pocket; at
the 3.5% ATR-clamp max it risked 0.63%). The new layer caps margin so
loss-at-SL <= VOL_TARGET_SIZING["per_trade_risk_pct"] of the pocket:

    margin_budget = risk_pct * balance / (lev_eff * sl_pct/100)

CEILING-ONLY (min() with the multiplier-chain notional): it can only
shrink a trade, never grow one — every existing de-risk multiplier
keeps full authority, and the only possible behavior change is a
smaller position. Because sl_pct is ATR-derived, notional ~ 1/ATR:
equal loss-at-SL AND approximately equal vol contribution per trade.
Fail-open: degenerate inputs return inf so min() keeps the chain value.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path("core/bot_engine.py")


def test_risk_budget_margin_math():
    """loss at SL == risk_pct of balance, futures and spot."""
    from core.vol_target import risk_budget_margin
    m = risk_budget_margin(5000, 3.5, 3, 0.005)
    assert m * 3 * 0.035 == pytest.approx(25.0)  # 0.5% of 5000
    # spot: leverage 1
    m_spot = risk_budget_margin(5000, 2.0, 1, 0.005)
    assert m_spot * 1 * 0.02 == pytest.approx(25.0)


def test_risk_budget_includes_fractional_leverage_and_stressed_exit_cost():
    from core.vol_target import risk_budget_margin

    assert risk_budget_margin(1000, 2.0, 1.5, 0.0025, 0.002) == pytest.approx(
        2.5 / (1.5 * 0.022)
    )


def test_degenerate_inputs_fail_open():
    """inf on bad input so min(ceiling, budget) keeps the chain notional."""
    from core.vol_target import risk_budget_margin
    assert risk_budget_margin(5000, 0.0, 3, 0.005) == float("inf")
    assert risk_budget_margin(0.0, 1.5, 3, 0.005) == float("inf")
    assert risk_budget_margin(5000, 1.5, 3, 0.0) == float("inf")
    assert risk_budget_margin(None, 1.5, 3, 0.005) == float("inf")
    assert risk_budget_margin(5000, "x", 3, 0.005) == float("inf")


def test_binding_threshold():
    """budget < ceiling iff sl_pct*lev > 100*risk_pct/size_fraction.
    At PAPER knobs (size 0.06, 3x, risk 0.5%): no-op at SL 1.5%,
    binds at SL 3.5% (the wide-ATR tail)."""
    from core.vol_target import risk_budget_margin
    bal, size_fraction = 5000.0, 0.06
    ceiling = bal * size_fraction  # 300
    assert risk_budget_margin(bal, 1.5, 3, 0.005) > ceiling   # no-op
    assert risk_budget_margin(bal, 3.5, 3, 0.005) < ceiling   # binds


def test_config_knob_present_and_on():
    # 2026-07-19: per_trade_risk_pct follows the active PAPER profile
    # (STANDARD 0.0025; AGGRESSIVE_RESEARCH / MAX_FLOW_BAND 0.00125), so
    # assert the wiring instead of hardcoding the STANDARD value — the old
    # literal broke on any machine running a non-standard profile.
    from config import MODE_PROFILE, VOL_TARGET_SIZING
    assert VOL_TARGET_SIZING.get("enabled") is True
    assert VOL_TARGET_SIZING.get("per_trade_risk_pct") == MODE_PROFILE.risk_per_trade_pct
    assert VOL_TARGET_SIZING.get("per_trade_risk_pct") > 0


def test_layer_inserted_after_pinned_notional_line():
    """House pin: the layer sits AFTER the multiplier-chain notional line
    (which other phase tests pin) and BEFORE the $5 min-notional rail."""
    src = SRC.read_text(encoding="utf-8")
    i_notional = src.index("notional = mtype_bal * size_fraction")
    i_layer = src.index("VOL_TARGET_SIZING")
    i_floor = src.index("min_notional = 5.0")
    assert i_notional < i_layer < i_floor


def test_ceiling_only_semantics():
    """Shrink-only: assignment happens only under `if _budget < notional`."""
    src = SRC.read_text(encoding="utf-8")
    i = src.index("VOL_TARGET_SIZING")
    block = src[i:i + 1800]
    assert "if _budget < notional" in block
    assert "notional = _budget" in block


def test_spot_uses_lev_1():
    src = SRC.read_text(encoding="utf-8")
    i = src.index("VOL_TARGET_SIZING")
    block = src[i:i + 1800]
    assert 'leverage if market_type == "futures" else 1' in block


def test_post_phase28_sl_used():
    """The budget keys off the POST-Phase-28-asymmetry SL actually placed
    (sell-side sl_pct*0.60 happens before the layer)."""
    src = SRC.read_text(encoding="utf-8")
    assert src.index("size_pct = size_pct * 0.75") < src.index("VOL_TARGET_SIZING")
