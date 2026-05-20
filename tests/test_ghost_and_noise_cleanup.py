"""Tests for 2026-05-20 ghost-cleanup + age-aware SL + small-TP capture spec.

Covers 5 areas:
  1. Ghost path accuracy (24h window, two-pass reconcile, mark_price fallback)
  2. Log noise cleanup (demote 5 informational warnings)
  3. Reliability hardening (_safe_cancel_order swallows 110001/40034)
  4. Age-aware SL->BE tightening (fires at age>=60min, pnl in [0%, 1%))
  5. Deterministic small-TP capture (fires at age>=30min, pnl in [1%, 2%))
"""
from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

# Import once at module load so loguru sinks set by tests below survive
# downstream re-imports inside the test bodies.
from core import position_tracker as pt  # noqa: E402


@pytest.fixture
def caplog(caplog):
    """Bridge loguru -> stdlib so pytest's caplog can capture log lines.

    Loguru bypasses stdlib logging entirely. Add a sink that re-emits each
    loguru record via a stdlib logger which propagates to root.
    """
    from loguru import logger as loguru_logger

    py_logger = logging.getLogger("tests.loguru_bridge")
    py_logger.setLevel(logging.DEBUG)
    py_logger.propagate = True

    def _sink(message):
        record = message.record
        py_level = getattr(logging, record["level"].name, logging.INFO)
        py_logger.log(py_level, record["message"])

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    caplog.set_level(logging.DEBUG, logger="tests.loguru_bridge")
    yield caplog
    loguru_logger.remove(handler_id)


# ---------------------------------------------------------------------------
# AREA 1 - Ghost path accuracy
# ---------------------------------------------------------------------------


def test_ghost_ledger_lookup_window_is_24h():
    """The since_ms argument passed to fetch_closed_pnl must reflect 24h, not 6h."""
    from config import GHOST_LEDGER_WINDOW_H

    assert GHOST_LEDGER_WINDOW_H == 24, (
        "Area 1 requires ledger lookup window of 24h; "
        f"config.GHOST_LEDGER_WINDOW_H={GHOST_LEDGER_WINDOW_H}"
    )


def test_ghost_sync_pending_then_reconciled(tmp_path, monkeypatch):
    """First sync returns ghost_sync (no ledger record); second sync finds
    the ledger record and upgrades the warehouse exit price to the real fill."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-GHOST-001",
        exchange="bybit",
        symbol="BNB/USDT:USDT",
        side="sell",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=640.0,
        size=0.1,
        stop_loss=645.0,
        take_profit=620.0,
        paper_trade=False,
    )
    p.open_time = time.time() - 3600  # opened 1h ago
    tracker._open[p.id] = p

    # First sync: ledger has NO matching record -> ghost_sync expected
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = []  # empty ledger
    fake_ex.fetch_ticker.return_value = {"last": 639.5, "info": {"markPrice": "639.2"}}
    fake_ex.fetch_positions.return_value = []  # exchange-side: no positions
    fake_ex.name = "bybit"

    # First pass: no ledger, position parked in _pending_ghost_reconcile (per Area 1 §2.1 spec)
    tracker.sync_with_exchanges({"bybit": fake_ex})  # exchange has no position
    assert "TEST-GHOST-001" in tracker._pending_ghost_reconcile

    # Second sync 15s later: ledger now has the fill record.
    # NOTE on test fixture: match_ghost_ledger_record (line 47) reads
    # `close_time` in unix SECONDS — see binance_client.py:342, bybit_client.py:444,
    # bitget_client.py:429 which all divide by 1000. The original task spec used
    # `"ts": time.time() * 1000` which would not match (wrong key, wrong units);
    # this test record uses the exchange-client contract.
    fake_ex.fetch_closed_pnl.return_value = [
        {
            "symbol": "BNB/USDT:USDT",
            "side": "sell",
            "exit_price": 644.5,
            "realized_pnl": -0.45,
            "close_time": time.time(),
        }
    ]
    tracker.sync_with_exchanges({"bybit": fake_ex})

    # After second pass, the position is in _closed with ghost_reconciled
    closed = [c for c in tracker._closed if c.id == "TEST-GHOST-001"]
    assert len(closed) == 1, (
        f"expected position to be moved to _closed; "
        f"_pending={list(tracker._pending_ghost_reconcile.keys())} "
        f"_closed_ids={[c.id for c in tracker._closed]}"
    )
    assert closed[0].close_reason == "ghost_reconciled", (
        f"expected close_reason ghost_reconciled, got {closed[0].close_reason}"
    )
    assert abs(closed[0].exit_price - 644.5) < 0.01, (
        f"expected exit_price 644.5 (from ledger), got {closed[0].exit_price}"
    )


def test_ghost_fallback_uses_mark_price(tmp_path, monkeypatch):
    """When no ledger record exists, the fallback close price is taken from
    ticker.info.markPrice, NOT ticker.last (Area 1 §2.1 mark_price fallback)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    # Disable two-pass so the first sync finalizes immediately
    monkeypatch.setattr("config.GHOST_PENDING_REQUEUE", False)

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-GHOST-002",
        exchange="binance",
        symbol="AAVE/USDT:USDT",
        side="sell",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=88.0,
        size=0.5,
        stop_loss=89.0,
        take_profit=85.0,
        paper_trade=False,
    )
    p.open_time = time.time() - 3600
    tracker._open[p.id] = p

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = []  # no ledger
    fake_ex.fetch_ticker.return_value = {
        "last": 87.50,                      # last-trade price
        "info": {"markPrice": "87.20"},     # exchange mark price (preferred)
    }
    fake_ex.fetch_positions.return_value = []
    fake_ex.name = "binance"

    tracker.sync_with_exchanges({"binance": fake_ex})

    closed = [c for c in tracker._closed if c.id == "TEST-GHOST-002"]
    assert len(closed) == 1
    # The Area 1 fix routes the fallback through mark_price (87.20), NOT
    # ticker.last (87.50). The 0.30 difference matters for PnL accuracy.
    assert abs(closed[0].exit_price - 87.20) < 0.01, (
        f"expected exit_price 87.20 (mark_price), got {closed[0].exit_price}"
    )


