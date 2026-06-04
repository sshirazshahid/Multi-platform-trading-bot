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


def test_auto_transfer_gated_by_dry_run():
    """Audit 2026-06-03: the low-balance auto-transfer in _execute_open must NOT call the
    live exchange.transfer() in PAPER — it is a real Binance Universal-Transfer / Bitget
    fund move. PAPER must simulate the wallet shift in-memory only. This was the one
    transfer site missed by the 2026-05-31 PAPER->live leak fix; it now mirrors
    set_leverage / _execute_fund_ops (gated on DRY_RUN)."""
    from pathlib import Path
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "did_xfer = True if DRY_RUN else exchange.transfer" in src, (
        "auto-transfer must be DRY_RUN-gated: the live exchange.transfer() may only run "
        "when not DRY_RUN; PAPER simulates in-memory")


def test_bitget_set_position_mode_gated_by_dry_run():
    """Audit 2026-06-04: BitgetClient._init_exchange called set_position_mode (a real
    authenticated private POST to the LIVE account) at construction time on EVERY start,
    regardless of OPERATING_MODE — the only init-time live write among the 3 clients. It
    must be DRY_RUN-gated so PAPER/research runs never mutate the owner's live account-wide
    position mode. Source-level pin (the write fires at __init__ before any caller logic, so
    it cannot be gated by a caller)."""
    from pathlib import Path
    src = Path("exchanges/bitget_client.py").read_text(encoding="utf-8")
    i = src.index("set_position_mode(hedged=False")
    # the gate must appear on the lines immediately preceding the call
    preceding = src[max(0, i - 400):i]
    assert "if not DRY_RUN:" in preceding, (
        "Bitget set_position_mode (live private POST) must be guarded by `if not DRY_RUN:` "
        "so it never fires in PAPER")
