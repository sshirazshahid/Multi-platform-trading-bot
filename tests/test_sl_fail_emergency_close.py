"""Emergency-close on failed SL placement (Codex order_router port, 2026-07-12).

When exchange-side SL placement GENUINELY fails after the retry ladder
(BaseExchange.create_order's internal attempts, incl. the Bybit-110072
clientOrderId regen and Binance -4120 routing), the position must be closed
immediately through the NORMAL close path — close_position → tracker.close →
on_close hooks (the Apr-26 ghost-close lesson: every close path hits all
safety hooks) — with exit_reason='sl_placement_failed', and only THEN is the
alert sent, reporting "closed fail-safe" instead of a naked position.

Config flag SL_FAIL_EMERGENCY_CLOSE_ENABLED (default TRUE — charter §2
Stop-Loss Guardian) exists only as an operator escape hatch: when false the
old behavior applies (_sl_failed=True + alert + local monitoring/reconcile).

The emergency path must NOT fire for:
  * placements that succeed (incl. after create_order's internal retries);
  * crossed-at-placement race rejects (110092/45122) — those close normally
    with their own reasons.
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
    """Minimal OrderManager; dry_run forced False so the live SL-placement
    branch runs. tracker.on_close mirrors the BotEngine wire."""
    notifier = MagicMock()
    tracker = PositionTracker()
    risk = RiskManager()
    manager = OrderManager(tracker, risk, notifier)
    manager.dry_run = False
    tracker.on_close = manager._finalize_close
    return manager


def _make_exchange(name: str = "Bybit") -> MagicMock:
    ex = MagicMock()
    ex.name = name
    ex.round_price.side_effect = lambda sym, p, market_type=None: p
    ex.round_quantity.side_effect = lambda sym, q, market_type=None: q
    # mark=100 keeps buy SL 95 / TP 110 on the correct sides (no crossed
    # pre-flight short-circuit).
    ex.fetch_ticker.return_value = {"last": 100.0, "close": 100.0}
    return ex


def _make_position(side: str = "buy") -> Position:
    return Position(
        id="slfail-" + side,
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


def _place(om, ex, pos):
    om._place_exchange_sl_tp(
        ex, pos, pos.stop_loss, pos.take_profit, pos.side, pos.symbol, pos.size, pos.market_type
    )


# --- default flag: close fail-safe through the normal path -------------------


def test_sl_fail_closes_with_reason_sl_placement_failed(om, monkeypatch):
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.side_effect = Exception("venue rejected the stop order")
    close_spy = MagicMock()
    monkeypatch.setattr(om, "close_position", close_spy)

    _place(om, ex, pos)

    close_spy.assert_called_once()
    args, kwargs = close_spy.call_args
    assert kwargs.get("reason") == "sl_placement_failed"
    assert args[1] is pos
    assert getattr(pos, "_sl_failed", False) is True


def test_sl_fail_alert_sent_after_close_and_reports_fail_safe(om, monkeypatch):
    """Ordering: the position is closed FIRST, then the alert goes out —
    and the alert reports the position was closed fail-safe, not naked."""
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.side_effect = Exception("venue rejected the stop order")
    events = []
    monkeypatch.setattr(
        om, "close_position", lambda *a, **k: events.append(("close", k.get("reason")))
    )
    om.notifier.send.side_effect = lambda msg: events.append(("alert", msg))

    _place(om, ex, pos)

    assert [e[0] for e in events] == ["close", "alert"]
    alert = events[1][1]
    assert "closed fail-safe" in alert
    assert "AAVE/USDT:USDT" in alert


def test_sl_fail_close_failure_alerts_naked_manual_intervention(om, monkeypatch):
    """If the fail-safe close itself fails, the alert must say NAKED +
    MANUAL INTERVENTION (never a false 'closed fail-safe')."""
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.side_effect = Exception("venue rejected the stop order")
    monkeypatch.setattr(
        om, "close_position", MagicMock(side_effect=RuntimeError("close also failed"))
    )
    _place(om, ex, pos)

    sent = " | ".join(str(c.args[0]) for c in om.notifier.send.call_args_list)
    assert "NAKED" in sent
    assert "MANUAL INTERVENTION" in sent
    assert "closed fail-safe" not in sent


def test_sl_failure_http_debug_evidence_redacts_credentials(om, monkeypatch):
    from loguru import logger

    ex = _make_exchange()
    raw = MagicMock()
    raw.options = {"defaultType": "swap"}
    raw.last_request_url = (
        "https://api.exchange.test/order?symbol=AAVEUSDT&"
        "apiKey=API_KEY_SHOULD_NOT_LEAK&signature=SIGNATURE_SHOULD_NOT_LEAK"
    )
    raw.last_request_body = (
        "symbol=AAVEUSDT&secret=SECRET_SHOULD_NOT_LEAK&"
        "passphrase=PASSPHRASE_SHOULD_NOT_LEAK"
    )
    raw.last_request_headers = {
        "Authorization": "Bearer TOKEN_SHOULD_NOT_LEAK",
        "X-BAPI-API-KEY": "HEADER_KEY_SHOULD_NOT_LEAK",
    }
    raw.last_http_response = '{"retCode":10001,"apiKey":"ECHO_SHOULD_NOT_LEAK"}'
    raw.last_response_headers = {"trace-id": "safe-trace"}
    ex.exchange = raw
    ex.create_order.side_effect = Exception("venue rejected the stop order")
    monkeypatch.setattr(om, "close_position", MagicMock())

    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG")
    try:
        _place(om, ex, _make_position())
    finally:
        logger.remove(sink)

    rendered = "\n".join(str(message) for message in messages)
    for secret in (
        "API_KEY_SHOULD_NOT_LEAK",
        "SIGNATURE_SHOULD_NOT_LEAK",
        "SECRET_SHOULD_NOT_LEAK",
        "PASSPHRASE_SHOULD_NOT_LEAK",
        "TOKEN_SHOULD_NOT_LEAK",
        "HEADER_KEY_SHOULD_NOT_LEAK",
        "ECHO_SHOULD_NOT_LEAK",
    ):
        assert secret not in rendered
    assert "AAVEUSDT" in rendered


# --- operator escape hatch: flag off restores the old behavior ---------------


def test_flag_off_keeps_position_open_and_alerts_unprotected(om, monkeypatch):
    # Resolve the module at TEST time, not module-import time: the root
    # conftest evicts/replaces sys.modules['config'] between skill test
    # packages, so the handler's late `import config` can see a different
    # module object than this file's top-level import under the full suite.
    import importlib

    cfg = importlib.import_module("config")
    monkeypatch.setattr(cfg, "SL_FAIL_EMERGENCY_CLOSE_ENABLED", False, raising=False)
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.side_effect = Exception("venue rejected the stop order")
    close_spy = MagicMock()
    monkeypatch.setattr(om, "close_position", close_spy)

    _place(om, ex, pos)

    close_spy.assert_not_called()
    assert getattr(pos, "_sl_failed", False) is True
    sent = " | ".join(str(c.args[0]) for c in om.notifier.send.call_args_list)
    assert "WITHOUT exchange-side protection" in sent


# --- must NOT fire on success or on crossed-race rejects ---------------------


def test_successful_placement_never_triggers_emergency_close(om, monkeypatch):
    """create_order returning a valid order (incl. after its INTERNAL retry
    ladder recovered a transient error) must not fire the emergency close."""
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.return_value = {"id": "sl-1"}
    close_spy = MagicMock()
    monkeypatch.setattr(om, "close_position", close_spy)

    _place(om, ex, pos)

    close_spy.assert_not_called()
    assert getattr(pos, "_exchange_sl", False) is True
    om.notifier.send.assert_not_called()


def test_crossed_race_closes_normally_not_via_emergency_path(om, monkeypatch):
    """Bybit 110092 (SL on wrong side of mark) is a would-have-triggered
    race — it closes with its own reason and sends NO emergency alert."""
    ex = _make_exchange()
    pos = _make_position()
    ex.create_order.side_effect = Exception("bybit 110092 wrong side")
    close_spy = MagicMock()
    monkeypatch.setattr(om, "close_position", close_spy)

    _place(om, ex, pos)

    close_spy.assert_called_once()
    _, kwargs = close_spy.call_args
    assert kwargs.get("reason") == "sl_crossed_during_placement"
    om.notifier.send.assert_not_called()


# --- the close is the NORMAL path: tracker.close fires on_close hooks --------


def test_fail_safe_close_fires_on_close_hooks(om):
    """Apr-26 ghost-close lesson: the emergency close must flow through
    close_position → tracker.close → on_close, hitting every safety hook."""
    import core.warehouse

    core.warehouse._default = None  # fresh singleton under chdir'd tmp_path

    ex = _make_exchange("Bitget")
    om._oneway_mode.add("bitget")
    pos = _make_position()
    om.tracker.add(pos)
    hook_calls = []
    _orig = om.tracker.on_close

    def _spy(p, exit_price, reason):
        hook_calls.append((p.id, reason))
        return _orig(p, exit_price, reason)

    om.tracker.on_close = _spy
    ex.create_order.side_effect = [
        Exception("venue rejected the stop order"),  # SL leg fails
        {"id": "close-1", "average": 100.0, "price": 100.0},  # market close
    ]

    _place(om, ex, pos)

    assert hook_calls, "on_close hook never fired for the fail-safe close"
    assert hook_calls[0] == (pos.id, "sl_placement_failed")
