"""Phase C — formalized infra + genuine-gap tests.

Covers:
  * BaseExchange.fetch_mark_index parsing (mark+index, fail-open).
  * MarketDataLedger.mark_index frame for all 3 venues (immutable).
  * cross_venue derivs harvester real fetch against a FIXTURE via the _get seam
    (no unconditional live call) + MarketDataLedger.derivs.
  * FillRealityEngine: order rejection / liquidation-buffer / failed-leg.
  * RiskGovernor: stale model-pointer HARD halt + per-venue exposure cap.
  * Fill-time feed-freshness gate: rejects a stale timestamp, allows a no-ts mock.
"""

from __future__ import annotations

import time

import pytest

from core import fill_reality, risk_governor
from core.market_data_ledger import MarketDataLedger
from exchanges.base import BaseExchange


# --------------------------------------------------------- base mark/index
class _FakeCcxt:
    def __init__(self, ticker):
        self._ticker = ticker

    def fetch_ticker(self, symbol, params=None):
        return self._ticker


class _StubExchange(BaseExchange):
    name = "stub"

    def _init_exchange(self):  # pragma: no cover - not used (we bypass __init__)
        ...

    def _futures_params(self):
        return {}


def _make_stub(ticker):
    ex = _StubExchange.__new__(_StubExchange)
    ex.exchange = _FakeCcxt(ticker)
    ex._connected = True
    return ex


def test_fetch_mark_index_parses_mark_and_index():
    ex = _make_stub({"info": {"markPrice": "100.5", "indexPrice": "100.1"}})
    mi = ex.fetch_mark_index("BTC/USDT:USDT", "futures")
    assert mi["mark"] == pytest.approx(100.5)
    assert mi["index"] == pytest.approx(100.1)
    assert mi["ts"] is not None


def test_fetch_mark_index_fail_open_when_not_ready():
    ex = _StubExchange.__new__(_StubExchange)
    ex.exchange = None
    ex._connected = False
    assert ex.fetch_mark_index("X") == {"mark": None, "index": None, "ts": None}


# --------------------------------------------------------- ledger frame
class _FakeClient:
    def __init__(self, mark, index):
        self._mi = {"mark": mark, "index": index, "ts": 1.0}

    def fetch_mark_index(self, symbol, market_type="futures"):
        return dict(self._mi)


def test_ledger_mark_index_all_three_venues_immutable():
    ledger = MarketDataLedger(
        {
            "binance": _FakeClient(100.0, 99.9),
            "bybit": _FakeClient(100.2, 100.0),
            "bitget": _FakeClient(99.8, 99.7),
        }
    )
    frame = ledger.mark_index("BTC/USDT:USDT")
    assert set(frame) == {"binance", "bybit", "bitget"}
    for venue in frame:
        assert frame[venue]["mark"] is not None
        assert frame[venue]["index"] is not None
    # immutable — cannot mutate the shared snapshot
    with pytest.raises(TypeError):
        frame["binance"]["mark"] = 0.0


# --------------------------------------------------- cross-venue derivs (fixture)
def test_bybit_derivs_fixture_via_get_seam(monkeypatch):
    from core.data_sources.cross_venue import BybitDerivsHarvester

    fixture = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "bid1Price": "100.0",
                    "ask1Price": "100.2",
                    "fundingRate": "0.0001",
                    "openInterestValue": "12345.0",
                }
            ]
        }
    }
    h = BybitDerivsHarvester()
    monkeypatch.setattr(h, "_get", lambda url: fixture)
    snap = h.snapshot(["BTC"], force=True)
    rec = snap["BTC"]
    assert rec["stale"] is False
    assert rec["funding"] == pytest.approx(0.0001)
    assert rec["oi"] == pytest.approx(12345.0)
    assert rec["mid"] == pytest.approx(100.1)


def test_ledger_derivs_frame_with_fixture_harvesters(monkeypatch):
    from core.data_sources.cross_venue import (
        BitgetDerivsHarvester,
        BybitDerivsHarvester,
    )

    by = BybitDerivsHarvester()
    bg = BitgetDerivsHarvester()
    monkeypatch.setattr(
        by, "_get",
        lambda url: {"result": {"list": [
            {"symbol": "BTCUSDT", "bid1Price": "100", "ask1Price": "100.2",
             "fundingRate": "0.0001", "openInterestValue": "10"}]}})
    monkeypatch.setattr(
        bg, "_get",
        lambda url: {"data": [
            {"symbol": "BTCUSDT", "bidPr": "100.1", "askPr": "100.3",
             "fundingRate": "0.0002", "holdingAmount": "20"}]})
    ledger = MarketDataLedger({}, harvesters={"bybit": by, "bitget": bg})
    frame = ledger.derivs(["BTC"], force=True)
    assert "BTC" in frame
    assert set(frame["BTC"]["mids"]) == {"bybit", "bitget"}


