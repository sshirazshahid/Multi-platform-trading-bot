"""L99 leverage-clamp invariants (2026-04-29).

At 99x configured, exchanges will reject most symbols (per-symbol notional
tiers). The clamp path must:
  1. Try requested first.
  2. On rejection, probe market metadata for symbol max.
  3. Walk a ladder of common caps until one is accepted.
  4. Return the actually-applied leverage (not the requested one).
  5. Caller proportionally rescales position size so margin stays put.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from exchanges.base import BaseExchange


class _StubExchange(BaseExchange):
    """Concrete subclass of the abstract BaseExchange for unit-testing
    the set_leverage path without touching real ccxt clients."""

    name = "TestEx"

    def _init_exchange(self):  # type: ignore[override]
        return None


@pytest.fixture
def base():
    b = _StubExchange.__new__(_StubExchange)
    b.exchange = MagicMock()
    b.exchange.markets = {}
    # Critical: real ccxt returns plain dicts. MagicMock would auto-create
    # truthy attributes that make markets.get(...).get("max") look real and
    # int() that to 1 — silently corrupting the ladder. Pin to {} so the
    # metadata probe falls through cleanly.
    b.exchange.load_markets.return_value = {}
    b._ready = lambda: True
    return b


def test_set_leverage_returns_requested_when_accepted(base):
    base.exchange.set_leverage.return_value = {"leverage": 99}
    result = base.set_leverage("BTC/USDT:USDT", 99)
    assert result == 99
    base.exchange.set_leverage.assert_called_once_with(99, "BTC/USDT:USDT")


def test_set_leverage_clamps_to_market_max(base):
    """Exchange rejects 99 → metadata says max=25 → returns 25."""
    base.exchange.markets = {
        "AXS/USDT:USDT": {"limits": {"leverage": {"max": 25}}}
    }
    calls = []

    def fake_set(lev, sym):
        calls.append(lev)
        if lev > 25:
            raise Exception(f"leverage {lev} exceeds tier cap")
        return {"leverage": lev}

    base.exchange.set_leverage.side_effect = fake_set
    result = base.set_leverage("AXS/USDT:USDT", 99)
    assert result == 25
    assert 99 in calls and 25 in calls


def test_set_leverage_walks_ladder_when_no_metadata(base):
    """No metadata → ladder bisection. Server caps at 20 → returns 20."""
    base.exchange.markets = {}

    def fake_set(lev, sym):
        if lev > 20:
            raise Exception(f"server max is 20 for {sym}")
        return {"leverage": lev}

    base.exchange.set_leverage.side_effect = fake_set
    result = base.set_leverage("DOGE/USDT:USDT", 99)
    assert result == 20


def test_set_leverage_returns_zero_when_all_fail(base):
    base.exchange.set_leverage.side_effect = Exception("symbol not tradeable")
    result = base.set_leverage("UNTRADED/USDT:USDT", 99)
    assert result == 0


def test_set_leverage_returns_zero_when_not_ready(base):
    base._ready = lambda: False
    result = base.set_leverage("BTC/USDT:USDT", 99)
    assert result == 0
    base.exchange.set_leverage.assert_not_called()


def test_clamp_size_rescale_preserves_margin():
    """If clamped 99→25, size must rescale by 25/99 so margin per trade
    stays the same (intended $200 margin → still $200 margin, but at lower
    notional ($5,000 instead of $19,800)."""
    requested_lev = 99
    applied_lev = 25
    intended_qty = 100.0  # arbitrary

    ratio = applied_lev / requested_lev
    rescaled_qty = intended_qty * ratio

    price = 1.0
    margin_intended = (intended_qty * price) / requested_lev
    margin_actual = (rescaled_qty * price) / applied_lev

    assert margin_intended == pytest.approx(margin_actual, rel=1e-9), (
        "Rescaling by applied/requested must preserve margin")
