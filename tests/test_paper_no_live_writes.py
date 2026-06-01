"""Regression: PAPER (DRY_RUN) must never issue live order-writes via the
SL-replace path.

Covers the 2026-05-31 CRITICAL: bot_engine._replace_exchange_sl had no DRY_RUN
gate, so BREAKEVEN/TIGHTEN/age-SL actions on PAPER positions fired REAL
cancel_all_orders + SL/TP placements onto the live exchange accounts (Bitget
rejected with 43023 → fail-closed paper winners; Binance/Bybit accepted →
phantom resting orders). The entry path (order_manager.open_position:1045) was
correctly gated; the SL-replace path was the gap.

In PAPER the move must be applied IN-MEMORY only (caller sets pos.stop_loss on a
True return; the dry_run check_sl_tp wick-trigger enforces it) with zero venue
writes. In LIVE the cancel+replace must still happen.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import core.bot_engine as be
from core.bot_engine import BotEngine


def _make_engine():
    eng = object.__new__(BotEngine)  # bypass heavy __init__
    eng.order_mgr = MagicMock()
    return eng


def _make_pos():
    # BUY @ futures, exchange-SL flags start True to prove the paper path clears them
    return SimpleNamespace(
        symbol="BNB/USDT:USDT", side="buy", market_type="futures",
        size=1.0, stop_loss=700.0, take_profit=760.0,
        _exchange_sl=True, _exchange_tp=True,
    )


def _make_exchange(last=750.0):
    ex = MagicMock()
    ex.fetch_ticker.return_value = {"last": last}
    return ex


def test_replace_exchange_sl_paper_no_live_write(monkeypatch):
    """DRY_RUN: returns True, mutates in-memory only, issues NO venue write."""
    monkeypatch.setattr(be, "DRY_RUN", True)
    eng = _make_engine()
    pos = _make_pos()
    ex = _make_exchange(last=750.0)

    # new_sl=720 sits below last=750 for a BUY → passes the side pre-flight,
    # so the gate (not the pre-flight) is what stops the venue write.
    result = eng._replace_exchange_sl(ex, pos, 720.0)

    assert result is True
    ex.cancel_all_orders.assert_not_called()
    eng.order_mgr._place_exchange_sl_tp.assert_not_called()
    assert pos._exchange_sl is False
    assert pos._exchange_tp is False


def test_replace_exchange_sl_live_still_writes(monkeypatch):
    """LIVE: the gate must NOT short-circuit — cancel + replace must run."""
    monkeypatch.setattr(be, "DRY_RUN", False)
    eng = _make_engine()
    pos = _make_pos()
    ex = _make_exchange(last=750.0)

    result = eng._replace_exchange_sl(ex, pos, 720.0)

    assert result is True
    ex.cancel_all_orders.assert_called_once()
    eng.order_mgr._place_exchange_sl_tp.assert_called_once()
