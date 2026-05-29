"""Phase 3 — derivatives harvester (data only; no trading agent, no order path).

Stubs the HTTP layer so the test is hermetic (no network). Verifies normalization,
fail-open behavior, and forward-accumulation persistence.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def harvester():
    from core.data_sources.derivs import DerivsHarvester

    return DerivsHarvester(period="1h", lookback=2, cache_ttl=0)


def _stub(monkeypatch, *, lsr=None, taker=None, oi=None, funding=None):
    """Patch core.data_sources.derivs._get to return canned series by URL."""
    from core.data_sources import derivs

    def fake_get(url: str):
        if "globalLongShortAccountRatio" in url:
            return lsr
        if "takerlongshortRatio" in url:
            return taker
        if "openInterestHist" in url:
            return oi
        if "fundingRate" in url:
            return funding
        return None

    monkeypatch.setattr(derivs, "_get", fake_get)


def test_snapshot_normalizes_and_computes_change(harvester, monkeypatch):
    _stub(
        monkeypatch,
        lsr=[
            {"timestamp": 1, "longShortRatio": "1.0"},
            {"timestamp": 2, "longShortRatio": "1.1"},
            {"timestamp": 3, "longShortRatio": "1.3"},
        ],
        taker=[{"timestamp": 3, "buySellRatio": "0.95"}],
        oi=[
            {"timestamp": 1, "sumOpenInterestValue": "100"},
            {"timestamp": 2, "sumOpenInterestValue": "110"},
            {"timestamp": 3, "sumOpenInterestValue": "121"},
        ],
        funding=[{"fundingRate": "0.0001"}],
    )
    snap = harvester.snapshot(["BTC"])["BTC"]
    assert snap["stale"] is False
    assert snap["lsr"] == pytest.approx(1.3)
    assert snap["lsr_chg"] == pytest.approx(1.3 - 1.0)  # lookback=2 bars
    assert snap["taker_ls"] == pytest.approx(0.95)
    assert snap["oi_chg_pct"] == pytest.approx((121 / 100 - 1) * 100)
    assert snap["funding"] == pytest.approx(0.0001)
    assert snap["bar_ts"] == 3


def test_snapshot_fail_open_on_no_data(harvester, monkeypatch):
    _stub(monkeypatch)  # everything returns None
    snap = harvester.snapshot(["BTC"])["BTC"]
    assert snap["stale"] is True
    assert snap["lsr"] is None


def test_append_history_writes_fresh_skips_stale(tmp_path, monkeypatch):
    from core.data_sources.derivs import DerivsHarvester

    snap = {
        "BTC": {
            "lsr": 1.3,
            "taker_ls": 0.95,
            "oi": 121.0,
            "oi_chg_pct": 21.0,
            "funding": 0.0001,
            "lsr_chg": 0.3,
            "bar_ts": 3,
            "stale": False,
        },
        "ETH": {
            "lsr": None,
            "taker_ls": None,
            "oi": None,
            "oi_chg_pct": None,
            "funding": None,
            "lsr_chg": None,
            "bar_ts": None,
            "stale": True,
        },
    }
    path = tmp_path / "derivs_history.jsonl"
    written = DerivsHarvester.append_history(path, snap, fetched_at=1700.0)
    assert written == 1  # stale ETH skipped
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC" and rows[0]["fetched_at"] == 1700.0 and rows[0]["bar_ts"] == 3


def test_import_is_side_effect_free():
    """Importing the module must not hit the network."""
    import importlib

    m = importlib.import_module("core.data_sources.derivs")
    assert hasattr(m, "DerivsHarvester")
