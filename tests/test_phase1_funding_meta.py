"""Phase 1 (Rev 5 carry-first): per-venue funding metadata — fixture-only tests.

Covers each venue quirk:
  - Binance: fundingInfo ADJUSTED interval override; 8h default when absent.
  - Bybit: instruments-info fundingInterval is MINUTES -> hours conversion.
  - Bitget: variable fundingRateInterval + nextUpdate parsing.
  - MarketDataLedger.funding(): BTC+ETH frame across all 3 venues.
  - Error path: stale-marked frame (age_sec=inf), never raises.

No unconditional live call: every harvester's ``_get`` seam is stubbed.
"""

from __future__ import annotations

import math

from core.data_sources.cross_venue import (
    BinanceFundingMetaHarvester,
    BitgetFundingMetaHarvester,
    BybitFundingMetaHarvester,
)
from core.market_data_ledger import MarketDataLedger


def _stub(harv, mapping):
    """Route _get(url) through a substring->payload fixture map (None if no hit)."""

    def fake_get(url):
        for key, payload in mapping.items():
            if key in url:
                return payload
        return None

    harv._get = fake_get
    return harv


# ------------------------------------------------------------------ Binance
BINANCE_FUNDING_INFO = [
    {"symbol": "ETHUSDT", "fundingIntervalHours": 4,
     "adjustedFundingRateCap": "0.03", "adjustedFundingRateFloor": "-0.03"},
]
BINANCE_PREMIUM_BTC = {
    "symbol": "BTCUSDT", "lastFundingRate": "0.00010000",
    "nextFundingTime": 1751500800000,
}
BINANCE_PREMIUM_ETH = {
    "symbol": "ETHUSDT", "lastFundingRate": "-0.00025000",
    "nextFundingTime": 1751472000000,
}


def _binance():
    return _stub(
        BinanceFundingMetaHarvester(),
        {
            "fundingInfo": BINANCE_FUNDING_INFO,
            "premiumIndex?symbol=BTCUSDT": BINANCE_PREMIUM_BTC,
            "premiumIndex?symbol=ETHUSDT": BINANCE_PREMIUM_ETH,
        },
    )


def test_binance_default_8h_when_not_adjusted():
    rec = _binance().snapshot(["BTC"], force=True)["BTC"]
    assert rec["interval_hours"] == 8.0
    assert rec["rate"] == 0.0001
    assert rec["next_funding_ts"] == 1751500800.0  # ms -> s
    assert rec["stale"] is False


def test_binance_adjusted_interval_override():
    rec = _binance().snapshot(["ETH"], force=True)["ETH"]
    assert rec["interval_hours"] == 4.0
    assert rec["rate"] == -0.00025


# -------------------------------------------------------------------- Bybit
BYBIT_INSTRUMENTS = {
    "retCode": 0,
    "result": {"list": [{"symbol": "BTCUSDT", "fundingInterval": 480}]},
}
BYBIT_TICKERS = {
    "retCode": 0,
    "result": {"list": [{
        "symbol": "BTCUSDT", "fundingRate": "0.00005",
        "nextFundingTime": "1751500800000",
    }]},
}


def _bybit():
    return _stub(
        BybitFundingMetaHarvester(),
        {"instruments-info": BYBIT_INSTRUMENTS, "tickers": BYBIT_TICKERS},
    )


def test_bybit_minutes_to_hours_conversion():
    rec = _bybit().snapshot(["BTC"], force=True)["BTC"]
    assert rec["interval_hours"] == 8.0  # 480 min
    assert rec["rate"] == 5e-05
    assert rec["next_funding_ts"] == 1751500800.0
    assert rec["stale"] is False


# ------------------------------------------------------------------- Bitget
BITGET_FUND_RATE = {
    "code": "00000",
    "data": [{
        "symbol": "BTCUSDT", "fundingRate": "0.0002",
        "fundingRateInterval": "4", "nextUpdate": "1751486400000",
    }],
}


