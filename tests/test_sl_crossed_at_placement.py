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
