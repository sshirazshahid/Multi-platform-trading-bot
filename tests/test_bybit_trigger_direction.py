"""Test that Bybit SL/TP placement passes the required triggerDirection.

ccxt 4.5.46 bybit v5 raises ArgumentsRequired on stop/trigger orders when
both triggerPrice and stopLossPrice are set without an explicit
triggerDirection. `_build_conditional` must supply:

  long SL  → "below"   long TP  → "above"
  short SL → "above"   short TP → "below"

Bitget must NOT get triggerDirection (ccxt bitget path doesn't use it).
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
def om(monkeypatch) -> OrderManager:
    """Minimal OrderManager. Notifier stubbed; dry_run forced False so the
    live SL-placement branch runs.

    BotEngine wires `tracker.on_close = order_mgr._finalize_close` at
    startup so every close path (normal exit, ghost-sync, fail-closed)
    flows through the warehouse / risk / notifier pipeline. The fixture
    instantiates the components directly, so we replicate that wire
    here — without it the fail-closed test sees tracker.close() that
    never propagates to the warehouse and asserts a stale OPEN row.
    """
    notifier = MagicMock()
    tracker = PositionTracker()
    risk = RiskManager()
    manager = OrderManager(tracker, risk, notifier)
    manager.dry_run = False  # exercise the real exchange path
    tracker.on_close = manager._finalize_close
    return manager


def _make_exchange(name: str) -> MagicMock:
    """Mock exchange that passes pre-flight checks but records create_order.

    2026-05-25 — must provide a realistic fetch_ticker. The SL pre-flight
    added 2026-05-21 (order_manager.py:1055, project_breakeven_fail_closed_
    kills_winners_2026_04_25) reads ticker["last"] and closes instead of
    placing if the SL is already on the wrong side of mark. An unconfigured
    MagicMock's __float__ defaults to 1.0, so a buy-side SL=95 read as
    "crossed" (95 >= 1) and the position was closed before any SL/TP order
    was recorded. Positions here use entry=100, so mark=100 keeps SL/TP on
    the correct sides (buy SL 95 < 100 < TP 110; sell TP 90 < 100 < SL 105).
    """
    ex = MagicMock()
    ex.name = name
    ex.round_price.side_effect = lambda sym, p: p
    ex.round_quantity.side_effect = lambda sym, q: q
    ex.fetch_ticker.return_value = {"last": 100.0, "close": 100.0}
    return ex


def _make_position(side: str) -> Position:
    return Position(
        id="test-" + side,
        exchange="bybit",
        symbol="AAVE/USDT:USDT",
        side=side,
        market_type="futures",
        strategy="test",
        entry_price=100.0,
        size=0.1,
        stop_loss=95.0 if side == "buy" else 105.0,
        take_profit=110.0 if side == "buy" else 90.0,
        leverage=3,
    )


# --- Bybit: SL direction ----------------------------------------------------


def test_bybit_long_sl_triggers_below(om):
    ex = _make_exchange("Bybit")
    pos = _make_position("buy")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    # First create_order call is the SL; second is the TP
    sl_call = ex.create_order.call_args_list[0]
    sl_params = sl_call.args[5] if len(sl_call.args) > 5 else sl_call.kwargs["params"]
    assert sl_params["triggerDirection"] == "below"


def test_bybit_short_sl_triggers_above(om):
    ex = _make_exchange("Bybit")
    pos = _make_position("sell")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    sl_call = ex.create_order.call_args_list[0]
    sl_params = sl_call.args[5] if len(sl_call.args) > 5 else sl_call.kwargs["params"]
    assert sl_params["triggerDirection"] == "above"


# --- Bybit: TP direction ----------------------------------------------------


def test_bybit_long_tp_triggers_above(om):
    ex = _make_exchange("Bybit")
    pos = _make_position("buy")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    tp_call = ex.create_order.call_args_list[1]
    tp_params = tp_call.args[5] if len(tp_call.args) > 5 else tp_call.kwargs["params"]
    assert tp_params["triggerDirection"] == "above"


def test_bybit_short_tp_triggers_below(om):
    ex = _make_exchange("Bybit")
    pos = _make_position("sell")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    tp_call = ex.create_order.call_args_list[1]
    tp_params = tp_call.args[5] if len(tp_call.args) > 5 else tp_call.kwargs["params"]
    assert tp_params["triggerDirection"] == "below"


# --- Bitget: must NOT receive triggerDirection ------------------------------


def test_bitget_has_no_trigger_direction(om):
    ex = _make_exchange("Bitget")
    pos = _make_position("buy")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    sl_call = ex.create_order.call_args_list[0]
    sl_params = sl_call.args[5] if len(sl_call.args) > 5 else sl_call.kwargs["params"]
    assert "triggerDirection" not in sl_params


# --- Binance: native STOP_MARKET path, unaffected ---------------------------


def test_binance_uses_native_stop_market(om):
    ex = _make_exchange("Binance")
    pos = _make_position("buy")
    om._place_exchange_sl_tp(ex, pos, pos.stop_loss, pos.take_profit,
                             pos.side, pos.symbol, pos.size, pos.market_type)
    sl_call = ex.create_order.call_args_list[0]
    order_type = sl_call.args[1]
    assert order_type == "STOP_MARKET"
    sl_params = sl_call.args[5] if len(sl_call.args) > 5 else sl_call.kwargs["params"]
    assert "triggerDirection" not in sl_params


# --- Bitget: params MUST be mutually exclusive ------------------------------
# ccxt rejects createOrder() when triggerPrice + stopLossPrice/takeProfitPrice
# are both set: "params can only contain one of triggerPrice, stopLossPrice,
# takeProfitPrice, trailingPercent". Exercise the pure builder directly.

from core.order_manager import build_sl_tp_order_params  # noqa: E402


def test_bitget_sl_params_only_stop_loss_price():
    _, params = build_sl_tp_order_params(
        "bitget", side="buy", oneway=True, trigger_price=95.0, is_sl=True)
    assert "stopLossPrice" in params
    assert "triggerPrice" not in params
    assert "takeProfitPrice" not in params


def test_bitget_tp_params_only_take_profit_price():
    _, params = build_sl_tp_order_params(
        "bitget", side="buy", oneway=True, trigger_price=110.0, is_sl=False)
    assert "takeProfitPrice" in params
    assert "triggerPrice" not in params
    assert "stopLossPrice" not in params


def test_bitget_sl_uses_market_order_type():
    order_type, _ = build_sl_tp_order_params(
        "bitget", side="sell", oneway=True, trigger_price=105.0, is_sl=True)
    assert order_type == "market"


def test_bitget_oneway_sets_reduce_only():
    _, params = build_sl_tp_order_params(
        "bitget", side="buy", oneway=True, trigger_price=95.0, is_sl=True)
    assert params.get("reduceOnly") is True
    assert "positionSide" not in params


def test_bitget_hedge_mode_uses_position_side():
    _, params = build_sl_tp_order_params(
        "bitget", side="sell", oneway=False, trigger_price=105.0, is_sl=True)
    assert params.get("positionSide") == "SHORT"


def test_bybit_builder_keeps_trigger_direction():
    _, sl = build_sl_tp_order_params(
        "bybit", side="buy", oneway=False, trigger_price=95.0, is_sl=True)
    assert sl["triggerDirection"] == "below"
    assert sl["stopLossPrice"] == 95.0
    assert sl["triggerPrice"] == 95.0  # intentional on Bybit path
    _, tp = build_sl_tp_order_params(
        "bybit", side="sell", oneway=False, trigger_price=90.0, is_sl=False)
    assert tp["triggerDirection"] == "below"
    assert tp["takeProfitPrice"] == 90.0


def test_binance_builder_uses_stop_market():
    order_type, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=95.0, is_sl=True)
    assert order_type == "STOP_MARKET"
    # C3 (2026-07-08): SLTP_TRIGGER_MARK_PRICE defaults ON, adding
    # workingType — flag-off byte-identity is pinned in
    # test_mark_price_trigger.py::test_binance_flag_off_is_byte_identical_legacy
    assert params == {"stopPrice": 95.0, "reduceOnly": True,
                      "workingType": "MARK_PRICE"}
    order_type, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=110.0, is_sl=False)
    assert order_type == "TAKE_PROFIT_MARKET"


# --- Regression: warehouse ordering when SL placement fails ------------------
# 2026-04-20 bug: record_trade_open used to run in bot_engine._execute_open
# AFTER open_position returned. _place_exchange_sl_tp's fail-closed path
# calls close_position → trade_id_by_key, which returned None because the
# row didn't exist yet, silently dropping the close. 14 OPEN rows piled up
# in data/warehouse.sqlite on CONTROLLED_LIVE. Fix moved record_trade_open
# INTO open_position, before _place_exchange_sl_tp. This test locks that
# ordering in: if a future refactor moves the warehouse write back out,
# this test will fail.


def test_sl_failure_persists_warehouse_close(om, monkeypatch):
    """open_position must leave a CLOSED warehouse row — with
    exit_reason='sl_placement_failed' — when exchange SL placement fails.

    Exercises the full entry → SL-fails → fail-closed → record_trade_close
    path. Proves record_trade_open runs BEFORE _place_exchange_sl_tp so
    trade_id_by_key can locate the row inside close_position.
    """
    import core.warehouse
    core.warehouse._default = None  # fresh singleton under chdir'd tmp_path

    monkeypatch.setattr(
        om.executor, "check_spread",
        lambda *a, **kw: {"ok": True, "spread_pct": 0.001,
                          "bid": 99.5, "ask": 100.0, "mid": 99.75})
    monkeypatch.setattr(om.executor, "should_use_twap", lambda *a, **kw: False)
    monkeypatch.setattr(
        om.executor, "execute_limit_with_fallback",
        lambda *a, **kw: {"id": "live-order-1", "average": 100.0, "price": 100.0})
    monkeypatch.setattr(om, "_verify_order_on_exchange", lambda *a, **kw: None)
    monkeypatch.setattr(om, "_check_price_band", lambda *a, **kw: True)
    monkeypatch.setattr(om, "auto_transfer_for_trade", lambda *a, **kw: True)
    monkeypatch.setattr(om.kelly, "should_block_trade", lambda *a, **kw: (False, ""))

    ex = MagicMock()
    ex.name = "Bitget"
    ex._is_oneway = True
    ex.round_price.side_effect = lambda sym, p: p
    ex.round_quantity.side_effect = lambda sym, q: q
    ex.fetch_ticker.return_value = {"last": 100.0, "close": 100.0}
    ex.get_min_order_size.return_value = 0.001
    ex.set_leverage.return_value = True
    ex.create_order.side_effect = [
        Exception(
            "bitget createOrder() params can only contain one of "
            "triggerPrice, stopLossPrice, takeProfitPrice, trailingPercent"),
        {"id": "close-order-1", "average": 100.0, "price": 100.0},
    ]
    om._oneway_mode.add("bitget")

    pos = om.open_position(
        exchange=ex,
        symbol="AAVE/USDT:USDT",
        side="buy",
        market_type="futures",
        strategy="systematic_v3_1",
        size=0.1,
        sl=95.0,
        tp=110.0,
        leverage=3,
    )
    assert pos is not None, "open_position returned None before SL placement"

    from core.warehouse import get_warehouse
    wh = get_warehouse()
    rows = wh.query(
        "SELECT status, exit_reason FROM trades "
        "WHERE symbol='AAVE/USDT:USDT'")
    assert len(rows) == 1, f"Expected 1 trade row, got {len(rows)}"
    row = rows[0]
    assert row["status"] == "CLOSED", (
        "Trade row still OPEN — regression: record_trade_open was likely "
        "moved back out of open_position, so close_position.trade_id_by_key "
        "returns None and the close is silently lost.")
    assert row["exit_reason"] == "sl_placement_failed", (
        f"Expected exit_reason='sl_placement_failed', got {row['exit_reason']!r}")
