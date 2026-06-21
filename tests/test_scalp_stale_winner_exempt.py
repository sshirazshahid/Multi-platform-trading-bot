"""Audit 2026-06-21 H3: scalp_stale_close must NOT clip a profitable aged scalp.

The bug: the stale branch read `pos.pnl_pct`, which is None on an OPEN position
(only set at close), so `float(pos.pnl_pct or 0) == 0.0 < stale_min_profit (0.3)`
was ALWAYS true and force-closed EVERY scalp older than stale_close_min — winners
included. The fix computes the live fee-aware net via _net_pnl_at_price and spares
genuinely-profitable positions.

Harness mirrors tests/test_tsmom_exit_gate.py: a real OrderManager with mocked
collaborators, a directly-constructed scalp Position aged into the stale window
(>= stale_close_min, < time_wall), and a controlled _net_pnl_at_price.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from config import SCALP_MODE
from core.order_manager import OrderManager
from core.position_tracker import Position


def _om(monkeypatch):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None
    om.close_position = MagicMock()
    om.accrue_paper_funding = MagicMock()
    om.sim = MagicMock()
    om.sim.check_wick_trigger.return_value = ("", None)
    om.trailing = MagicMock()
    om.trailing.update.return_value = (False, "none", 0.0)
    om._early_breakeven_move = lambda pos, price, eff: eff
    monkeypatch.setattr("core.order_manager._should_fire_partial_tp",
                        lambda *a, **k: (False, 0.0, 0.0))
    return om


def _scalp_pos(*, age_min: float, entry=100.0, tp=200.0, side="buy"):
    # open_time as a tz-aware datetime so the scalp block computes a real age
    # (a float epoch would fail datetime.fromisoformat and yield age 0).
    ot = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    p = Position(
        id="s1", exchange="Binance", symbol="BTC/USDT", side=side,
        market_type="futures", strategy="scalp",
        entry_price=entry, size=1.0, stop_loss=92.0, take_profit=tp,
        leverage=1, open_time=ot)
    p._scalp = True               # force the scalp-block branch
    return p


def _exchange():
    ex = MagicMock()
    ex.name = "Binance"
    ex.fetch_ticker.return_value = {"last": 101.0}
    return ex


def _run(om, pos, *, net_pnl, net_pct):
    ex = _exchange()
    om.tracker.get_open.return_value = [pos]
    om._net_pnl_at_price = MagicMock(return_value=(net_pnl, net_pct, pos.entry_price))
    om.check_sl_tp(ex, "futures")
    return om.close_position


def _closed_reason(close_mock):
    if not close_mock.called:
        return None
    args = close_mock.call_args.args
    return args[2] if len(args) > 2 else close_mock.call_args.kwargs.get("reason")


# stale window: stale_close_min <= age < time_wall_min
_STALE_AGE = (SCALP_MODE["stale_close_min"] + SCALP_MODE["time_wall_min"]) / 2
_FLOOR = SCALP_MODE["stale_min_profit"]


def test_profitable_aged_scalp_is_NOT_stale_closed(monkeypatch):
    om = _om(monkeypatch)
    # Winner above the stale floor: must be spared by scalp_stale, then it is
    # closed downstream by the deterministic configured-TP (tp just below the
    # 101.0 mark). The point is the close reason is NOT scalp_stale_close.
    close = _run(om, _scalp_pos(age_min=_STALE_AGE, tp=100.5),
                 net_pnl=+5.0, net_pct=_FLOOR + 1.0)
    assert _closed_reason(close) != "scalp_stale_close", \
        "a profitable aged scalp must not be force-closed by scalp_stale"


def test_losing_aged_scalp_IS_stale_closed(monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _scalp_pos(age_min=_STALE_AGE),
                 net_pnl=-1.0, net_pct=-0.5)
    assert close.called
    assert _closed_reason(close) == "scalp_stale_close"


def test_flat_below_floor_aged_scalp_IS_stale_closed(monkeypatch):
    om = _om(monkeypatch)
    # Tiny positive $ but net% below the stale floor -> still "flat", close it.
    close = _run(om, _scalp_pos(age_min=_STALE_AGE),
                 net_pnl=+0.01, net_pct=_FLOOR - 0.1)
    assert close.called
    assert _closed_reason(close) == "scalp_stale_close"


def test_stale_decision_uses_live_net_not_none_pnl_pct(monkeypatch):
    # The regression guard: pos.pnl_pct is None, but the profitable live net
    # must win. If the code regressed to reading pnl_pct, float(None or 0)=0.0
    # would be < floor and the winner would be wrongly closed.
    om = _om(monkeypatch)
    pos = _scalp_pos(age_min=_STALE_AGE, tp=100.5)
    assert pos.pnl_pct is None
    close = _run(om, pos, net_pnl=+3.0, net_pct=_FLOOR + 0.5)
    assert _closed_reason(close) != "scalp_stale_close"
