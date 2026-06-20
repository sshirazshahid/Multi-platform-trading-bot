"""Tests for research/data_io.py — CSV + candle ingestion for the research labs."""
from __future__ import annotations

import pytest

from research import data_io


def test_write_then_load_closes_roundtrip(tmp_path):
    p = tmp_path / "c.csv"
    data_io.write_closes_csv(p, [100.0, 101.5, 99.0])
    assert data_io.load_closes_csv(p) == [100.0, 101.5, 99.0]


def test_write_with_timestamps_and_load(tmp_path):
    p = tmp_path / "c.csv"
    data_io.write_closes_csv(p, [10.0, 11.0], timestamps=["2026-01-01", "2026-01-02"])
    assert data_io.load_closes_csv(p) == [10.0, 11.0]


def test_load_closes_missing_column(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("open,high\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        data_io.load_closes_csv(p)


def test_load_closes_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        data_io.load_closes_csv(tmp_path / "nope.csv")


def test_load_closes_skips_blank_rows(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("close\n100\n\n101\n", encoding="utf-8")
    assert data_io.load_closes_csv(p) == [100.0, 101.0]


def test_load_funding_csv(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("rate\n0.0001\n-0.0002\n", encoding="utf-8")
    assert data_io.load_funding_csv(p) == [0.0001, -0.0002]


def test_closes_from_dicts_sorts_chronologically():
    # Crypto.com style: newest-first; must come back oldest-first.
    candles = [
        {"timestamp": "2026-06-20T00:00:00Z", "close": "63360.0"},
        {"timestamp": "2026-06-18T00:00:00Z", "close": "62886.2"},
        {"timestamp": "2026-06-19T00:00:00Z", "close": "63485.7"},
    ]
    assert data_io.closes_from_candles(candles) == [62886.2, 63485.7, 63360.0]


def test_closes_from_ohlcv_lists():
    # ccxt OHLCV: [ts, o, h, l, c, v], newest-first input
    candles = [
        [3000, 1, 2, 0.5, 30.0, 9],
        [1000, 1, 2, 0.5, 10.0, 9],
        [2000, 1, 2, 0.5, 20.0, 9],
    ]
    assert data_io.closes_from_candles(candles) == [10.0, 20.0, 30.0]


def test_closes_from_candles_preserve_order():
    candles = [{"close": 3}, {"close": 1}, {"close": 2}]
    # no timestamps -> index order preserved when chronological sort is by index
    assert data_io.closes_from_candles(candles, chronological=False) == [3.0, 1.0, 2.0]


def test_closes_from_candles_errors():
    with pytest.raises(ValueError):
        data_io.closes_from_candles([])
    with pytest.raises(ValueError):
        data_io.closes_from_candles([{"open": 1}])  # missing close
    with pytest.raises(ValueError):
        data_io.closes_from_candles([[1, 2]])  # ohlcv too short


def test_end_to_end_into_dca_lab(tmp_path):
    """data_io output pipes straight into the DCA lab."""
    from research import dca_rebalance_lab as lab
    p = tmp_path / "px.csv"
    data_io.write_closes_csv(p, [100 - i for i in range(30)])
    closes = data_io.load_closes_csv(p)
    r = lab.simulate_dca(closes, capital=1000.0, interval_bars=1)
    assert r.extra["n_buys"] == 30
    assert r.invested == pytest.approx(1000.0)
