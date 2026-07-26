from __future__ import annotations

import csv
from pathlib import Path

import pytest

from core.order_manager import OrderManager
from core.position_tracker import Position


def _position() -> Position:
    return Position(
        id="paper-funding-1",
        exchange="Bybit",
        symbol="BTC/USDT:USDT",
        side="buy",
        market_type="futures",
        strategy="test",
        entry_price=100.0,
        size=2.0,
        stop_loss=90.0,
        take_profit=110.0,
        open_time=100.0,
        paper_trade=True,
    )


def _write_history(path: Path, rows: list[tuple[float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "funding_rate", "venue", "symbol"])
        for ts, rate in rows:
            writer.writerow([ts, rate, "bybit", "BTC/USDT:USDT"])


class _Tracker:
    def __init__(self, position):
        self.position = position
        self.saves = 0

    def get_open(self, exchange=None):
        return [self.position]

    def _save(self):
        self.saves += 1


class _Wallet:
    def __init__(self):
        self.costs = []

    def balance(self, name):
        return 100.0 - sum(self.costs)

    def apply_funding(self, name, cost):
        self.costs.append(cost)
        return self.balance(name)


class _InnerExchange:
    def fetch_mark_ohlcv(self, symbol, timeframe, since=None, limit=None):
        settlement_ms = int(since) + 60_000
        return [[settlement_ms, 100.0, 100.0, 100.0, 100.0, 1.0]]


class _Exchange:
    name = "Bybit"
    exchange = _InnerExchange()


class _Sim:
    funding_on = True

    def funding_payment(self, pos, mark, rate):
        sign = 1.0 if pos.side == "buy" else -1.0
        return pos.size * mark * rate * sign


def _manager(position):
    manager = OrderManager.__new__(OrderManager)
    manager.dry_run = True
    manager.sim = _Sim()
    manager.tracker = _Tracker(position)
    manager.wallet = _Wallet()
    return manager


def test_realized_variable_interval_settlements_accrue_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_history(
        Path("data/funding_history/bybit_BTC.csv"),
        [(200.0, 0.001), (260.0, -0.0005), (380.0, 0.002)],
    )
    monkeypatch.setattr("core.order_manager.time.time", lambda: 400.0)
    position = _position()
    manager = _manager(position)

    manager.accrue_paper_funding(_Exchange())

    assert manager.wallet.costs == pytest.approx([0.2, -0.1, 0.4])
    assert position.funding_paid == pytest.approx(0.5)
    assert position.last_funding_ts == pytest.approx(380.0)
    assert manager.tracker.saves == 1

    manager.accrue_paper_funding(_Exchange())
    assert manager.wallet.costs == pytest.approx([0.2, -0.1, 0.4])


def test_missing_realized_history_never_fabricates_rate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("core.order_manager.time.time", lambda: 400.0)
    position = _position()
    manager = _manager(position)

    manager.accrue_paper_funding(_Exchange())

    assert manager.wallet.costs == []
    assert position.funding_paid == 0.0
    assert position.last_funding_ts == 0.0