# ---------------------------------------------------------------------------
# AREA 2 — Log noise cleanup
# ---------------------------------------------------------------------------


def test_ghost_detected_at_info_level_when_reconciled(tmp_path, monkeypatch, caplog):
    """When a ghost reconciles cleanly via the ledger path, the price-source
    log line should be at INFO level, not WARNING (Area 2 demotion)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-AREA2-001",
        exchange="bybit",
        symbol="BCH/USDT:USDT",
        side="sell",
        market_type="futures",
        strategy="claude_portfolio",
        entry_price=370.0,
        size=0.05,
        stop_loss=374.0,
        take_profit=360.0,
        paper_trade=False,
    )
    p.open_time = time.time() - 3600
    tracker._open[p.id] = p

    fake_ex = MagicMock()
    fake_ex.fetch_positions.return_value = []  # position no longer on exchange
    fake_ex.fetch_closed_pnl.return_value = [
        {
            "symbol": "BCH/USDT:USDT",
            "side": "sell",
            "exit_price": 369.5,
            "realized_pnl": 0.025,
            "close_time": time.time(),
        }
    ]
    fake_ex.name = "bybit"

    caplog.clear()
    tracker.sync_with_exchanges({"bybit": fake_ex})

    # The "GHOST close price source=" log line must be at INFO when reconciled.
    price_source_records = [
        r for r in caplog.records
        if "GHOST close price source" in r.message
    ]
    assert price_source_records, "expected the GHOST close price source log line"
    assert price_source_records[0].levelname == "INFO", (
        f"expected INFO when reconciled, got {price_source_records[0].levelname}"
    )


def test_bybit_110001_cancel_logged_at_debug(monkeypatch, caplog):
    """Bybit 110001 'order not exists or too late to cancel' is the race-
    condition expected case (the order already filled). It should log at
    DEBUG, NOT ERROR, and return {} cleanly (Area 2 demotion + Area 3 swallow)."""
    import ccxt
    from exchanges import base as base_mod

    # Build a tiny fake BaseExchange that throws ccxt-style 110001
    class _FakeExchange:
        id = "bybit"
        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bybit {"retCode":110001,"retMsg":"order not exists or too late to cancel"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bybit"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeExchange()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    caplog.clear()
    # cancel_order is bound on the class; call the real one with our fake
    result = base_mod.BaseExchange.cancel_order(
        fake_be, "ABC-123", "BNB/USDT:USDT", "futures"
    )

    # Result should be a clean {} (no exception leaks out)
    assert result == {}, f"expected swallowed cancel to return {{}}, got {result}"
    # No ERROR-level log allowed for known-race 110001 (DEBUG/INFO are fine)
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "cancel_order" in r.message
    ]
    assert not error_records, (
        f"110001 cancel race must not log at ERROR. Found: "
        f"{[r.message for r in error_records]}"
    )


# ---------------------------------------------------------------------------
# AREA 3 — Reliability hardening (_safe_cancel_order)
# ---------------------------------------------------------------------------


def test_safe_cancel_order_swallows_110001(caplog):
    """Bybit 110001 'order not exists or too late to cancel' returns {} cleanly."""
    import ccxt
    from exchanges import base as base_mod

    class _FakeBybit:
        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bybit {"retCode":110001,"retMsg":"order not exists or too late to cancel"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bybit"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeBybit()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    result = base_mod.BaseExchange.cancel_order(
        fake_be, "ABC-123", "BNB/USDT:USDT", "futures"
    )

    assert result == {}, f"expected swallowed cancel to return empty dict, got {result}"


def test_safe_cancel_order_swallows_bitget_40034(caplog):
    """Bitget 40034 'order does not exist' race — same swallow semantics."""
    import ccxt
    from exchanges import base as base_mod

    class _FakeBitget:
        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bitget {"code":"40034","msg":"order does not exist"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bitget"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeBitget()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    result = base_mod.BaseExchange.cancel_order(
        fake_be, "XYZ-987", "AVAX/USDT:USDT", "futures"
    )
    assert result == {}


def test_safe_cancel_order_reraises_unknown_errors(caplog):
    """Unknown error classes (RuntimeError, ConnectionError, generic) must
    still be logged at ERROR and not silently swallowed."""
    from exchanges import base as base_mod

    class _FakeExchange:
        def cancel_order(self, order_id, symbol, params=None):
            raise RuntimeError("boom: something genuinely unexpected")

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "binance"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeExchange()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    caplog.clear()
    result = base_mod.BaseExchange.cancel_order(
        fake_be, "DEF-456", "ETH/USDT:USDT", "futures"
    )
    assert result == {}  # caller still gets a safe {}; behavior preserved
    # But the unknown error MUST surface at ERROR (visibility for real bugs)
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "boom" in r.message
    ]
    assert error_records, (
        f"unknown errors must still log at ERROR; got: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# AREA 4 — Age-aware SL→breakeven tightening
# ---------------------------------------------------------------------------


def _make_long_position(age_min: float, entry: float, current_mark: float):
    """Build a minimal Position-like mock for Area 4/5 unit tests."""
    p = MagicMock()
    p.id = f"TEST-AREA4-{age_min}-{entry}-{current_mark}"
    p.exchange = "binance"
    p.symbol = "ARB/USDT:USDT"
    p.side = "buy"
    p.size = 50.0
    p.entry_price = entry
    p.stop_loss = entry * (1 - 0.015)   # 1.5% below entry (default SL)
    p.take_profit = entry * 1.03
    p.market_type = "futures"
    p.leverage = 2
    p.duration_minutes = age_min
    return p


def _make_short_position(age_min: float, entry: float, current_mark: float):
    p = MagicMock()
    p.id = f"TEST-AREA4-S-{age_min}-{entry}-{current_mark}"
    p.exchange = "bybit"
    p.symbol = "DOGE/USDT:USDT"
    p.side = "sell"
    p.size = 100.0
    p.entry_price = entry
    p.stop_loss = entry * (1 + 0.015)
    p.take_profit = entry * 0.97
    p.market_type = "futures"
    p.leverage = 2
    p.duration_minutes = age_min
    return p


def test_age_aware_tighten_fires_at_60min_in_profit(monkeypatch):
    """Age 70min, pnl +0.8%, SL far from entry → _replace_exchange_sl called
    with the breakeven price (entry × (1 + 2·fee + 0.0005))."""
    from core import bot_engine
    from config import FEE

    # Long position +0.8% in profit at age 70min
    entry = 1.00
    current_mark = entry * 1.008  # +0.8%
    p = _make_long_position(age_min=70.0, entry=entry, current_mark=current_mark)

    # Wire up a minimal BotEngine-like object with just the method under test
    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng._replace_exchange_sl = MagicMock(return_value=True)
    eng._exchange_for = MagicMock(return_value=MagicMock())

    # Mock pnl_frac computation: return +0.008 (+0.8%)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)

    # Run the method under test (unbound; bind manually)
    fired = bot_engine.BotEngine._maybe_tighten_aged_position(eng, p)

    assert fired is True, "expected age-aware tightener to fire"
    eng._replace_exchange_sl.assert_called_once()
    # Verify the new SL is the breakeven price for a long
    call_args = eng._replace_exchange_sl.call_args
    new_sl_arg = call_args.args[2]
    fee_rate = FEE.get("futures_taker", 0.0005)
    expected = entry * (1 + 2 * fee_rate + 0.0005)
    assert abs(new_sl_arg - expected) < 1e-6, (
        f"expected new SL {expected:.6f}, got {new_sl_arg:.6f}"
    )


def test_age_aware_tighten_no_op_outside_band(monkeypatch):
    """No-op conditions: age too young, in loss, above 2% profit, or SL already at/past BE."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng._replace_exchange_sl = MagicMock(return_value=True)
    eng._exchange_for = MagicMock(return_value=MagicMock())

    # (1) age 45min — too young
    p1 = _make_long_position(age_min=45.0, entry=1.0, current_mark=1.008)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p1) is False

    # (2) pnl -0.5% — in loss
    p2 = _make_long_position(age_min=70.0, entry=1.0, current_mark=0.995)
    eng._unrealized_pnl_frac = MagicMock(return_value=-0.005)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p2) is False

    # (3) pnl +2.5% — above trailing-stop activation zone
    p3 = _make_long_position(age_min=70.0, entry=1.0, current_mark=1.025)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.025)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p3) is False

    # (4) SL already tighter than breakeven — refuse to widen
    p4 = _make_long_position(age_min=70.0, entry=1.0, current_mark=1.008)
    p4.stop_loss = 1.005  # already past breakeven; tightening would WIDEN
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p4) is False

    # No tightener calls in any of the above
    assert eng._replace_exchange_sl.call_count == 0


