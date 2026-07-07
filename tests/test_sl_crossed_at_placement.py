"""Tests for the 2026-05-21 SL pre-flight and race-rejection downgrade.

Two distinct paths in `OrderManager._place_exchange_sl_tp`:

1. **Pre-flight** — before submitting, fetch mark price and detect when the
   target SL is already on the wrong side (SHORT with SL <= mark, or LONG
   with SL >= mark). The exchange would correctly reject this placement
   (Bybit 110092, Bitget 45122). Without this guard, the fail-closed path
   logged EMERGENCY + 5 debug spam lines and market-closed the position.

2. **Race-rejection** — if mark moves between the pre-flight check and the
   exchange's view of mark, the create_order call returns the same reject
   codes. The except handler now downgrades those specific codes to INFO
   and closes normally with reason `sl_crossed_during_placement`.

Both branches must close the position (it would have been closed by SL
anyway) but log INFO not EMERGENCY.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.order_manager import OrderManager
from core.position_tracker import Position, PositionTracker
from core.risk_manager import RiskManager


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    yield


@pytest.fixture
def om() -> OrderManager:
    notifier = MagicMock()
    tracker = PositionTracker()
    risk = RiskManager()
    manager = OrderManager(tracker, risk, notifier)
    manager.dry_run = False
    tracker.on_close = manager._finalize_close
    return manager


def _make_exchange(name: str, last: float) -> MagicMock:
    ex = MagicMock()
    ex.name = name
    ex.round_price.side_effect = lambda sym, p: p
    ex.round_quantity.side_effect = lambda sym, q: q
    ex.fetch_ticker.return_value = {"last": last, "close": last}
    return ex


def _make_position(om: OrderManager, side: str, entry: float,
                   sl: float, tp: float) -> Position:
    pos = Position(
        id=f"test-{side}",
        exchange="bybit",
        symbol="ARB/USDT:USDT",
        side=side,
        market_type="futures",
        strategy="test",
        entry_price=entry,
        size=100.0,
        stop_loss=sl,
        take_profit=tp,
        leverage=3,
    )
    om.tracker._open[pos.id] = pos
    return pos


# ── 1. Pre-flight: short with SL already at/below mark ────────────────────


def test_preflight_short_sl_at_mark_closes_normally(om):
    """Bybit log: SL=0.11014, mark=0.11014 (short). Pre-flight must catch."""
    ex = _make_exchange("Bybit", last=0.11014)
    pos = _make_position(om, side="sell", entry=0.10800,
                         sl=0.11014, tp=0.10500)
    # Mock close_position so we can verify it was called with the right reason
    captured = {}

    def fake_close(exchange, position, reason, **kw):
        captured["reason"] = reason
        captured["pos_id"] = position.id

    om.close_position = fake_close
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    # Exchange create_order MUST NOT have been called — pre-flight short-circuit
    ex.create_order.assert_not_called()
    assert captured.get("reason") == "sl_crossed_at_placement"
    assert captured.get("pos_id") == "test-sell"


def test_preflight_short_sl_below_mark_closes_normally(om):
    """SL below mark for short = past where it should have triggered."""
    ex = _make_exchange("Bitget", last=0.11200)
    pos = _make_position(om, side="sell", entry=0.10800,
                         sl=0.11170, tp=0.10500)
    captured = {}
    om.close_position = lambda ex, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    ex.create_order.assert_not_called()
    assert captured.get("reason") == "sl_crossed_at_placement"


# ── 2. Pre-flight: long with SL already at/above mark ─────────────────────


def test_preflight_long_sl_at_mark_closes_normally(om):
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="buy", entry=105.0,
                         sl=100.0, tp=115.0)
    captured = {}
    om.close_position = lambda ex, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    ex.create_order.assert_not_called()
    assert captured.get("reason") == "sl_crossed_at_placement"


# ── 3. Pre-flight no-op when SL is on the correct side ────────────────────


def test_preflight_noop_when_sl_correctly_placed_short(om):
    """SL above mark for short = valid. Pre-flight must NOT fire."""
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="sell", entry=99.0,
                         sl=105.0, tp=92.0)  # SL=105 > mark=100 ✓
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    # Exchange should receive both SL and TP create_order calls
    assert ex.create_order.call_count == 2


def test_preflight_noop_when_sl_correctly_placed_long(om):
    """SL below mark for long = valid."""
    ex = _make_exchange("Binance", last=100.0)
    pos = _make_position(om, side="buy", entry=101.0,
                         sl=95.0, tp=110.0)  # SL=95 < mark=100 ✓
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    assert ex.create_order.call_count == 2


# ── 4. Race-rejection: Bybit 110092 ──────────────────────────────────────


def test_bybit_110092_downgrades_to_normal_close(om):
    """If mark moves between pre-flight and exchange view, 110092 surfaces
    in the except handler. It must NOT log EMERGENCY — close normally."""
    ex = _make_exchange("Bybit", last=100.0)  # pre-flight passes
    pos = _make_position(om, side="sell", entry=99.0,
                         sl=105.0, tp=92.0)  # SL > mark ✓ at pre-flight
    # But the SL create_order hits 110092 — simulate mark moved to 105 by then
    ex.create_order.side_effect = Exception(
        'bybit {"retCode":110092,"retMsg":"expect Rising, but '
        'trigger_price[10500000] <= current[10500000]"}'
    )
    captured = {}
    om.close_position = lambda ex, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    assert captured.get("reason") == "sl_crossed_during_placement"


# ── 5. Race-rejection: Bitget 45122 ──────────────────────────────────────


def test_bitget_45122_downgrades_to_normal_close(om):
    ex = _make_exchange("Bitget", last=100.0)
    pos = _make_position(om, side="sell", entry=99.0,
                         sl=105.0, tp=92.0)
    ex.create_order.side_effect = Exception(
        'bitget {"code":"45122","msg":"Short position stop loss price '
        'please > mark price 105"}'
    )
    captured = {}
    om.close_position = lambda ex, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    assert captured.get("reason") == "sl_crossed_during_placement"


# ── 6. Genuine SL failure still goes EMERGENCY ────────────────────────────


def test_unknown_sl_error_still_fail_closed_emergency(om):
    """Errors that are NOT crossed-race must keep the original EMERGENCY +
    fail-closed path. This is the safety guarantee — only the two known
    benign codes are downgraded."""
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="sell", entry=99.0,
                         sl=105.0, tp=92.0)
    ex.create_order.side_effect = Exception(
        'bybit {"retCode":10001,"retMsg":"invalid params: leverage too high"}'
    )
    captured = {}
    om.close_position = lambda ex, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    # Unknown error → original fail-closed reason, not the downgraded one
    assert captured.get("reason") == "sl_placement_failed"


# ── 7. SL reconciliation: re-establish a missing exchange SL (2026-06-20) ──


def test_reconcile_restores_exchange_sl(om):
    """A position left with _sl_failed=True must regain exchange-side SL on the
    next monitor pass (the flag was previously set but never read/cleared)."""
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="buy", entry=101.0, sl=95.0, tp=110.0)
    pos._sl_failed = True
    pos._exchange_sl = False

    def fake_replace(exchange, position):
        position._exchange_sl = True  # simulate a successful re-placement

    om._replace_exchange_sl = fake_replace
    assert om._reconcile_missing_sl(ex, pos) is True
    assert pos._sl_failed is False
    assert pos._exchange_sl is True


def test_reconcile_throttles_repeat_attempts(om):
    """Reconciliation must not hammer the venue: a second call within the
    throttle window does not re-attempt placement."""
    import time
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="buy", entry=101.0, sl=95.0, tp=110.0)
    pos._sl_failed = True
    pos._exchange_sl = False
    pos._sl_retry_ts = time.time()  # a retry just happened

    calls = {"n": 0}
    om._replace_exchange_sl = lambda e, p: calls.__setitem__("n", calls["n"] + 1)
    assert om._reconcile_missing_sl(ex, pos) is False
    assert calls["n"] == 0  # throttled — no re-attempt


def test_reconcile_noop_when_already_protected(om):
    """When the SL is healthy, reconciliation is a no-op and clears any stale
    flag without touching the exchange."""
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="buy", entry=101.0, sl=95.0, tp=110.0)
    pos._sl_failed = True
    pos._exchange_sl = True  # already attached
    calls = {"n": 0}
    om._replace_exchange_sl = lambda e, p: calls.__setitem__("n", calls["n"] + 1)
    assert om._reconcile_missing_sl(ex, pos) is True
    assert pos._sl_failed is False
    assert calls["n"] == 0


def test_reconcile_noop_in_paper(om):
    """Paper mode has no exchange orders to reconcile."""
    ex = _make_exchange("Bybit", last=100.0)
    pos = _make_position(om, side="buy", entry=101.0, sl=95.0, tp=110.0)
    pos._sl_failed = True
    pos._exchange_sl = False
    om.dry_run = True
    calls = {"n": 0}
    om._replace_exchange_sl = lambda e, p: calls.__setitem__("n", calls["n"] + 1)
    assert om._reconcile_missing_sl(ex, pos) is True
    assert calls["n"] == 0


# ── 2026-07-07: empty create_order return = failure, never success ──────────
# A not-connected BaseExchange client returns {} WITHOUT raising. The old code
# unconditionally set pos._exchange_sl/_exchange_tp = True, marking naked
# positions as exchange-protected and suppressing the local SL fallback.


def test_sl_empty_create_order_result_fails_closed(om):
    ex = _make_exchange("Bybit", last=100.0)
    ex.create_order.return_value = {}  # not-connected client signature
    pos = _make_position(om, side="buy", entry=100.0, sl=95.0, tp=110.0)
    captured = {}
    om.close_position = lambda ex_, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    assert getattr(pos, "_exchange_sl", False) is not True
    assert getattr(pos, "_sl_failed", False) is True
    assert captured.get("reason") == "sl_placement_failed"


def test_tp_empty_create_order_result_falls_back_to_local(om):
    ex = _make_exchange("Bybit", last=100.0)
    ex.create_order.side_effect = [{"id": "sl-ok"}, {}]  # SL ok, TP empty
    pos = _make_position(om, side="buy", entry=100.0, sl=95.0, tp=110.0)
    captured = {}
    om.close_position = lambda ex_, p, reason, **kw: captured.update(reason=reason)
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    assert pos._exchange_sl is True
    assert pos._exchange_tp is False  # local monitoring covers the TP
    assert "reason" not in captured  # position NOT closed for a failed TP


# ── 2026-07-07: paper stop fills can never beat their trigger level ─────────


def test_cap_stop_fill_never_beats_trigger():
    from core.order_manager import OrderManager
    # Closing a LONG (sell): a recovered book ABOVE the trigger caps DOWN.
    assert OrderManager._cap_stop_fill(96.0, 95.0, "sell") == 95.0
    # A genuinely worse fill (gap through the stop) is kept as-is.
    assert OrderManager._cap_stop_fill(94.5, 95.0, "sell") == 94.5
    # Closing a SHORT (buy): a recovered book BELOW the trigger caps UP.
    assert OrderManager._cap_stop_fill(104.0, 105.0, "buy") == 105.0
    assert OrderManager._cap_stop_fill(105.8, 105.0, "buy") == 105.8
    # Degenerate inputs pass through untouched.
    assert OrderManager._cap_stop_fill(96.0, 0.0, "sell") == 96.0
