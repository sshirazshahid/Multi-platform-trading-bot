"""Tests for the sweet-spot partial-TP retune (2026-05-19).

PARTIAL_TP had zero unit tests despite being live for weeks. This file
establishes coverage AND pins the new threshold values so a future
accidental revert is caught immediately.

The behavior tests (3-6) exercise the extracted helper
core.order_manager._should_fire_partial_tp directly. The pin tests
(1-2) read config.PARTIAL_TP straight from the config module.
"""
from __future__ import annotations

from unittest.mock import MagicMock

# -- Pin tests (catch accidental revert of config values) --------------

def test_partial_tp_default_first_take_at_pct_is_035():
    """Retune value pinned at 0.35 (was 0.5 before 2026-05-19)."""
    from config import PARTIAL_TP
    assert PARTIAL_TP["first_take_at_pct"] == 0.35


def test_partial_tp_default_first_take_size_is_03():
    """2026-05-25 retune: 0.6 → 0.3. The no-edge-forensics swarm traced
    realized R:R 0.63 to booking 60% of every winner at 0.42 R; lowering
    to 0.3 lets 70% ride to full TP. See test_partial_tp_retune_2026_05_25
    + memory no-edge-forensics-2026-05-25."""
    from config import PARTIAL_TP
    assert PARTIAL_TP["first_take_size"] == 0.3


# -- Behavior tests via the extracted _should_fire_partial_tp helper --

def _stub_position(side, entry, tp, partial_taken=False):
    pos = MagicMock()
    pos.symbol = "ATOM/USDT:USDT"
    pos.side = side
    pos.entry_price = entry
    pos.take_profit = tp
    pos.partial_taken = partial_taken
    return pos


def test_partial_fires_at_35pct_long():
    """Long: entry=100, tp=105. Price at 101.75 (35% of TP distance) fires
    partial with size=0.3 (2026-05-25 retune; was 0.6)."""
    from config import PARTIAL_TP
    from core.order_manager import _should_fire_partial_tp

    pos = _stub_position(side="buy", entry=100.0, tp=105.0)
    should_fire, take_sz, partial_level = _should_fire_partial_tp(
        pos, 101.75, PARTIAL_TP)
    assert should_fire is True
    assert take_sz == 0.3
    assert abs(partial_level - 101.75) < 1e-9


def test_partial_fires_at_35pct_short():
    """Short mirror: entry=100, tp=95. Price at 98.25 (35% of TP distance below entry)."""
    from config import PARTIAL_TP
    from core.order_manager import _should_fire_partial_tp

    pos = _stub_position(side="sell", entry=100.0, tp=95.0)
    should_fire, take_sz, partial_level = _should_fire_partial_tp(
        pos, 98.25, PARTIAL_TP)
    assert should_fire is True
    assert take_sz == 0.3
    assert abs(partial_level - 98.25) < 1e-9


def test_partial_does_not_fire_below_35pct():
    """Long at 101.70 (34% of TP distance) is below threshold; no fire."""
    from config import PARTIAL_TP
    from core.order_manager import _should_fire_partial_tp

    pos = _stub_position(side="buy", entry=100.0, tp=105.0)
    should_fire, _, partial_level = _should_fire_partial_tp(
        pos, 101.70, PARTIAL_TP)
    assert should_fire is False
    assert abs(partial_level - 101.75) < 1e-9


def test_partial_taken_flag_prevents_double_fire():
    """Position with partial_taken=True never re-fires, even at higher price."""
    from config import PARTIAL_TP
    from core.order_manager import _should_fire_partial_tp

    pos = _stub_position(side="buy", entry=100.0, tp=105.0, partial_taken=True)
    should_fire, _, _ = _should_fire_partial_tp(pos, 102.5, PARTIAL_TP)
    assert should_fire is False
