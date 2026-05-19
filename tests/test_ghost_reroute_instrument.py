"""Patch #0 instrumentation tests — log-only, no behavior change.

Adaptations from the plan's verbatim test code (2026-05-19):
  1. Local `caplog` fixture override propagates loguru's logger into
     pytest's stdlib LogCaptureHandler. The production code uses
     `from loguru import logger` (matching codebase convention), so
     without this bridge `caplog.records` would be empty.
  2. `_patch0_instrument_ghost` takes an extra `exchange_instance` arg
     (a BaseExchange, not the string `pos.exchange`). The production
     `verify_exchange_sl_alive` calls `exchange.name.lower()` and would
     swallow an AttributeError on a string — producing zero events in
     prod and silently failing the Day 4–5 decision gate.
  3. (2026-05-19 follow-up) `_patch0_instrument_ghost` now takes a
     `current_mark` parameter and computes uPnL inline using the
     canonical "buy"/"sell" convention — the original
     `_compute_unrealized_pnl_pct` method read a never-populated
     `self._last_mark_price` cache and always returned 0.0. Tests
     below pass `current_mark` directly and the C1-regression test
     uses a real PositionTracker (not MagicMock(spec=...)) to verify
     the uPnL math against a known mark.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

# Import position_tracker at module load — its transitive imports (via the
# `utils` package's __init__) trigger `utils.logger.setup_logger()`, which
# calls `loguru.logger.remove()` and wipes any sinks added before the
# import. Doing the import here means the loguru-→caplog bridge fixture
# below can safely install its sink without it being wiped by a downstream
# import inside the test body.
from core import position_tracker as pt  # noqa: E402


@pytest.fixture
def caplog(caplog):
    """Bridge loguru -> stdlib so pytest's caplog can capture log lines.

    Loguru bypasses stdlib logging entirely. Add a sink that re-emits each
    loguru record via a stdlib logger (`tests.loguru_bridge`) which
    propagates to the root logger pytest's `caplog` is attached to.
    """
    from loguru import logger as loguru_logger

    py_logger = logging.getLogger("tests.loguru_bridge")
    py_logger.setLevel(logging.DEBUG)
    py_logger.propagate = True

    def _sink(message):
        record = message.record
        level_name = record["level"].name
        py_level = getattr(logging, level_name, logging.INFO)
        py_logger.log(py_level, record["message"])

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    caplog.set_level(logging.DEBUG, logger="tests.loguru_bridge")
    yield caplog
    try:
        loguru_logger.remove(handler_id)
    except ValueError:
        # Handler may already be removed if setup_logger() ran mid-test
        # (defensive — should not happen now that pt is imported at module
        # load, but harmless if it does).
        pass


def _make_position(symbol="BTC/USDT:USDT", side="buy",
                   entry_price=100.0, size=1.0):
    """Build a stub position object with the fields the instrument block reads.

    Note: side uses the canonical "buy"/"sell" convention used by
    Position.close() and every other side check in position_tracker.py.
    The original tests used "long"/"short" which never matched the
    production code path.
    """
    pos = MagicMock()
    pos.symbol = symbol
    pos.side = side
    pos.exchange = "binance"
    pos.market_type = "futures"
    pos.entry_price = entry_price
    pos.size = size
    pos.leverage = 2
    pos.tp_price = 105.0
    pos.sl_price = 98.0
    pos._pending_ghost_since = None
    return pos


def test_instrument_logs_would_reroute_true_when_all_conditions_met(caplog):
    """uPnL>0 + SL alive + TP alive → log line shows would_reroute=True."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    # buy entry $100, mark $102.50 → +2.5% uPnL.
    pos = _make_position(side="buy", entry_price=100.0, size=1.0)
    tracker = MagicMock(spec=pt.PositionTracker)

    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=102.50)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    assert "would_reroute=True" in msg
    assert "sl_alive=True" in msg
    assert "tp_alive=True" in msg
    assert "reason=ghost_sync" in msg
    # uPnL fraction is 0.025 → formatted as 0.0250.
    assert "upnl_pct=0.0250" in msg
    # notional = size * entry_price = 1.0 * 100.0 = 100.0.
    assert "notional=100.0000" in msg


