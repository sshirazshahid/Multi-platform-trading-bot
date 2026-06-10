"""Offline tests for scripts/harvest_tv.py (TradingView/CoinGecko forward-harvester).

Mirrors the skew/L2 harvester test approach: parsing, hourly bucketing, atomic
flush — no network.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("harvest_tv", ROOT / "scripts" / "harvest_tv.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HT = _load()


def test_parse_global_extracts_dominance_and_total():
    payload = {
        "data": {
            "market_cap_percentage": {"usdt": 8.5, "btc": 56.0, "eth": 10.0},
            "total_market_cap": {"usd": 2.2e12},
        }
    }
    out = HT.parse_global(payload)
    assert out == {"usdt_d": 8.5, "btc_d": 56.0, "eth_d": 10.0, "total_usd": 2.2e12}


def test_parse_global_malformed_returns_none():
    assert HT.parse_global({}) is None
    assert HT.parse_global({"data": {"market_cap_percentage": {}}}) is None


def test_parse_scanner_maps_base_to_rating():
    payload = {
        "data": [
            {"s": "BINANCE:BTCUSDT", "d": [-0.42]},
            {"s": "BINANCE:ETHUSDT", "d": [0.13]},
            {"s": "BINANCE:XXXUSDT", "d": [None]},
        ]
    }
    out = HT.parse_scanner(payload)
    assert out == {"BTC": -0.42, "ETH": 0.13}


def test_parse_scanner_malformed_returns_empty():
    assert HT.parse_scanner({}) == {}
    assert HT.parse_scanner({"data": [{"bogus": 1}]}) == {}


def test_hour_bucketing():
    assert HT._hour(1781103599.9) == 1781100000
    assert HT._hour(1781103600.0) == 1781103600


def test_flush_writes_completed_hours_only(tmp_path, monkeypatch):
    monkeypatch.setattr(HT, "HIST", tmp_path / "tv_history.jsonl")
    monkeypatch.setattr(HT, "STATUS", tmp_path / "tv_status.json")
    h0, h1 = 1781100000, 1781103600
    buckets = {
        h0: [
            {
                "usdt_d": 8.0,
                "btc_d": 56.0,
                "eth_d": 10.0,
                "total_usd": 2.0e12,
                "ratings": {"BTC": -0.4},
            },
            {
                "usdt_d": 9.0,
                "btc_d": 58.0,
                "eth_d": 11.0,
                "total_usd": 2.4e12,
                "ratings": {"BTC": -0.2, "ETH": 0.1},
            },
        ],
        h1: [{"usdt_d": 8.5, "btc_d": 57.0, "eth_d": 10.5, "total_usd": 2.2e12, "ratings": {}}],
    }
    n = HT._flush(buckets, now_hour=h1)
    assert n == 1
    lines = (tmp_path / "tv_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["hour"] == h0
    assert rec["n_polls"] == 2
    assert abs(rec["usdt_d"] - 8.5) < 1e-9  # mean of 8.0, 9.0
    assert abs(rec["ratings"]["BTC"] - (-0.3)) < 1e-9  # mean of -0.4, -0.2
    assert abs(rec["ratings"]["ETH"] - 0.1) < 1e-9  # single poll carrying ETH
    assert h0 not in buckets and h1 in buckets  # current hour still buffered


def test_flush_appends_preserving_existing(tmp_path, monkeypatch):
    hist = tmp_path / "tv_history.jsonl"
    hist.write_text('{"hour": 1}\n', encoding="utf-8")
    monkeypatch.setattr(HT, "HIST", hist)
    monkeypatch.setattr(HT, "STATUS", tmp_path / "tv_status.json")
    buckets = {
        1781100000: [
            {"usdt_d": 8.0, "btc_d": 56.0, "eth_d": 10.0, "total_usd": 2e12, "ratings": {}}
        ]
    }
    HT._flush(buckets, now_hour=1781103600)
    lines = hist.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"hour": 1}
    assert not (tmp_path / "tv_history.jsonl.tmp").exists()  # atomic temp removed


def test_flush_empty_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(HT, "HIST", tmp_path / "tv_history.jsonl")
    monkeypatch.setattr(HT, "STATUS", tmp_path / "tv_status.json")
    assert HT._flush({}, now_hour=1781100000) == 0
    assert not (tmp_path / "tv_history.jsonl").exists()
