"""Phase 2b: the TSMOM exit gate in OrderManager.check_sl_tp.

A tsmom position must be HELD until its own momentum-flip CLOSE — every scalp
early-exit (partial-TP, trailing, breakeven, fixed TP, age/stale) is suppressed
for it — while a WIDE disaster stop remains (wick-stop in paper, widened
hard-max-loss in all modes). These tests pin both halves and prove a non-tsmom
position is unaffected (the change is surgical).

The harness mirrors tests/test_tp_booking_fix.py: a real OrderManager with mocked
collaborators, a directly-constructed Position, and a controlled _net_pnl_at_price
so each test dials an exact net%.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position


def _om(monkeypatch):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None                         # disables entry-staleness block
    om.close_position = MagicMock()
    om.accrue_paper_funding = MagicMock()
    om.sim = MagicMock()
    om.sim.check_wick_trigger.return_value = ("", None)   # no wick by default
    om.trailing = MagicMock()
    om.trailing.update.return_value = (False, "none", 0.0)
    om._early_breakeven_move = lambda pos, price, eff: eff
    # Neutralise partial-TP noise so each test isolates the behaviour under test.
    monkeypatch.setattr("core.order_manager._should_fire_partial_tp",
                        lambda *a, **k: (False, 0.0, 0.0))
    return om


def _pos(strategy, *, entry=100.0, sl=92.0, tp=0.0, side="buy"):
    return Position(
        id="p1", exchange="Binance", symbol="BTC/USDT", side=side,
        market_type="futures", strategy=strategy,
        entry_price=entry, size=1.0, stop_loss=sl, take_profit=tp,
        leverage=1, open_time=1000.0)


def _exchange():
    ex = MagicMock()
    ex.name = "Binance"
    return ex


def _run(om, pos, price, net_pct):
    ex = _exchange()
    ex.fetch_ticker.return_value = {"last": price}
    om.tracker.get_open.return_value = [pos]
    net_pnl = net_pct          # same sign; hard-max-loss needs net_pnl < 0
    om._net_pnl_at_price = MagicMock(return_value=(net_pnl, net_pct, pos.entry_price))
    om.check_sl_tp(ex, "futures")
    return om.close_position


def _closed_reason(close_mock):
    if not close_mock.called:
        return None
    args = close_mock.call_args.args
    # close_position(exchange, pos, reason, [price])
    return args[2] if len(args) > 2 else close_mock.call_args.kwargs.get("reason")


# ── tsmom is HELD through scalp-clip conditions ──────────────────────────────

def test_tsmom_long_in_profit_is_held_even_when_trailing_would_fire(monkeypatch):
    om = _om(monkeypatch)
    # Trailing set to FIRE — a non-tsmom position would be trail-closed here.
    om.trailing.update.return_value = (True, "trail", 101.0)
    close = _run(om, _pos("tsmom"), price=105.0, net_pct=+5.0)
    assert not close.called, "tsmom long must be held, not trail-clipped, in profit"


def test_tsmom_long_at_small_loss_is_held_below_disaster_level(monkeypatch):
    om = _om(monkeypatch)
    # -3.5% would trip the normal 3% hard-max-loss, but tsmom's disaster
    # threshold is ~9% — the position is held.
    close = _run(om, _pos("tsmom"), price=96.5, net_pct=-3.5)
    assert not close.called, "tsmom long must survive a normal pullback"


# ── tsmom KEEPS a wide disaster stop ─────────────────────────────────────────

def test_tsmom_long_disaster_closes_at_widened_hard_max_loss(monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos("tsmom"), price=90.5, net_pct=-9.5)
    assert close.called
    assert _closed_reason(close) == "hard_max_loss"


def test_tsmom_long_wick_stop_still_fires(monkeypatch):
    om = _om(monkeypatch)
    # Paper disaster stop: the wick-stop (independent of TP) must still close a
    # tsmom long — it runs BEFORE the tsmom hold branch, so it isn't suppressed.
    om.sim.check_wick_trigger.return_value = ("stop_loss", 91.5)
    close = _run(om, _pos("tsmom", sl=92.0), price=95.0, net_pct=-5.0)
    assert close.called
    assert _closed_reason(close) == "stop_loss"


# ── non-tsmom positions are UNAFFECTED (surgical change) ─────────────────────

def test_nontsmom_long_closes_at_normal_3pct_hard_max_loss(monkeypatch):
    om = _om(monkeypatch)
    close = _run(om, _pos("claude_portfolio"), price=96.5, net_pct=-3.5)
    assert close.called
    assert _closed_reason(close) == "hard_max_loss"


def test_nontsmom_long_trailing_still_fires(monkeypatch):
    om = _om(monkeypatch)
    om.trailing.update.return_value = (True, "trail", 103.0)
    close = _run(om, _pos("claude_portfolio", tp=200.0), price=105.0, net_pct=+5.0)
    assert close.called
    assert _closed_reason(close) == "trailing_stop"