# --------------------------------------------------- fill reality: rejection
def test_order_rejection_min_notional_and_margin():
    assert fill_reality.order_rejection_reason(size=0.0, price=100.0) == "nonpositive_size"
    assert fill_reality.order_rejection_reason(size=1.0, price=0.0) == "invalid_price"
    r = fill_reality.order_rejection_reason(size=0.01, price=100.0, min_notional=5.0)
    assert r is not None and r.startswith("min_notional")
    assert fill_reality.order_rejection_reason(
        size=1.0, price=100.0, available_margin=10.0, required_margin=50.0
    ) == "insufficient_margin"
    assert fill_reality.order_rejection_reason(size=1.0, price=100.0) is None


# --------------------------------------------------- fill reality: liquidation
def test_liquidation_buffer_breach_long():
    # 5x long from 100 -> liq ~ 100*(1-0.2+0.005)=80.5
    ok = fill_reality.liquidation_buffer_breach("long", 100.0, 95.0, 5.0)
    assert ok["breach"] is False and ok["crossed"] is False
    breached = fill_reality.liquidation_buffer_breach("long", 100.0, 80.0, 5.0)
    assert breached["crossed"] is True and breached["breach"] is True


# --------------------------------------------------- fill reality: failed leg
def test_failed_leg_outcome_unhedged():
    legs = [
        {"leg": "long", "target_qty": 1.0, "filled_qty": 1.0},
        {"leg": "short", "target_qty": 1.0, "filled_qty": 0.4},
    ]
    out = fill_reality.failed_leg_outcome(legs)
    assert out["any_failed"] is True
    assert out["failed_legs"] == ["short"]
    assert out["unhedged_qty"] == pytest.approx(0.6)
    assert out["legging_risk"] is True


# --------------------------------------------------- risk governor
def test_model_freshness_halt_on_missing_and_stale():
    assert risk_governor.model_freshness_halt(None, market_type="futures")["halt"] is True
    stale_ptr = {
        "model_version": "v1",
        "artifact_path": "nonexistent.bin",
        "promoted_at": time.time() - 100 * 86400,
        "diag": {"oos_wr": 0.6, "deflated_sharpe": 0.2, "pbo": 0.3,
                 "artifact_path": "nonexistent.bin", "model_version": "v1"},
    }
    res = risk_governor.model_freshness_halt(stale_ptr, market_type="futures")
    assert res["halt"] is True


def test_per_venue_exposure_cap():
    class _P:
        def __init__(self, venue, size, px):
            self.venue = venue
            self.size = size
            self.entry_price = px

    positions = [_P("binance", 1.0, 100.0), _P("bybit", 1.0, 100.0)]
    # binance venue slice = 100 notional; +50 new = 150 vs equity 1000 @ 12% cap (=120) -> breach
    assert risk_governor.per_venue_exposure_breached(
        positions, "binance", 50.0, 1000.0, 12.0) is True
    # bybit slice 100; +5 new = 105 < 120 -> ok
    assert risk_governor.per_venue_exposure_breached(
        positions, "bybit", 5.0, 1000.0, 12.0) is False


# --------------------------------------------------- feed-freshness fill gate
def _bare_order_manager():
    from core.order_manager import OrderManager

    return OrderManager.__new__(OrderManager)


class _ExName:
    name = "binance"


def test_fill_freshness_rejects_stale_ts_allows_missing():
    om = _bare_order_manager()
    ex = _ExName()
    # stale: timestamp 1h old (> 300s default)
    stale = {"timestamp": (time.time() - 3600) * 1000.0, "last": 100.0}
    reason = om._fill_freshness_reason(stale, ex)
    assert reason and "stale" in reason
    # missing timestamp -> allowed (mocked ticker)
    assert om._fill_freshness_reason({"last": 100.0}, ex) is None
    # fresh -> allowed
    fresh = {"timestamp": time.time() * 1000.0, "last": 100.0}
    assert om._fill_freshness_reason(fresh, ex) is None