def test_instrument_logs_would_reroute_false_when_upnl_negative(caplog):
    """uPnL<=0 → would_reroute=False (verify calls skipped)."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    # buy entry $100, mark $99.50 → -0.5% uPnL.
    pos = _make_position(side="buy", entry_price=100.0, size=1.0)
    tracker = MagicMock(spec=pt.PositionTracker)

    om = MagicMock()
    # These SHOULD NOT be called when upnl <= 0 (efficiency)
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_reconciled", current_mark=99.50)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    assert "would_reroute=False" in matches[0].getMessage()
    # Verify calls were skipped (upnl_pct gate)
    om.verify_exchange_sl_alive.assert_not_called()
    om.verify_exchange_tp_alive.assert_not_called()


def test_instrument_no_log_when_flag_disabled(caplog):
    """GHOST_REROUTE_INSTRUMENT=False → no log line emitted."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = False

    pos = _make_position()
    tracker = MagicMock(spec=pt.PositionTracker)
    om = MagicMock()
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=102.0)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 0

    # Reset flag for subsequent tests (defensive — test order shouldn't matter).
    config.GHOST_REROUTE_INSTRUMENT = True


def test_instrument_swallows_exception_during_compute(caplog):
    """Exception in compute path → warning logged, never raises.

    Trigger an exception by making verify_exchange_sl_alive raise after
    the uPnL>0 gate. The helper's outer try/except must swallow it and
    log a warning.
    """
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position(side="buy", entry_price=100.0, size=1.0)
    tracker = MagicMock(spec=pt.PositionTracker)
    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(side_effect=RuntimeError("kaboom"))
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.WARNING):
        # Must not raise. Mark > entry so uPnL>0 gate fires → SL check runs → raises.
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=102.0)

    matches = [r for r in caplog.records
               if "GHOST_REROUTE_INSTRUMENT" in r.getMessage() and r.levelname == "WARNING"]
    assert len(matches) == 1
    assert "failed to compute" in matches[0].getMessage()


def test_instrument_short_side_sign_inverts(caplog):
    """sell side with mark below entry → positive uPnL → would_reroute=True path.

    Verifies the canonical "buy"/"sell" sign convention. With side="sell",
    entry=$100, mark=$98 → uPnL = -1 * (98-100)/100 = +0.02 (favorable for short).
    The original helper used "long"/"short" — would have logged the wrong sign
    on every short position even if `_last_mark_price` had been wired.
    """
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position(side="sell", entry_price=100.0, size=1.0)
    tracker = MagicMock(spec=pt.PositionTracker)
    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=98.0)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    assert "upnl_pct=0.0200" in msg
    assert "would_reroute=True" in msg


def test_instrument_missing_mark_logs_nan_and_does_not_reroute(caplog):
    """current_mark=None → upnl_pct=nan, would_reroute=False, no verify calls.

    Caller's fetch_ticker may fail; helper must still emit an event so the
    rate counter in the report stays accurate.
    """
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position(side="buy", entry_price=100.0, size=2.0)
    tracker = MagicMock(spec=pt.PositionTracker)
    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=None)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    # NaN formats as "nan" via f"{nan:.4f}".
    assert "upnl_pct=nan" in msg
    assert "would_reroute=False" in msg
    # notional still emitted from size * entry_price = 2.0 * 100.0 = 200.0.
    assert "notional=200.0000" in msg
    # Verify calls skipped (NaN > 0 is False).
    om.verify_exchange_sl_alive.assert_not_called()
    om.verify_exchange_tp_alive.assert_not_called()


def test_instrument_real_tracker_upnl_math_C1_regression(tmp_path, caplog, monkeypatch):
    """C1-regression: exercise uPnL compute against a real PositionTracker.

    The original tests used MagicMock(spec=pt.PositionTracker) and mocked out
    `_compute_unrealized_pnl_pct` entirely, so the helper's broken
    `self._last_mark_price` lookup went unnoticed for the entire commit
    357d025. This test uses a REAL PositionTracker instance and verifies
    that the helper correctly computes uPnL from the passed-in current_mark
    (no method-mocking).
    """
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    # Isolate persistence — don't touch the real data/positions.json.
    save_path = tmp_path / "positions.json"
    backup_path = tmp_path / "positions.json.bak"
    monkeypatch.setattr(pt.PositionTracker, "SAVE_PATH", save_path)
    monkeypatch.setattr(pt.PositionTracker, "BACKUP_PATH", backup_path)

    tracker = pt.PositionTracker()  # REAL instance, not a MagicMock
    pos = _make_position(side="buy", entry_price=200.0, size=0.5)
    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=False)
    ex_instance = MagicMock()

    # Mark $206.00 → +3% uPnL on a buy at $200.
    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance,
                                     "ghost_sync", current_mark=206.0)

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    # 3% in favor → 0.0300
    assert "upnl_pct=0.0300" in msg
    # notional = 0.5 * 200 = 100.0 (real, not a $50 hardcoded proxy)
    assert "notional=100.0000" in msg
    # SL alive but TP not → would_reroute=False
    assert "sl_alive=True" in msg
    assert "tp_alive=False" in msg
    assert "would_reroute=False" in msg
