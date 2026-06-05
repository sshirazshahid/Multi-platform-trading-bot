"""Defense-in-depth: BotEngine._close_external_position must self-gate on DRY_RUN.

`_close_external_position` (core/bot_engine.py) executes REAL money side effects on
exchange-discovered (untracked / manual) positions:
  * spot    -> exchange.create_order(symbol, "market", "sell", held, ...)  (sells the
              owner's actual coins)
  * futures -> exchange.create_order(symbol, "market", close_side, size, ...) (reduceOnly
              market close, plus error-recovery retries)

Its sole caller (the MCP position monitor, ~bot_engine.py:4243) already guards on
DRY_RUN, so there is no live leak today. But the function itself trusts caller
governance — exactly the fragile pattern behind the 2026-05-31 PAPER->live leak. This
pins a belt-and-suspenders self-gate so PAPER/OBSERVATION can never reach the exchange
regardless of how the function is entered, while CONTROLLED_LIVE behavior is unchanged.

The function reads the module-global DRY_RUN (imported in core/bot_engine.py), so tests
monkeypatch core.bot_engine.DRY_RUN.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import core.bot_engine as be
from core.bot_engine import BotEngine


def _make_engine(exchange):
    """BotEngine shell with only the attributes this method touches (skip heavy __init__)."""
    eng = BotEngine.__new__(BotEngine)
    eng.active_exchanges = {"binance": exchange}
    eng.order_mgr = MagicMock()
    eng.order_mgr._oneway_mode = set()  # real set -> deterministic futures branch
    return eng


def _futures_pos():
    return {"symbol": "BTC/USDT:USDT", "market_type": "futures", "side": "buy", "size": 0.01}


def _spot_pos():
    return {"symbol": "BTC/USDT", "market_type": "spot", "side": "buy", "size": 1.0}


def test_dry_run_blocks_external_futures_close(monkeypatch):
    monkeypatch.setattr(be, "DRY_RUN", True)
    ex = MagicMock()
    _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")
    ex.create_order.assert_not_called()


def test_dry_run_blocks_external_spot_sell(monkeypatch):
    """Gate must short-circuit BEFORE any venue read, so PAPER never even probes balance."""
    monkeypatch.setattr(be, "DRY_RUN", True)
    ex = MagicMock()
    ex.fetch_balance.return_value = {"free": {"BTC": 1.0}}
    ex.fetch_ticker.return_value = {"last": 60000.0}
    _make_engine(ex)._close_external_position("binance", _spot_pos(), "monitor exit")
    ex.create_order.assert_not_called()
    ex.fetch_balance.assert_not_called()


def test_live_still_places_external_futures_close(monkeypatch):
    """CONTROLLED_LIVE behavior unchanged: exactly one opposite-side market close."""
    monkeypatch.setattr(be, "DRY_RUN", False)
    ex = MagicMock()
    ex.create_order.return_value = {"id": "1"}
    _make_engine(ex)._close_external_position("binance", _futures_pos(), "monitor exit")
    ex.create_order.assert_called_once()
    args = ex.create_order.call_args.args
    assert args[0] == "BTC/USDT:USDT"
    assert args[1] == "market"
    assert args[2] == "sell"      # close of a long
    assert args[3] == 0.01
    assert args[6] == "futures"


def test_self_gate_present_in_function_source():
    """The gate lives INSIDE the function (defense-in-depth), not only at the call site."""
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    i = src.index("def _close_external_position")
    j = src.index("\n    def ", i + 1)  # next method = end of this one
    body = src[i:j]
    assert "if DRY_RUN:" in body, "_close_external_position must self-gate on DRY_RUN"
    assert "[DRY]" in body, "the self-gate should log a [DRY] line for auditability"