def test_bitget_variable_interval_and_next_update():
    harv = _stub(BitgetFundingMetaHarvester(), {"current-fund-rate?symbol=BTCUSDT": BITGET_FUND_RATE})
    rec = harv.snapshot(["BTC"], force=True)["BTC"]
    assert rec["interval_hours"] == 4.0  # never hardcoded
    assert rec["rate"] == 0.0002
    assert rec["next_funding_ts"] == 1751486400.0
    assert rec["stale"] is False


# ----------------------------------------------------------- error / stale
def test_error_path_yields_stale_frame_no_raise():
    for cls in (BinanceFundingMetaHarvester, BybitFundingMetaHarvester,
                BitgetFundingMetaHarvester):
        harv = cls()
        harv._get = lambda url: None  # feed down
        rec = harv.snapshot(["BTC"], force=True)["BTC"]
        assert rec["stale"] is True
        assert rec["rate"] is None
        assert math.isinf(rec["age_sec"])


def test_raising_get_is_contained():
    harv = BybitFundingMetaHarvester()

    def boom(url):
        raise RuntimeError("network down")

    harv._get = boom
    rec = harv.snapshot(["ETH"], force=True)["ETH"]
    assert rec["stale"] is True and math.isinf(rec["age_sec"])


# ------------------------------------------------------------------ ledger
def test_ledger_funding_frame_btc_eth_all_venues():
    ledger = MarketDataLedger(
        {},
        funding_harvesters={
            "binance": _binance(),
            "bybit": _bybit(),
            "bitget": _stub(BitgetFundingMetaHarvester(),
                            {"current-fund-rate?symbol=BTCUSDT": BITGET_FUND_RATE}),
        },
    )
    frame = ledger.funding(["BTC", "ETH"], force=True)
    assert set(frame.keys()) == {"BTC", "ETH"}
    for coin in ("BTC", "ETH"):
        assert set(frame[coin].keys()) == {"binance", "bybit", "bitget"}
        for rec in frame[coin].values():
            assert {"rate", "interval_hours", "next_funding_ts",
                    "age_sec", "stale"} <= set(rec.keys())
    btc_bn = frame["BTC"]["binance"]
    assert btc_bn["interval_hours"] == 8.0 and btc_bn["stale"] is False
    assert frame["BTC"]["bitget"]["interval_hours"] == 4.0
    # fresh fetch -> finite small age
    assert frame["BTC"]["bybit"]["age_sec"] < 60
    # Bitget fixture had no ETH -> stale record, still present, no raise
    assert frame["ETH"]["bitget"]["stale"] is True


def test_ledger_funding_frame_immutable():
    import pytest

    ledger = MarketDataLedger({}, funding_harvesters={"bybit": _bybit()})
    frame = ledger.funding(["BTC"], force=True)
    with pytest.raises(TypeError):
        frame["BTC"]["bybit"]["rate"] = 1.0


# ------------------------------------------------- coin-aware snapshot cache
def test_snapshot_ttl_hit_fetches_missing_coins():
    # Regression: a TTL-fresh cache holding only BTC must FETCH ETH on request,
    # never return the BTC-only cache (which made ETH permanently stale for any
    # holder of the instance).
    harv = _binance()
    fetches = []
    orig_fetch = harv._fetch
    harv._fetch = lambda c: (fetches.append(c) or orig_fetch(c))

    first = harv.snapshot(["BTC"])
    assert fetches == ["BTC"] and first["BTC"]["rate"] == 0.0001

    second = harv.snapshot(["ETH"])          # TTL hit, ETH missing -> fetch it
    assert fetches == ["BTC", "ETH"]
    assert second["ETH"]["rate"] == -0.00025 and not second["ETH"]["stale"]
    assert "BTC" in second                   # cached BTC retained, not refetched

    harv.snapshot(["BTC", "ETH"])            # fully cached -> zero new fetches
    assert fetches == ["BTC", "ETH"]


def test_ledger_memoizes_funding_harvesters():
    ledger = MarketDataLedger({})
    first = ledger._venue_funding_harvesters()
    second = ledger._venue_funding_harvesters()
    assert first is second
    assert first["binance"] is second["binance"]
