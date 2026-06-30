"""Phase 5 — S4 cross-venue RV stack (data + sim only; FIXTURES, no live calls).

Verifies the Bybit/Bitget public-derivs harvesters parse their native response
shapes into the SAME normalized record, the cross-venue quote/spread-z frames are
computed on fixtures, the two-book simulator models a legging event (one leg
filled, the other missed via a stale feed), and S4 is marked live-INFEASIBLE +
forward-data-gated. No network, no daemon.
"""

from __future__ import annotations

import importlib

import pytest

from core.data_sources import cross_venue as cv


# --------------------------------------------------------------- harvesters
def test_import_is_side_effect_free():
    m = importlib.import_module("core.data_sources.cross_venue")
    assert hasattr(m, "BybitDerivsHarvester")
    assert hasattr(m, "BitgetDerivsHarvester")


def _bybit_ticker(funding="0.0001", bid="100.0", ask="100.2", oi="5000000"):
    return {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "bid1Price": bid,
                    "ask1Price": ask,
                    "lastPrice": "100.1",
                    "fundingRate": funding,
                    "openInterestValue": oi,
                }
            ]
        },
    }


def _bitget_ticker(funding="0.0002", bid="99.9", ask="100.1", oi="4000000"):
    return {
        "code": "00000",
        "data": [
            {
                "symbol": "BTCUSDT",
                "bidPr": bid,
                "askPr": ask,
                "lastPr": "100.0",
                "fundingRate": funding,
                "holdingAmount": oi,
            }
        ],
    }


def test_bybit_harvester_normalizes_fixture(monkeypatch):
    h = cv.BybitDerivsHarvester(cache_ttl=0)
    monkeypatch.setattr(h, "_get", lambda url: _bybit_ticker())
    snap = h.snapshot(["BTC"])["BTC"]
    assert snap["venue"] == "bybit"
    assert snap["stale"] is False
    assert snap["bid"] == pytest.approx(100.0)
    assert snap["ask"] == pytest.approx(100.2)
    assert snap["mid"] == pytest.approx(100.1)
    assert snap["funding"] == pytest.approx(0.0001)
    assert snap["oi"] == pytest.approx(5_000_000)


def test_bitget_harvester_normalizes_fixture(monkeypatch):
    h = cv.BitgetDerivsHarvester(cache_ttl=0)
    monkeypatch.setattr(h, "_get", lambda url: _bitget_ticker())
    snap = h.snapshot(["BTC"])["BTC"]
    assert snap["venue"] == "bitget"
    assert snap["stale"] is False
    assert snap["mid"] == pytest.approx(100.0)
    assert snap["funding"] == pytest.approx(0.0002)


def test_harvester_fail_open_on_bad_payload(monkeypatch):
    h = cv.BybitDerivsHarvester(cache_ttl=0)
    monkeypatch.setattr(h, "_get", lambda url: None)
    snap = h.snapshot(["BTC"])["BTC"]
    assert snap["stale"] is True
    assert snap["mid"] is None


# ----------------------------------------------------- cross-venue frames
def test_cross_venue_quote_frame_aligns_venues():
    snaps = {
        "bybit": {"BTC": {"venue": "bybit", "bid": 100.0, "ask": 100.2, "mid": 100.1,
                          "funding": 0.0001, "oi": 5e6, "stale": False}},
        "bitget": {"BTC": {"venue": "bitget", "bid": 99.9, "ask": 100.1, "mid": 100.0,
                           "funding": 0.0002, "oi": 4e6, "stale": False}},
    }
    frame = cv.cross_venue_quote_frame(snaps)
    assert set(frame["BTC"]["mids"]) == {"bybit", "bitget"}
    assert frame["BTC"]["mids"]["bybit"] == pytest.approx(100.1)


def test_spread_z_frame_on_fixture_series():
    # zero-variance trailing spread -> z undefined (None), by design
    mid_a = [101.0, 101.0, 101.0, 101.0, 103.0]
    mid_b = [100.0, 100.0, 100.0, 100.0, 100.0]
    assert cv.spread_z(mid_a, mid_b, window=4) is None
    mid_a2 = [101.0, 100.8, 101.2, 100.9, 103.0]
    z2 = cv.spread_z(mid_a2, mid_b, window=4)
    assert z2 > 2.0  # last spread is a large positive outlier


def test_round_trip_cost_frame_two_leg_sums_both_venues():
    frame = cv.round_trip_cost_frame(["bybit", "bitget"])
    assert frame["bybit"] > 0 and frame["bitget"] > 0
    two_leg = cv.two_leg_round_trip_cost("bybit", "bitget")
    assert two_leg == pytest.approx(frame["bybit"] + frame["bitget"])


# ------------------------------------------------- two-book legging sim
def _book(venue, *, asks, bids, ts=1000.0):
    return cv.Book(venue=venue, bids=bids, asks=asks, ts=ts)


def test_two_book_sim_legging_event_one_filled_other_missed():
    # Long leg book is fresh & deep; short leg feed is stale -> rejected.
    long_book = _book("bybit", asks=[(100.0, 10.0)], bids=[(99.9, 10.0)], ts=1000.0)
    short_book = _book("bitget", asks=[(100.2, 10.0)], bids=[(100.0, 10.0)], ts=900.0)
    sim = cv.TwoBookSimulator(max_age_sec=10.0)
    out = sim.execute_two_leg(long_book, short_book, qty=5.0, now=1001.0)
    assert out["legging_risk"] is True
    assert out["long_leg"]["filled_qty"] == pytest.approx(5.0)
    assert out["short_leg"]["filled_qty"] == 0.0
    assert "stale" in out["short_leg"]["reason"]
    assert out["unhedged_qty"] == pytest.approx(5.0)


def test_two_book_sim_both_legs_fill_no_legging():
    long_book = _book("bybit", asks=[(100.0, 10.0)], bids=[(99.9, 10.0)], ts=1000.0)
    short_book = _book("bitget", asks=[(100.2, 10.0)], bids=[(100.0, 10.0)], ts=1000.0)
    sim = cv.TwoBookSimulator(max_age_sec=10.0)
    out = sim.execute_two_leg(long_book, short_book, qty=5.0, now=1001.0)
    assert out["legging_risk"] is False
    assert out["unhedged_qty"] == 0.0


def test_two_book_sim_partial_fill_thin_book():
    long_book = _book("bybit", asks=[(100.0, 2.0)], bids=[(99.9, 10.0)], ts=1000.0)
    short_book = _book("bitget", asks=[(100.2, 10.0)], bids=[(100.0, 10.0)], ts=1000.0)
    sim = cv.TwoBookSimulator(max_age_sec=10.0)
    out = sim.execute_two_leg(long_book, short_book, qty=5.0, now=1001.0)
    # long leg can only take 2.0 of 5.0 -> partial -> unhedged vs short fill
    assert out["long_leg"]["filled_qty"] == pytest.approx(2.0)
    assert out["legging_risk"] is True


# --------------------------------------------------- S4 markers / refusal
def test_s4_marked_infeasible_and_forward_gated():
    assert cv.S4_LIVE_FEASIBILITY == "INFEASIBLE-AT-$420"
    assert cv.S4_FORWARD_DATA_GATED is True


def test_refuse_live_raises():
    with pytest.raises(Exception):
        cv.refuse_live_at_420()
