"""Correlation manager: hard block lifted, soft taper retained (2026-04-29).

Background: at the new sizing regime (50% × 2x = 100% leveraged
notional per trade), the prior correlation group caps (10-30%) fired
on the FIRST position in any group, hard-blocking new trades. This
contradicts the user's "go all-in" + "Don't block any trades"
directives.

Fix:
  - Caps raised to 1.0-2.0 (allow ~2 leveraged positions per group)
  - `can_add` always True — never hard-block
  - size_multiplier still tapers from 1.0 → 0.5 as group fills
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from core.correlation_manager import CorrelationManager, CORRELATION_GROUPS


@dataclass
class _StubPos:
    symbol: str
    side: str
    entry_price: float
    size: float


def test_can_add_is_always_true():
    """Hard block lifted — must never return can_add=False even at
    saturation."""
    mgr = CorrelationManager()
    # Simulate: 3 positions in major_l1 already at full $400 notional each
    open_pos = [
        _StubPos(symbol=f"{s}/USDT:USDT", side="buy",
                 entry_price=1.0, size=400.0)
        for s in ["ETH", "SOL", "BNB"]
    ]
    info = mgr.group_exposure("ATOM/USDT:USDT", open_pos, balance=400.0)
    assert info["can_add"] is True, (
        "Correlation hard-block lifted; must always allow per user directive")


def test_size_multiplier_tapers_with_exposure():
    """As group fills, size_mult walks down 1.0 → 0.8 → 0.6 → 0.5."""
    mgr = CorrelationManager()
    bal = 400.0
    # Empty group: 1.0x
    info = mgr.group_exposure("ETH/USDT:USDT", [], bal)
    assert info["size_multiplier"] == 1.0

    # One $200 notional position in major_l1 (50% of $400 balance, well
    # under 200% cap → ample room).
    pos1 = [_StubPos("SOL/USDT:USDT", "buy", 1.0, 200.0)]
    info = mgr.group_exposure("ETH/USDT:USDT", pos1, bal)
    assert info["size_multiplier"] == 1.0

    # Heavy: 6 positions at $200 each = $1200 notional = 300% of balance
    # (above the 200% cap). Must taper to floor 0.5, NEVER zero.
    pos_heavy = [_StubPos(f"X{i}/USDT:USDT", "buy", 1.0, 200.0)
                 for i in range(6)]
    info_heavy = mgr.group_exposure("ATOM/USDT:USDT", pos_heavy, bal)
    assert info_heavy["size_multiplier"] >= 0.5, "Floor at 0.5"
    assert info_heavy["can_add"] is True


def test_floor_is_one_half_not_zero():
    """Even at maximum saturation, size_mult must be ≥ 0.5 not 0."""
    mgr = CorrelationManager()
    # Massive over-saturation
    huge_pos = [_StubPos(f"X{i}/USDT:USDT", "buy", 1.0, 1000.0)
                for i in range(20)]
    info = mgr.group_exposure("ETH/USDT:USDT", huge_pos, balance=400.0)
    assert info["size_multiplier"] >= 0.5
    assert info["can_add"] is True


def test_caps_raised_to_match_new_sizing():
    """Sanity-check the cap values themselves. Old major_l1=0.30 was
    too tight for 100% leveraged notional per trade."""
    assert CORRELATION_GROUPS["major_l1"]["max_group_pct"] >= 1.0
    assert CORRELATION_GROUPS["btc_proxy"]["max_group_pct"] >= 1.0
    # Meme tier still tightest (most volatile correlation)
    assert (CORRELATION_GROUPS["meme"]["max_group_pct"] <=
            CORRELATION_GROUPS["major_l1"]["max_group_pct"])


def test_uncorrelated_symbol_unaffected():
    """A symbol not in any group passes through with size_mult=1.0."""
    mgr = CorrelationManager()
    info = mgr.group_exposure("RANDOMCOIN/USDT:USDT", [], balance=400.0)
    assert info["group"] is None
    assert info["size_multiplier"] == 1.0
    assert info["can_add"] is True
