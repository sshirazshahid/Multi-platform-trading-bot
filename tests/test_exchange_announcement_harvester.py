"""Diff/normalize tests for the exchange-announcement harvester (delisting reopen clock)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harvest_exchange_announcements import (  # noqa: E402
    diff_venue,
    load_state,
    normalize_binance,
    normalize_bitget,
    normalize_bybit_pages,
    save_state_atomic,
)

TS = "2026-07-24T19:00:00Z"


def test_normalize_binance():
    payload = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "deliveryDate": 4133404800000,
             "contractType": "PERPETUAL"},
            {"symbol": None},
        ]
    }
    out = normalize_binance(payload)
    assert out == {
        "BTCUSDT": {"status": "TRADING", "delivery_date": 4133404800000,
                    "contract_type": "PERPETUAL"}
    }


def test_normalize_bybit_pages_and_bitget():
    pages = [
        {"result": {"list": [
            {"symbol": "ETHUSDT", "status": "Trading", "deliveryTime": "0",
             "leverageFilter": {"maxLeverage": "100"}},
        ]}},
        {"result": {"list": [
            {"symbol": "SOLUSDT", "status": "Trading", "deliveryTime": "0",
             "leverageFilter": {"maxLeverage": "75"}},
        ]}},
    ]
    by = normalize_bybit_pages(pages)
    assert by["ETHUSDT"]["max_leverage"] == "100"
    assert by["SOLUSDT"]["status"] == "Trading"

    bg = normalize_bitget({"data": [
        {"symbol": "XRPUSDT", "symbolStatus": "normal", "offTime": "-1",
         "maxLever": "75", "minLever": "1"},
    ]})
    assert bg["XRPUSDT"] == {
        "status": "normal", "off_time": "-1", "max_leverage": "75", "min_leverage": "1"
    }


def test_diff_detects_delisting_tier_status_and_membership():
    old = {
        "AUSDT": {"status": "TRADING", "delivery_date": 4133404800000},
        "BUSDT": {"status": "Trading", "delivery_time": "0", "max_leverage": "100"},
        "GONEUSDT": {"status": "TRADING", "delivery_date": 4133404800000},
    }
    new = {
        # deliveryDate flipped to a near date -> delisting_signal
        "AUSDT": {"status": "TRADING", "delivery_date": 1785000000000},
        # leverage cut + status flip -> tier_change AND status_change
        "BUSDT": {"status": "Closed", "delivery_time": "0", "max_leverage": "50"},
        # new listing
        "NEWUSDT": {"status": "Trading", "delivery_time": "0", "max_leverage": "25"},
    }
    events = diff_venue(old, new, "binance", TS)
    classes = {(e["symbol"], e["event_class"]) for e in events}
    assert ("AUSDT", "delisting_signal") in classes
    assert ("BUSDT", "tier_change") in classes
    assert ("BUSDT", "status_change") in classes
    assert ("NEWUSDT", "new_symbol") in classes
    assert ("GONEUSDT", "removed_symbol") in classes
    delist = next(e for e in events if e["event_class"] == "delisting_signal")
    assert delist["changed"]["delivery_date"] == [4133404800000, 1785000000000]


def test_diff_no_changes_yields_no_events():
    state = {"AUSDT": {"status": "TRADING", "delivery_date": 4133404800000}}
    assert diff_venue(state, dict(state), "bybit", TS) == []


def test_state_roundtrip_atomic(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {"venues": {}}
    save_state_atomic(path, {"venues": {"binance": {"BTCUSDT": {"status": "TRADING"}}}})
    loaded = load_state(path)
    assert loaded["venues"]["binance"]["BTCUSDT"]["status"] == "TRADING"
    # corrupted file re-baselines instead of crashing
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == {"venues": {}}
    # and a fresh save repairs it atomically
    save_state_atomic(path, {"venues": {}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"venues": {}}
