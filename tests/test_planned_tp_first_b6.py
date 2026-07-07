"""Audit 2026-06-21 B6: planned-TP-first reorder + trailing age-out activation
floor, both behind the single default-OFF flag RISK['near_target_exit_enabled'].

The discriminating test: with the SAME inputs (price at/beyond TP AND trailing
would fire), flag-OFF closes 'trailing_stop' (today: trailing is checked before
the late TP), flag-ON closes 'take_profit' (the early planned-TP block beats
trailing). Harness mirrors tests/test_tsmom_exit_gate.py.
"""
from __future__ import annotations

import time
import types
from unittest.mock import MagicMock

import config
from core.order_manager import OrderManager
from core.position_tracker import Position
from core.trailing_stop_manager import TrailingStopManager


def _om(monkeypatch):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.dry_run = True
    om.mcp_brain = None                          # disables entry-staleness block
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


def _pos(*, entry=100.0, sl=92.0, tp=105.0, side="buy", strategy="claude_portfolio"):
    return Position(
        id="p1", exchange="Binance", symbol="ETH/USDT", side=side,
        market_type="futures", strategy=strategy,
        entry_price=entry, size=1.0, stop_loss=sl, take_profit=tp,
        leverage=1, open_time=1000.0)


def _run(om, pos, price, net_pct):
    ex = MagicMock(); ex.name = "Binance"
    ex.fetch_ticker.return_value = {"last": price}
    om.tracker.get_open.return_value = [pos]
    om._net_pnl_at_price = MagicMock(return_value=(net_pct, net_pct, pos.entry_price))
    om.check_sl_tp(ex, "futures")
    return om.close_position


def _reason(close_mock):
    if not close_mock.called:
        return None
    a = close_mock.call_args.args
    return a[2] if len(a) > 2 else close_mock.call_args.kwargs.get("reason")


def _set_flag(monkeypatch, on: bool):
    monkeypatch.setitem(config.RISK, "near_target_exit_enabled", on)


# ── The reorder: same inputs, opposite winner depending on the flag ──────────
def test_flag_on_planned_tp_beats_trailing_buy(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    om.trailing.update.return_value = (True, "trailing_stop", 104.0)  # would fire
    close = _run(om, _pos(tp=105.0, side="buy"), price=106.0, net_pct=+6.0)
    assert _reason(close) == "take_profit"
    om.trailing.update.assert_not_called()  # early block continued before trailing


def test_flag_on_planned_tp_beats_trailing_sell(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    om.trailing.update.return_value = (True, "trailing_stop", 96.0)
    close = _run(om, _pos(entry=100.0, sl=108.0, tp=95.0, side="sell"),
                 price=94.0, net_pct=+6.0)
    assert _reason(close) == "take_profit"


def test_flag_off_preserves_trailing_before_tp(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, False)
    om.trailing.update.return_value = (True, "trailing_stop", 104.0)
    close = _run(om, _pos(tp=105.0, side="buy"), price=106.0, net_pct=+6.0)
    # Today's order: trailing (2446) is checked before the late TP (2527).
    assert _reason(close) == "trailing_stop"


# ── Early block fires only at/beyond the planned TP ──────────────────────────
def test_flag_on_below_tp_does_not_early_close(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    om.trailing.update.return_value = (True, "trailing_stop", 103.0)
    close = _run(om, _pos(tp=105.0, side="buy"), price=103.0, net_pct=+3.0)
    assert _reason(close) == "trailing_stop"  # price < tp -> early block skipped


# ── Exchange-held TP is not double-fired by the early block ──────────────────
def test_flag_on_exchange_held_tp_not_double_fired(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    pos = _pos(tp=105.0, side="buy")
    pos._exchange_sl = True
    pos._exchange_tp = True
    close = _run(om, pos, price=106.0, net_pct=+6.0)
    assert not close.called  # exchange owns the TP; early block + late TP both skip


# ── Trailing age-out activation floor (B6 second half) ───────────────────────
def _aged_pos():
    return types.SimpleNamespace(
        open_time=time.time() - 66 * 60, atr_pct=0, market_type="futures",
        symbol="ETH/USDT")


def test_trailing_activation_ignores_age(monkeypatch):
    """2026-07-07: the 50/65-min age tiers (and the B6 cost floor that
    mitigated them) were REMOVED — they were calibrated against a 75-min
    AGE_LIMIT that has been 4h/loss-only since Phase 14, and the ~0 threshold
    insta-closed aged near-flat positions at market (161 of 258 trailing
    exits in 30d, 6% WR, -$52). Age must no longer lower activation,
    regardless of the near_target_exit flag."""
    from config import RISK
    for flag in (True, False):
        _set_flag(monkeypatch, flag)
        tsm = TrailingStopManager()
        assert tsm._adaptive_activation(_aged_pos()) == RISK["trailing_activation"]


# ── The core B6 claim: planned-TP-first must pre-empt PARTIAL-TP (the clipper) ─
def test_flag_on_planned_tp_bypasses_partial_tp(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    # A partial WOULD fire here; the early block must pre-empt it.
    monkeypatch.setattr("core.order_manager._should_fire_partial_tp",
                        lambda *a, **k: (True, 0.5, 1.0))
    om.partial_close_position = MagicMock()
    close = _run(om, _pos(tp=105.0, side="buy"), price=106.0, net_pct=+6.0)
    assert _reason(close) == "take_profit"
    om.partial_close_position.assert_not_called()  # winner not clipped by partial


def test_flag_off_partial_tp_still_fires(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, False)
    monkeypatch.setattr("core.order_manager._should_fire_partial_tp",
                        lambda *a, **k: (True, 0.5, 1.0))
    om.partial_close_position = MagicMock()
    _run(om, _pos(tp=105.0, side="buy"), price=106.0, net_pct=+6.0)
    # Flag OFF: today's order -> partial fires (and the late TP closes after).
    om.partial_close_position.assert_called_once()


def test_flag_on_planned_tp_first_leveraged_position(monkeypatch):
    # B6 trigger is a pure price compare (leverage-independent) — pin it anyway.
    om = _om(monkeypatch)
    _set_flag(monkeypatch, True)
    om.trailing.update.return_value = (True, "trailing_stop", 104.0)
    pos = _pos(tp=105.0, side="buy")
    pos.leverage = 5
    close = _run(om, pos, price=106.0, net_pct=+30.0)
    assert _reason(close) == "take_profit"


def test_flag_off_preserves_trailing_before_tp_sell(monkeypatch):
    om = _om(monkeypatch)
    _set_flag(monkeypatch, False)
    om.trailing.update.return_value = (True, "trailing_stop", 96.0)
    close = _run(om, _pos(entry=100.0, sl=108.0, tp=95.0, side="sell"),
                 price=94.0, net_pct=+6.0)
    assert _reason(close) == "trailing_stop"  # today's order, sell side


def test_flag_defaults_off_when_env_unset():
    # The whole safety story is "default OFF => byte-identical". Pin the default
    # so an env-default typo can't ship the EV-shape change to PAPER green.
    # (All flag toggles in this file use auto-reverting monkeypatch.setitem, so
    # the imported value here is the import-time env default — no reload needed,
    # which would divorce config.RISK from the modules' bound reference.)
    assert config.RISK["near_target_exit_enabled"] is False
    assert config.RISK["near_target_frac"] == 0.8
