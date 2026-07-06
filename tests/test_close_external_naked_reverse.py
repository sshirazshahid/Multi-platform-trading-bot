"""Naked-reverse fail-closed guard in BotEngine._close_external_position.

THE BUG (CONTROLLED_LIVE only): when a reduceOnly close of an exchange-discovered
(untracked) futures position is REJECTED because the position was already closed
on-venue (stale 60s cache -> e.g. Binance -2022 "ReduceOnly Order is rejected"), the
error handler retried create_order WITHOUT reduceOnly (params={}). That plain market
order FILLS and opens an untracked NAKED REVERSE position — no SL, no tracker entry.
Same hole in the position-mode ("bybit"/unilateral) retry branch when _is_oneway is
False and the stripped params are also {}.

THE FIX (ported from order_manager._close_position_impl's 2026-04-20 guard): before
ANY reduceOnly-stripped retry, re-fetch the live position; if flat/absent, ABORT the
retry (fail-closed). If the re-fetch itself raises, also fail closed (no order).

These tests exercise CONTROLLED_LIVE (DRY_RUN=False) — the only path that reaches the
venue. Test doubles mirror tests/test_close_external_position_dry_gate.py.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

import core.bot_engine as be
from core.bot_engine import BotEngine


@pytest.fixture
def caplog(caplog):
    """Bridge loguru -> stdlib so pytest's caplog can capture the guard's log line.

    Production code uses `from loguru import logger`, which bypasses stdlib logging;
    without this sink `caplog.records` would be empty (pattern from
    tests/test_ghost_reroute_instrument.py).
    """
    from loguru import logger as loguru_logger

    py_logger = logging.getLogger("tests.loguru_bridge")
    py_logger.setLevel(logging.DEBUG)
    py_logger.propagate = True

    def _sink(message):
        record = message.record
        py_logger.log(getattr(logging, record["level"].name, logging.INFO), record["message"])

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    caplog.set_level(logging.DEBUG, logger="tests.loguru_bridge")
    yield caplog
    try:
        loguru_logger.remove(handler_id)
    except ValueError:
        pass


def _make_engine(exchange):
    """BotEngine shell with only the attributes this method touches."""
    eng = BotEngine.__new__(BotEngine)
    eng.active_exchanges = {"binance": exchange}
    eng.order_mgr = MagicMock()
    eng.order_mgr._oneway_mode = set()  # real set -> deterministic futures branch
    return eng


def _futures_pos():
    return {"symbol": "BTC/USDT:USDT", "market_type": "futures", "side": "buy", "size": 0.01}


# ── (a) reduceOnly-reject + position now FLAT -> NO second order placed ───────
def test_reduceonly_reject_when_flat_skips_naked_reverse_retry(monkeypatch, caplog):
    monkeypatch.setattr(be, "DRY_RUN", False)
    ex = MagicMock()
    # First close attempt rejected because the position is already gone on-venue.
    ex.create_order.side_effect = Exception("ReduceOnly Order is rejected")
    ex.fetch_positions.return_value = []  # re-fetch confirms FLAT

    with caplog.at_level(logging.INFO):
        _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")

    assert ex.create_order.call_count == 1  # first attempt only; retry aborted
    ex.fetch_positions.assert_called_once()  # guard consulted the venue
    assert any(
        "fail-closed guard" in r.getMessage() for r in caplog.records
    ), "expected the naked-reverse fail-closed guard log line"


# ── (b) reduceOnly-reject + position STILL open -> retry proceeds (no regress) ─
def test_reduceonly_reject_when_still_open_retries(monkeypatch):
    monkeypatch.setattr(be, "DRY_RUN", False)
    ex = MagicMock()
    ex.create_order.side_effect = [Exception("ReduceOnly Order is rejected"), {"id": "2"}]
    ex.fetch_positions.return_value = [{"contracts": 0.01}]  # re-fetch confirms STILL OPEN

    _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")

    assert ex.create_order.call_count == 2  # retry proceeded exactly as before
    retry = ex.create_order.call_args_list[1]
    assert retry.args[0] == "BTC/USDT:USDT"
    assert retry.args[2] == "sell"  # close of a long
    assert retry.args[5] == {}  # reduceOnly stripped on the legit retry


# ── (c) re-fetch itself raises -> fail CLOSED, NO retry order ─────────────────
def test_refetch_raises_fails_closed(monkeypatch):
    monkeypatch.setattr(be, "DRY_RUN", False)
    ex = MagicMock()
    ex.create_order.side_effect = Exception("ReduceOnly Order is rejected")
    ex.fetch_positions.side_effect = Exception("network timeout")

    _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")

    assert ex.create_order.call_count == 1  # retry aborted (fail-closed on re-fetch error)


# ── (d) position-mode ("bybit") branch is guarded too ────────────────────────
def test_position_mode_branch_flat_skips_naked_reverse_retry(monkeypatch, caplog):
    monkeypatch.setattr(be, "DRY_RUN", False)
    ex = MagicMock()
    ex._is_oneway = False  # stripped retry params would be {} -> naked reverse risk
    ex.create_order.side_effect = Exception("position side does not match -4061 unilateral")
    ex.fetch_positions.return_value = []  # FLAT

    with caplog.at_level(logging.INFO):
        _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")

    assert ex.create_order.call_count == 1  # first attempt only; retry aborted
    assert any("fail-closed guard" in r.getMessage() for r in caplog.records)