# ---------------------------------------------------------------------------
# AREA 5 — Deterministic small-TP capture
# ---------------------------------------------------------------------------


def test_auto_small_tp_fires_at_1pct_after_30min(monkeypatch):
    """Futures position age 35min pnl +1.2% → close_position called with
    reason='auto_small_tp_1pct'."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.012)  # +1.2%
    monkeypatch.setattr("config.STAR_SYMBOLS", set())  # empty STAR set for this test

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.012)

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is True, "expected Area 5 to fire"

    # close_position was called with the right close_reason
    eng.order_mgr.close_position.assert_called_once()
    pos_args = eng.order_mgr.close_position.call_args.args
    # Expected signature: close_position(exchange, position, reason)
    assert pos_args[2] == "auto_small_tp_1pct", (
        f"expected close reason 'auto_small_tp_1pct', got {pos_args[2]}"
    )


def test_auto_small_tp_no_op_below_1pct_or_below_30min(monkeypatch):
    """No-op when: pnl < 1%, age < 30min, pnl >= 2% (trailing zone)."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    monkeypatch.setattr("config.STAR_SYMBOLS", set())

    # (1) pnl +0.5% (below 1% threshold)
    p1 = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.005)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.005)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p1) is False

    # (2) age 25min (below 30min threshold)
    p2 = _make_long_position(age_min=25.0, entry=1.0, current_mark=1.015)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.015)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p2) is False

    # (3) pnl +2.5% (trailing stop zone, not Area 5's job)
    p3 = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.025)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.025)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p3) is False

    assert eng.order_mgr.close_position.call_count == 0


def test_auto_small_tp_skips_star_symbols(monkeypatch):
    """STAR_SYMBOLS positions ride per Phase 46 policy — Area 5 must skip."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.012)

    # Pin ATOM/ARB as STAR
    monkeypatch.setattr(
        "config.STAR_SYMBOLS",
        {"ATOM/USDT:USDT", "ARB/USDT:USDT"},
    )

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.012)
    p.symbol = "ATOM/USDT:USDT"   # STAR symbol

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is False, "STAR symbol should be exempt from Area 5"
    eng.order_mgr.close_position.assert_not_called()


def test_auto_small_tp_skips_spot(monkeypatch):
    """Spot positions keep existing logic — Area 5 is futures-only."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.015)
    monkeypatch.setattr("config.STAR_SYMBOLS", set())

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.015)
    p.market_type = "spot"   # spot, not futures

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is False, "spot should be exempt from Area 5"
    eng.order_mgr.close_position.assert_not_called()
