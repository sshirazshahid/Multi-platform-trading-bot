"""C9 (tpbot retrofit): flatten_all() — the missing close-all primitive.

Gap (survey 2026-07-08): no close-all function existed anywhere in core/ —
breakers and the operator had nothing to CALL even when they wanted to
flatten (the clock-drift/anomaly Tier-2 ideal, manual emergencies, venue
migration). This is an implementation gap distinct from the owner's
no-auto-halt policy: a callable primitive is not an automatic trigger.

Semantics:
  * closes every OPEN tracked position (optional exchange/market filters)
    through the ONE close authority — self.close_position -> tracker.close
    -> on_close -> _finalize_close — so warehouse/wallet/risk/journal hooks
    all fire exactly as for any other close (no new TP/close authority)
  * continues past per-position failures and reports them; never raises
  * works in PAPER (sim closes) and LIVE alike
  * NO automatic trigger: nothing in core/bot_engine.py calls it (pinned by
    a source-scan test below per the owner's no-auto-halt directive)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core.order_manager import OrderManager
from core.position_tracker import Position


def _pos(pid, exchange="Binance", symbol="ETH/USDT", market_type="futures"):
    return Position(
        id=pid,
        exchange=exchange,
        symbol=symbol,
        side="buy",
        market_type=market_type,
        strategy="claude_portfolio",
        entry_price=100.0,
        size=1.0,
        stop_loss=92.0,
        take_profit=110.0,
        leverage=3,
        open_time=1000.0,
    )


def _om(positions, exchanges=None):
    om = OrderManager(tracker=MagicMock(), risk=MagicMock(), notifier=MagicMock())
    om.tracker.get_open.return_value = positions
    om.close_position = MagicMock(return_value=True)
    om.set_exchanges(
        exchanges if exchanges is not None else {"binance": MagicMock(name="binance_ex")}
    )
    return om


def test_flatten_all_closes_every_open_position():
    positions = [_pos("p1"), _pos("p2"), _pos("p3")]
    om = _om(positions)
    result = om.flatten_all(reason="operator_flatten")
    assert result["closed"] == 3
    assert result["failed"] == []
    assert om.close_position.call_count == 3
    for call in om.close_position.call_args_list:
        assert call.kwargs.get("reason") == "operator_flatten"


def test_flatten_all_continues_past_failures_and_reports():
    positions = [_pos("p1"), _pos("p2"), _pos("p3")]
    om = _om(positions)
    om.close_position.side_effect = [True, RuntimeError("venue down"), True]
    result = om.flatten_all(reason="operator_flatten")
    assert result["closed"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0][0] == "ETH/USDT"
    assert "venue down" in result["failed"][0][1]
    assert om.close_position.call_count == 3  # p3 still attempted


def test_flatten_all_exchange_filter():
    positions = [_pos("p1", exchange="Binance"), _pos("p2", exchange="Bybit")]
    om = _om(positions, exchanges={"binance": MagicMock(), "bybit": MagicMock()})
    result = om.flatten_all(reason="x", exchange_name="bybit")
    assert result["closed"] == 1
    assert om.close_position.call_count == 1
    closed_pos = om.close_position.call_args[0][1]
    assert closed_pos.exchange == "Bybit"


def test_flatten_all_skips_unresolvable_exchange():
    positions = [_pos("p1", exchange="Kraken")]  # not in registry
    om = _om(positions, exchanges={"binance": MagicMock()})
    result = om.flatten_all(reason="x")
    assert result["closed"] == 0
    assert result["skipped"] == 1
    om.close_position.assert_not_called()


def test_flatten_all_empty_book_is_clean_noop():
    om = _om([])
    result = om.flatten_all(reason="x")
    assert result == {"closed": 0, "failed": [], "skipped": 0}


def test_flatten_all_never_raises():
    om = _om([_pos("p1")])
    om.tracker.get_open.side_effect = RuntimeError("tracker corrupt")
    result = om.flatten_all(reason="x")
    assert result["closed"] == 0
    assert result["failed"]  # the tracker failure is reported, not raised


def test_no_automatic_trigger_wired_in_bot_engine():
    """Owner directive: no auto-halts. flatten_all is operator/breaker-
    CALLABLE only — nothing in the engine invokes it automatically."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    assert "flatten_all" not in src, (
        "flatten_all must not be auto-wired into bot_engine — the owner's "
        "no-auto-halt directive stands; it is a callable primitive only"
    )


# ── review 2026-07-09: close_position signals failure by RETURNING None ──────
def test_none_return_still_open_counts_failed():
    """Venue outage: every close returns None and the book stays open — the
    old code reported {closed: N, failed: []}. The tracker is the arbiter."""
    positions = [_pos("p1")]
    om = _om(positions)
    om.close_position = MagicMock(return_value=None)
    om.tracker.get_open.return_value = positions  # still open after the call
    result = om.flatten_all(reason="x")
    assert result["closed"] == 0
    assert len(result["failed"]) == 1
    assert "unconfirmed" in result["failed"][0][1]


def test_none_return_position_gone_counts_closed():
    """None is also the benign already-closed race no-op — when the tracker
    confirms the position is gone, it counts as closed, not failed."""
    positions = [_pos("p1")]
    om = _om(positions)
    om.close_position = MagicMock(return_value=None)
    om.tracker.get_open.side_effect = [positions, []]  # iteration, then verify
    result = om.flatten_all(reason="x")
    assert result["closed"] == 1
    assert result["failed"] == []
