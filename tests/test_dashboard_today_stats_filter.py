"""Pin the 2026-05-04 dashboard fix: 'Today' / 'Yesterday' stats filter
out manual positions and external reconciliations.

Bug we're fixing
================
User reported the dashboard's PERFORMANCE box showing:

    Today        +0.1883 USDT  trades:3  W:2 L:1  WR:66.7%

When the bot had only opened/closed 2 actual bot trades today.
Investigation showed positions.json had 3 entries closed today:

  - MANUAL-bitget-... +$0.998 (a manual BTC position on Bitget the
    user opened+closed on the exchange; bot only reconciled it)
  - 1002434242541    -$1.038 (bot trade, opened yesterday, closed today)
  - 14349108500733   +$0.228 (bot trade, opened today, closed today)

The MANUAL position inflated trade count + WR. The fix excludes:
  - id-prefix "MANUAL-" or strategy="manual" (manual user positions
    imported via sync_with_exchanges, not bot-initiated)
  - close_reason in {reconciled_from_exchange, reconciled_no_context}
    (external imports the bot didn't trade)

Importantly NOT excluded (these ARE bot trades):
  - close_reason in {ghost_sync, ghost_reconciled, ghost_force_close} —
    positions the bot OPENED that closed via exchange-side mechanisms
    (SL fill, manual close on exchange). Real bot trades; counted.

All-time stats unchanged — they include everything (real PnL is real PnL).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_closed(close_reason="trailing_stop", pnl=1.0,
                 close_time=None, position_id="bot-001",
                 strategy="claude_portfolio", open_time=None):
    import time
    if close_time is None:
        close_time = time.time()
    if open_time is None:
        open_time = close_time - 3600
    return {
        "id": position_id,
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "strategy": strategy,
        "entry_price": 70000.0, "exit_price": 70100.0,
        "size": 0.001,
        "open_time": open_time, "close_time": close_time,
        "pnl": pnl, "gross_pnl": pnl + 0.05, "total_fees": 0.05,
        "close_reason": close_reason,
        "leverage": 2,
    }


def test_manual_id_prefix_excluded_from_today():
    """An id starting with MANUAL- means user reconciled, not bot trade."""
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="MANUAL-bitget-x", pnl=0.998,
                     close_reason="ghost_sync"),
        _make_closed(position_id="bot-real",        pnl=0.228,
                     close_reason="ghost_sync"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 1, "MANUAL- id must be excluded"
    assert abs(s["today_pnl"] - 0.228) < 1e-6
    # All-time still includes everything
    assert s["total_n"] == 2
    assert abs(s["all_pnl"] - 1.226) < 1e-6


def test_manual_strategy_excluded_from_today():
    from dashboard import calc_stats
    closed = [
        _make_closed(strategy="manual", pnl=2.0, close_reason="ghost_sync"),
        _make_closed(strategy="claude_portfolio", pnl=1.0,
                     close_reason="trailing_stop"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 1
    assert abs(s["today_pnl"] - 1.0) < 1e-6


def test_reconcile_reasons_excluded_from_today():
    """Positions imported via sync (reconciled_from_exchange) aren't bot trades."""
    from dashboard import calc_stats
    closed = [
        _make_closed(close_reason="reconciled_from_exchange", pnl=5.0),
        _make_closed(close_reason="reconciled_no_context",     pnl=-3.0),
        _make_closed(close_reason="trailing_stop",             pnl=0.5),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 1
    assert abs(s["today_pnl"] - 0.5) < 1e-6


def test_ghost_sync_INCLUDED_when_bot_opened():
    """ghost_sync = bot-opened position closed via exchange-side mechanism.
    Real bot trade — must count."""
    from dashboard import calc_stats
    closed = [
        _make_closed(close_reason="ghost_sync",       pnl=1.5),
        _make_closed(close_reason="ghost_reconciled", pnl=-0.7),
        _make_closed(close_reason="ghost_force_close", pnl=0.2),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 3
    assert abs(s["today_pnl"] - 1.0) < 1e-6


def test_all_time_pnl_unchanged_by_filter():
    """The filter only affects today/yesterday, NEVER all-time."""
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="MANUAL-x", pnl=10.0),
        _make_closed(strategy="manual",      pnl=5.0),
        _make_closed(close_reason="reconciled_from_exchange", pnl=-2.0),
        _make_closed(pnl=1.0),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 1
    assert abs(s["all_pnl"] - 14.0) < 1e-6
    assert s["total_n"] == 4
