"""A1 (2026-07-16): a close error whose text merely CONTAINS an "already closed"
phrase ("order not found", "position is zero", ...) must NOT untrack a
still-open LIVE position.

Before the fix (core/order_manager.py ~3057), _close_position_impl matched the
error substring and set order_placed=True — removing the position from tracking
(no stop-loss, no monitor) and booking phantom PnL while it was STILL OPEN on
the venue. That is a naked position, the worst state a bot can be in. "order not
found" is especially wrong: it is an ORDER-level error that fires for many
reasons while the POSITION persists.

The fix makes the error string a TRIGGER TO VERIFY, never proof: it calls
fetch_positions and only untracks when the venue POSITIVELY confirms flat. On
still-open OR fetch failure it leaves the position tracked (order_placed=False →
the fail-closed path returns None, keeps it OPEN for reconcile). A falsely-kept
closed position reconciles away harmlessly via ghost-sync; a falsely-untracked
open one does not.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position


def _om():
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = False  # live path
    return om


def _pos():
    p = Position(id="a1", exchange="Binance", symbol="ETH/USDT:USDT", side="buy",
                 market_type="futures", strategy="claude_portfolio",
                 entry_price=100.0, size=1.0, stop_loss=95.0, take_profit=110.0,
                 leverage=3, open_time=1000.0)
    p.paper_trade = False  # force the live close path
    return p


def _ex(create_err, positions):
    ex = MagicMock()
    ex.name = "Binance"
    ex.round_price.side_effect = lambda s, x: x
    ex.round_quantity.side_effect = lambda s, x: x
    ex.create_order.side_effect = RuntimeError(create_err)
    ex.fetch_positions.return_value = positions
    return ex


def test_order_not_found_but_position_still_open_is_not_untracked():
    om = _om()
    pos = _pos()
    # venue error CONTAINS "order not found" but the position is STILL OPEN
    ex = _ex("bybit order not found", [{"contracts": 1.0}])
    result = om.close_position(ex, pos, reason="take_profit", price=110.0)
    assert result is None, "still-open position must NOT report a successful close"
    ex.create_order.assert_called()          # a close was attempted
    ex.fetch_positions.assert_called()        # and the position was verified


def test_position_is_zero_but_still_open_is_not_untracked():
    om = _om()
    pos = _pos()
    ex = _ex("position is zero", [{"contractSize": 2.0}])  # venue still shows size
    result = om.close_position(ex, pos, reason="stop_loss", price=95.0)
    assert result is None
    ex.fetch_positions.assert_called()


def test_fetch_failure_during_verify_keeps_position_tracked():
    om = _om()
    pos = _pos()
    ex = _ex("order not found", [])
    ex.fetch_positions.side_effect = RuntimeError("venue timeout")
    result = om.close_position(ex, pos, reason="take_profit", price=110.0)
    # cannot verify flat -> fail-safe keeps the position OPEN (returns None)
    assert result is None


def test_confirmed_flat_proceeds_to_close():
    om = _om()
    pos = _pos()
    ex = _ex("position does not exist", [])  # venue POSITIVELY confirms flat
    result = om.close_position(ex, pos, reason="take_profit", price=110.0)
    # confirmed flat -> untrack allowed; the fail-closed None path must NOT fire
    assert result is not None
    ex.fetch_positions.assert_called()
