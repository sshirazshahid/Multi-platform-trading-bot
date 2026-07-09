"""Regression: paper funding must accrue per-venue, not share one 8h window.

Pre-fix, ``OrderManager._last_funding_hour`` was a single scalar. The monitor
calls ``accrue_paper_funding(exchange)`` once per venue; the FIRST venue to land
in a funding hour stamped the scalar, so every OTHER venue saw the window as
already settled and skipped its own charge — carry-heavy longs on binance/bybit
looked free whenever bitget happened to settle first that window.
"""
from __future__ import annotations

import datetime as _dt


class _Pos:
    symbol = "BTC/USDT:USDT"
    side = "buy"
    market_type = "futures"
    entry_price = 100.0
    funding_paid = 0.0


class _Tracker:
    def get_open(self, exchange=None):
        return [_Pos()]


class _Sim:
    funding_on = True

    def funding_payment(self, pos, mark, rate):
        return 0.01


class _InnerEx:
    def fetch_funding_rate(self, symbol):
        return {"fundingRate": 0.0001}


class _Ex:
    def __init__(self, name):
        self.name = name
        self.exchange = _InnerEx()

    def fetch_ticker(self, symbol, market_type):
        return {"last": 100.0}


def test_funding_accrues_on_every_venue_in_same_window(monkeypatch):
    from core.order_manager import OrderManager

    # Freeze time to a real 8h funding boundary so the method proceeds.
    class _FakeDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 7, 9, 8, 30, tzinfo=tz)

    monkeypatch.setattr(_dt, "datetime", _FakeDatetime)

    applied: list[str] = []

    class _Wallet:
        def balance(self, name):
            return 100.0

        def apply_funding(self, name, cost):
            applied.append(name)
            return 100.0 - cost

    om = OrderManager.__new__(OrderManager)
    om.dry_run = True
    om._last_funding_hour = {}
    om.sim = _Sim()
    om.tracker = _Tracker()
    om.wallet = _Wallet()

    # Two venues settle in the SAME funding window.
    om.accrue_paper_funding(_Ex("binance"))
    om.accrue_paper_funding(_Ex("bybit"))

    # Both venues must have accrued (pre-fix only the first did).
    assert applied == ["binance", "bybit"], applied

    # Same venue again in the same window is idempotent (no double charge).
    om.accrue_paper_funding(_Ex("binance"))
    assert applied == ["binance", "bybit"], applied
