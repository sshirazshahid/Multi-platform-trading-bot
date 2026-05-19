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


def _make_position(symbol="BTC/USDT:USDT", side="long", upnl_pct=0.02):
    """Build a stub position object with the fields the instrument block reads."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.side = side
    pos.exchange = "binance"
    pos.market_type = "futures"
    pos.entry_price = 100.0
    pos.size = 1.0
    pos.leverage = 2
    pos.tp_price = 105.0
    pos.sl_price = 98.0
    pos._pending_ghost_since = None
    return pos


def test_instrument_logs_would_reroute_true_when_all_conditions_met(caplog):
    """uPnL>0 + SL alive + TP alive → log line shows would_reroute=True."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position(upnl_pct=0.025)
    tracker = MagicMock(spec=pt.PositionTracker)
    tracker._compute_unrealized_pnl_pct = MagicMock(return_value=0.025)

    om = MagicMock()
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance, "ghost_sync")

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    assert "would_reroute=True" in msg
    assert "sl_alive=True" in msg
    assert "tp_alive=True" in msg
    assert "reason=ghost_sync" in msg


def test_instrument_logs_would_reroute_false_when_upnl_negative(caplog):
    """uPnL<=0 → would_reroute=False (verify calls skipped)."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position(upnl_pct=-0.005)
    tracker = MagicMock(spec=pt.PositionTracker)
    tracker._compute_unrealized_pnl_pct = MagicMock(return_value=-0.005)

    om = MagicMock()
    # These SHOULD NOT be called when upnl <= 0 (efficiency)
    om.verify_exchange_sl_alive = MagicMock(return_value=True)
    om.verify_exchange_tp_alive = MagicMock(return_value=True)
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance, "ghost_reconciled")

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
    tracker._compute_unrealized_pnl_pct = MagicMock(return_value=0.02)
    om = MagicMock()
    ex_instance = MagicMock()

    with caplog.at_level(logging.INFO):
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance, "ghost_sync")

    matches = [r for r in caplog.records if "GHOST_REROUTE_INSTRUMENT" in r.getMessage()]
    assert len(matches) == 0

    # Reset flag for subsequent tests (defensive — test order shouldn't matter).
    config.GHOST_REROUTE_INSTRUMENT = True


def test_instrument_swallows_exception_during_compute(caplog):
    """Exception in compute path → warning logged, never raises."""
    import config
    config.GHOST_REROUTE_INSTRUMENT = True

    pos = _make_position()
    tracker = MagicMock(spec=pt.PositionTracker)
    tracker._compute_unrealized_pnl_pct = MagicMock(side_effect=RuntimeError("kaboom"))
    om = MagicMock()
    ex_instance = MagicMock()

    with caplog.at_level(logging.WARNING):
        # Must not raise
        pt._patch0_instrument_ghost(tracker, pos, om, ex_instance, "ghost_sync")

    matches = [r for r in caplog.records
               if "GHOST_REROUTE_INSTRUMENT" in r.getMessage() and r.levelname == "WARNING"]
    assert len(matches) == 1
    assert "failed to compute" in matches[0].getMessage()
