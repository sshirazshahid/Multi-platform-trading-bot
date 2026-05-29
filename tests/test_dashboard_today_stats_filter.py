"""Pin dashboard PERFORMANCE filter behaviour.

2026-05-04 Phase 21: PERFORMANCE excluded MANUAL-prefix positions so the
count matched bot-opened trades only. This caused the dashboard to appear
frozen — emails fired for MANUAL closes (bot manages SL/TP on them) but
PERFORMANCE didn't update.

2026-05-06 Phase 36: switched PERFORMANCE/Daily/Weekly/Heatmap to
_filter_real_trades (broader): includes MANUAL-prefix and strategy="manual"
positions since they hold real capital and the bot actively manages their
exits. Only RECONCILE size-adjustments and zero-context imports remain
excluded. STRATEGY BREAKDOWN keeps _filter_bot_trades (bot-initiated only).

Filter matrix (after Phase 36):
  id MANUAL-prefix    → INCLUDED in PERFORMANCE (real money)
  id RECONCILE-prefix → EXCLUDED (size adjustment, not a standalone trade)
  strategy "manual"   → INCLUDED in PERFORMANCE
  strategy "reconcile"→ EXCLUDED
  close_reason in {reconciled_from_exchange, reconciled_no_context} → EXCLUDED
  ghost_sync / ghost_reconciled / ghost_force_close → INCLUDED (real closes)
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


def test_manual_id_prefix_included_in_performance():
    """Phase 36 (2026-05-06): MANUAL-prefix positions ARE included in
    PERFORMANCE because the bot manages their SL/TP and their P&L is real.
    Excluding them caused the dashboard to appear frozen when emails fired
    for MANUAL closes but the stats panel didn't update."""
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="MANUAL-bitget-x", pnl=0.998,
                     close_reason="ghost_sync"),
        _make_closed(position_id="bot-real",        pnl=0.228,
                     close_reason="ghost_sync"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 2, "MANUAL- id must now be INCLUDED in PERFORMANCE"
    assert abs(s["today_pnl"] - (0.998 + 0.228)) < 1e-6
    assert s["total_n"] == 2
    assert abs(s["all_pnl"] - (0.998 + 0.228)) < 1e-6


def test_manual_strategy_included_in_performance():
    """Phase 36: strategy="manual" also included in PERFORMANCE — same
    rationale as MANUAL-prefix id."""
    from dashboard import calc_stats
    closed = [
        _make_closed(strategy="manual", pnl=2.0, close_reason="ghost_sync"),
        _make_closed(strategy="claude_portfolio", pnl=1.0,
                     close_reason="trailing_stop"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 2, "strategy='manual' must now be INCLUDED"
    assert abs(s["today_pnl"] - 3.0) < 1e-6


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


def test_all_time_real_trade_filter_phase36():
    """Phase 36 (2026-05-06): PERFORMANCE uses _filter_real_trades.
    MANUAL-prefix and strategy="manual" are INCLUDED (real capital).
    Only RECONCILE-prefix and reconciled_* close_reasons are excluded.
    """
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="MANUAL-x", pnl=10.0),     # INCLUDED
        _make_closed(strategy="manual",      pnl=5.0),       # INCLUDED
        _make_closed(close_reason="reconciled_from_exchange", pnl=-2.0),  # excluded
        _make_closed(pnl=1.0),                               # INCLUDED
    ]
    s = calc_stats(closed)
    # 3 rows included: MANUAL-x, strategy=manual, plain pnl=1.0
    assert s["today_n"] == 3
    assert abs(s["today_pnl"] - 16.0) < 1e-6
    assert s["total_n"] == 3
    assert abs(s["all_pnl"] - 16.0) < 1e-6


# ─── Phase 21 — cross-panel consistency ───────────────────────────────


def test_calc_daily_pnl_applies_filter():
    """Daily PnL heatmap uses _filter_real_trades — same as PERFORMANCE.
    Phase 36: MANUAL-x is now included; reconciled_from_exchange excluded."""
    from dashboard import calc_daily_pnl
    closed = [
        _make_closed(position_id="MANUAL-x", pnl=10.0),           # INCLUDED
        _make_closed(close_reason="reconciled_from_exchange", pnl=5.0),  # excluded
        _make_closed(pnl=1.0),                                     # INCLUDED
        _make_closed(close_reason="ghost_sync", pnl=2.0),          # INCLUDED
    ]
    daily = calc_daily_pnl(closed, days=2)
    today_bucket = daily[-1]
    # 3 rows: MANUAL-x + pnl=1 + ghost_sync pnl=2
    assert today_bucket["trades"] == 3
    assert abs(today_bucket["pnl"] - 13.0) < 1e-6


def test_calc_strategy_stats_applies_filter():
    from dashboard import calc_strategy_stats
    closed = [
        _make_closed(strategy="manual",      pnl=5.0),
        _make_closed(strategy="claude_portfolio", pnl=1.0),
        _make_closed(strategy="claude_portfolio", pnl=-0.5),
    ]
    s = calc_strategy_stats(closed)
    # 'manual' must be filtered out entirely
    assert "manual" not in s
    assert s["claude_portfolio"]["n"] == 2


def test_calc_exchange_stats_applies_filter():
    """Phase 36: MANUAL-x is now included in exchange stats (real P&L)."""
    from dashboard import calc_exchange_stats
    closed = [
        _make_closed(position_id="MANUAL-x", pnl=10.0),
        _make_closed(pnl=1.0),
    ]
    open_pos = []
    s = calc_exchange_stats(closed, open_pos)
    exchange = s.get("binance") or s.get("unknown")
    assert exchange is not None
    assert exchange["n"] == 2, "MANUAL-x must now be INCLUDED in exchange stats"
    assert abs(exchange["pnl"] - 11.0) < 1e-6


# ─── Phase 37 — RECONCILE positions closed by bot count ───────────────


def test_reconcile_prefix_with_mcp_take_profit_included():
    """Phase 37 (2026-05-06): RECONCILE-prefix positions actively closed by
    the bot (close_reason=mcp_take_profit) must be counted in PERFORMANCE.
    These are real capital events — the bot managed the exit.
    Observed in prod: RECONCILE-binance-DOT +$1.36 and RECONCILE-binance-BNB
    +$0.99 via mcp_take_profit were excluded, creating a ~$2.35 gap between
    the daily email (reads risk_state.daily_pnl) and the dashboard."""
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="RECONCILE-binance-DOT", pnl=1.3613,
                     strategy="reconcile", close_reason="mcp_take_profit"),
        _make_closed(position_id="RECONCILE-binance-BNB", pnl=0.9871,
                     strategy="reconcile", close_reason="mcp_take_profit"),
        _make_closed(pnl=1.0, close_reason="trailing_stop"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 3, (
        "RECONCILE positions closed by mcp_take_profit must be INCLUDED")
    assert abs(s["today_pnl"] - (1.3613 + 0.9871 + 1.0)) < 1e-4


def test_reconcile_prefix_pure_import_excluded():
    """RECONCILE positions are INCLUDED when the close reason is not a pure-import
    marker (reconciled_no_context / reconciled_from_exchange).

    2026-05-20 fix: the old whitelist (_BOT_CLOSE_REASONS) missed Claude's
    descriptive close reasons such as "4h RSI=32.2 near oversold, lock +1.4%".
    Those are real bot decisions that carry real P&L (AAVE +$1.23, BCH -$0.39
    on 2026-05-20). The new rule: include RECONCILE positions unless the close
    reason is explicitly a pure-import marker. An "unknown_reason" on a RECONCILE
    position means the bot's position monitor decided to close it — include it.
    Only _DASH_RECONCILE_REASONS values (reconciled_no_context, reconciled_from_exchange)
    are excluded.
    """
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="RECONCILE-binance-X", pnl=5.0,
                     strategy="reconcile", close_reason="unknown_reason"),
        _make_closed(pnl=1.0, close_reason="trailing_stop"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 2, (
        "RECONCILE with any non-import close_reason must now be INCLUDED")
    assert abs(s["today_pnl"] - 6.0) < 1e-6


def test_reconcile_age_loss_included():
    """RECONCILE positions closed by AGE_LOSS (bot's age-cutoff rule) count."""
    from dashboard import calc_stats
    closed = [
        _make_closed(position_id="RECONCILE-binance-DOT", pnl=-0.21,
                     strategy="reconcile", close_reason="AGE_LOSS"),
        _make_closed(pnl=1.0, close_reason="trailing_stop"),
    ]
    s = calc_stats(closed)
    assert s["today_n"] == 2
    assert abs(s["today_pnl"] - (-0.21 + 1.0)) < 1e-4
