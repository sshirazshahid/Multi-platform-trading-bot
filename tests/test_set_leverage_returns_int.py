"""Contract: every exchange.set_leverage returns int (2026-04-29).

Live crash:
  2026-04-29 05:47:23 [Bybit] Leverage set: 2x for DOGE/USDT:USDT
  2026-04-29 05:47:23 [Claude] EXECUTING OPEN: ... 2x | size=596.83 ...
  2026-04-29 05:47:23 [Claude] open_position failed:
    unsupported operand type(s) for /: 'NoneType' and 'int'

Root cause: my leverage-clamp work in commit 307c370 made
order_manager._open_position do `applied / safe_lev`, but the
BinanceClient / BybitClient / BitgetClient overrides of
set_leverage all returned None — they pre-dated the int-return
contract introduced by exchanges.base.

Fix has two layers:
  1. All three client overrides now explicitly return int.
  2. order_manager defensively coerces None → safe_lev (preserves
     legacy behavior so a future override-without-return doesn't
     crash trade entry).
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_stub(client_cls):
    """Build a client without running __init__ (no real ccxt)."""
    c = client_cls.__new__(client_cls)
    c.exchange = MagicMock()
    c.exchange.markets = {}
    c.exchange.set_leverage.return_value = {"leverage": 2}
    c.exchange.load_markets.return_value = {}
    c._ok = lambda: True
    c._ready = lambda: True
    c.switch_to_futures = lambda: None
    c.switch_to_spot = lambda: None
    return c


def test_binance_client_returns_int_on_success():
    from exchanges.binance_client import BinanceClient
    c = _make_stub(BinanceClient)
    result = c.set_leverage("DOGE/USDT:USDT", 2)
    assert isinstance(result, int)
    assert result == 2


def test_bybit_client_returns_int_on_success():
    from exchanges.bybit_client import BybitClient
    c = _make_stub(BybitClient)
    result = c.set_leverage("DOGE/USDT:USDT", 2)
    assert isinstance(result, int)
    assert result == 2


def test_bitget_client_returns_int_on_success():
    from exchanges.bitget_client import BitgetClient
    c = _make_stub(BitgetClient)
    result = c.set_leverage("DOGE/USDT:USDT", 2)
    assert isinstance(result, int)
    assert result == 2


def test_bybit_returns_requested_lev_on_already_set_error():
    """'leverage not modified' is success, not failure."""
    from exchanges.bybit_client import BybitClient
    c = _make_stub(BybitClient)
    c.exchange.set_leverage.side_effect = Exception("leverage not modified")
    result = c.set_leverage("DOGE/USDT:USDT", 2)
    assert result == 2, "Already-at-this-level must return the level"


def test_bitget_returns_requested_lev_on_already_set_error():
    from exchanges.bitget_client import BitgetClient
    c = _make_stub(BitgetClient)
    c.exchange.set_leverage.side_effect = Exception("leverage not modified")
    result = c.set_leverage("BTC/USDT:USDT", 5)
    assert result == 5


def test_bybit_returns_zero_on_real_error():
    from exchanges.bybit_client import BybitClient
    c = _make_stub(BybitClient)
    c.exchange.set_leverage.side_effect = Exception("symbol not found")
    result = c.set_leverage("FAKE/USDT:USDT", 2)
    assert result == 0


def test_bitget_returns_zero_on_real_error():
    from exchanges.bitget_client import BitgetClient
    c = _make_stub(BitgetClient)
    c.exchange.set_leverage.side_effect = Exception("internal error 500")
    result = c.set_leverage("FAKE/USDT:USDT", 2)
    assert result == 0


def test_clients_return_zero_when_not_ready():
    from exchanges.binance_client import BinanceClient
    from exchanges.bitget_client import BitgetClient
    from exchanges.bybit_client import BybitClient
    for cls in (BinanceClient, BybitClient, BitgetClient):
        c = _make_stub(cls)
        c._ok = lambda: False
        c._ready = lambda: False
        result = c.set_leverage("BTC/USDT:USDT", 2)
        assert result == 0, f"{cls.__name__} must return 0 when not ready"
