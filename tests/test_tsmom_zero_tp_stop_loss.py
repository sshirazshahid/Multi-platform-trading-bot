"""Zero-take-profit positions must still honour their stop-loss (2026-07-27).

DEFECT (producer/consumer contract mismatch on the "no TP" sentinel):

  PRODUCERS encode "this position has no take-profit" as the literal 0.0:
    * core/bot_engine.py:4385-4389 for tsmom entries ("tp_pct=0 would make
      take_profit==entry (fires on the first tick); force a literal 0 so the
      TP triggers stay inert") -- the origin of the convention.
    * core/position_tracker.py:738-751 -- IMPORTED manual / RECONCILED
      leftover futures positions are built with ``take_profit=0.0`` and a
      REAL positive ``stop_loss`` (the liquidation price as fallback).
    Every one of them expects the stop to remain active.

  CONSUMER  core/order_manager.py:4828-4830
      _sltp_valid = _tp_ok and _sl_ok
    -> reads a non-positive TP as "SL/TP data is garbage" and skips the
       STOP-LOSS branch too (buy branch ~4841, sell branch ~4872). The
       sentinel is indistinguishable from corruption.

Net effect: such a position runs with its intended stop silently disabled,
protected only by the 3% HARD MAX LOSS circuit breaker at
order_manager.py:4815 -- a 2-6x worse loss than designed.

HARNESS NOTE (why these positions are NOT tagged ``strategy="tsmom"``):
tsmom-family positions are diverted at order_manager.py:4589-4608 by the
Phase-2b lane, which ``continue``s after its own take_profit-independent stop
-- they never reach line 4828. Tagging the fixture "tsmom" therefore proves
nothing: the whole SL/TP block is unreachable and all five tests pass or fail
vacuously. The fixture uses the live-reachable shape instead (a non-tsmom
position carrying the same sentinel), which is exactly what the manual /
reconcile import path produces.

The 2026-04-20 guard that introduced the conjunction is NOT reverted: its
actual defect was TAKE_PROFIT firing on the first tick because any positive
price >= 0 (the ALGO naked-short cascade). TP therefore stays gated on
_tp_ok; only the SL branch becomes independent.
test_zero_tp_still_cannot_fire_take_profit pins that.

Harness mirrors tests/test_accuracy_band_time_exit.py.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position


def _om(monkeypatch):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None
    om.close_position = MagicMock()
    om._close_take_profit = MagicMock()
    om.accrue_paper_funding = MagicMock()
    om.sim = MagicMock()
    om.sim.check_wick_trigger.return_value = ("", None)
    om.trailing = MagicMock()
    # A flat trailing tick must hand back the position's OWN stop. Returning a
    # bare 0.0 collapses the SHORT branch's `min(updated_sl, pos.stop_loss)`
    # to 0.0, which fakes an invalid stop and hides the real defect.
    om.trailing.update.side_effect = lambda pos, price: (
        False, "none", float(pos.stop_loss or 0.0))
    om._early_breakeven_move = lambda pos, price, eff: eff
    monkeypatch.setattr("core.order_mgmt.monitor._should_fire_partial_tp",
                        lambda *a, **k: (False, 0.0, 0.0))
    # Isolate from NEAR_TARGET_EXIT_ENABLED=.env — planned-TP-first would
    # otherwise fire TAKE_PROFIT before the SL/TP independence block under test.
    import config
    monkeypatch.setitem(config.RISK, "near_target_exit_enabled", False)
    return om


def _zero_tp_pos(*, side="buy", entry=100.0, sl=92.0, tp=0.0):
    """A sentinel-TP position: real stop, take_profit=0.0.

    ``strategy="manual"`` mirrors position_tracker.py:735 -- an imported
    exchange position, the live shape that actually reaches the SL/TP block.
    """
    return Position(
        id="t1", exchange="Binance", symbol="BTC/USDT", side=side,
        market_type="futures", strategy="manual",
        entry_price=entry, size=1.0, stop_loss=sl, take_profit=tp,
        leverage=1, open_time=time.time() - 3600.0)


def _run(om, pos, *, last, net_pnl, net_pct):
    ex = MagicMock()
    ex.name = "Binance"
    ex.fetch_ticker.return_value = {"last": last}
    om.tracker.get_open.return_value = [pos]
    # net_pnl/net_pct must be mutually consistent, and losses must stay under
    # the 3% HARD MAX LOSS gate, so we isolate the SL branch.
    om._net_pnl_at_price = MagicMock(return_value=(net_pnl, net_pct, pos.entry_price))
    om.check_sl_tp(ex, "futures")
    return om


def _reason(close_mock):
    if not close_mock.called:
        return None
    args = close_mock.call_args.args
    return args[2] if len(args) > 2 else close_mock.call_args.kwargs.get("reason")


# -- the defect ---------------------------------------------------------------

def test_zero_tp_long_still_stops_out(monkeypatch):
    """LONG with tp=0: price through the stop MUST close as stop_loss."""
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="buy", entry=100.0, sl=92.0, tp=0.0)
    _run(om, pos, last=91.0, net_pnl=-1.0, net_pct=-2.0)
    assert om.close_position.called, (
        "long with take_profit=0 did NOT stop out - the stop-loss branch "
        "was skipped because _sltp_valid required a positive take_profit")
    assert _reason(om.close_position) == "stop_loss"


def test_zero_tp_short_still_stops_out(monkeypatch):
    """SHORT with tp=0: price through the stop MUST close as stop_loss."""
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="sell", entry=100.0, sl=108.0, tp=0.0)
    _run(om, pos, last=109.0, net_pnl=-1.0, net_pct=-2.0)
    assert om.close_position.called, (
        "short with take_profit=0 did NOT stop out")
    assert _reason(om.close_position) == "stop_loss"


# -- the 2026-04-20 guard must survive (no regression to the ALGO cascade) ----

def test_zero_tp_still_cannot_fire_take_profit(monkeypatch):
    """The original defect: tp=0 must never trigger TAKE_PROFIT at any price."""
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="buy", entry=100.0, sl=92.0, tp=0.0)
    # Far above entry and PROFITABLE, so the hard-max-loss gate (which needs
    # net_pnl < 0) cannot pre-empt the TP branch and make this pass vacuously.
    _run(om, pos, last=150.0, net_pnl=+50.0, net_pct=+50.0)
    assert not om._close_take_profit.called, (
        "take_profit=0 fired TAKE_PROFIT - the 2026-04-20 ALGO naked-short "
        "cascade guard has been regressed")


def test_invalid_sl_still_skips_stop_branch(monkeypatch):
    """A genuinely absent stop must NOT be invented: sl=0 fires no stop_loss."""
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="buy", entry=100.0, sl=0.0, tp=0.0)
    _run(om, pos, last=91.0, net_pnl=-1.0, net_pct=-2.0)
    assert _reason(om.close_position) != "stop_loss"


def test_invalid_sl_with_valid_tp_does_not_fire_tp(monkeypatch):
    """Boundary pin: sl<=0 with a VALID tp keeps today's behavior (no TP).

    The fix gates the whole trigger block on _sl_ok, so this quadrant is
    byte-identical to pre-fix. A future "fully independent" refactor would
    start firing TAKE_PROFIT on stop-less positions — an uncovered behavior
    change. This test makes crossing that line explicit.
    """
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="buy", entry=100.0, sl=0.0, tp=103.0)
    _run(om, pos, last=110.0, net_pnl=+10.0, net_pct=+10.0)
    assert not om._close_take_profit.called, (
        "a position with no valid stop must not gain a new TP-exit path")


def test_normal_position_unaffected(monkeypatch):
    """Valid SL and TP behave exactly as before."""
    om = _om(monkeypatch)
    pos = _zero_tp_pos(side="buy", entry=100.0, sl=92.0, tp=103.0)
    _run(om, pos, last=91.0, net_pnl=-1.0, net_pct=-2.0)
    assert _reason(om.close_position) == "stop_loss"
