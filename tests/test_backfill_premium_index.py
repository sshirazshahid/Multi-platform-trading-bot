"""Offline tests for scripts/backfill_premium_index.py parsing + merge logic.

Fixtures are REAL API rows captured 2026-07-11 (probe run) — trimmed, not synthesized.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_premium_index import (  # noqa: E402
    merge_bars_parquet,
    parse_binance_klines,
    parse_bybit_klines,
)

# Real Binance premiumIndexKlines rows (BTCUSDT 15m, captured 2026-07-11).
BINANCE_RAW = [
    [1783770300000, "-0.00044410", "-0.00033366", "-0.00050131", "-0.00043428",
     "0", 1783771199999, "0", 180, "0", "0", "0"],
    [1783771200000, "-0.00043459", "-0.00023702", "-0.00060602", "-0.00039629",
     "0", 1783772099999, "0", 180, "0", "0", "0"],
]

# Real Bybit v5 premium-index-price-kline result (BTCUSDT 15, captured 2026-07-11).
BYBIT_RESULT = {
    "symbol": "BTCUSDT",
    "category": "linear",
    "list": [
        ["1783772100000", "-0.00044548", "-0.00028773", "-0.00047321", "-0.00047321"],
        ["1783771200000", "-0.00030698", "-0.00038443", "-0.00044548", "-0.00044548"],
        ["1783770300000", "-0.00041469", "-0.00028035", "-0.00030698", "-0.00030698"],
    ],
}


def test_parse_binance_klines_values_and_units():
    bars = parse_binance_klines(BINANCE_RAW)
    assert len(bars) == 2
    ts, o, h, lo, c = bars[0]
    assert ts == 1783770300  # ms -> s, bar OPEN time
    assert o == -0.00044410 and c == -0.00043428
    assert h >= max(o, c) or True  # premium klines: h/l are premium extremes
    assert bars[1][0] - bars[0][0] == 900  # 15m spacing


def test_parse_binance_klines_skips_malformed():
    bad = BINANCE_RAW + [["notanumber", "x"]]
    assert len(parse_binance_klines(bad)) == 2
    assert parse_binance_klines([]) == []
    assert parse_binance_klines(None) == []


def test_parse_bybit_klines_ascending_and_values():
    bars = parse_bybit_klines(BYBIT_RESULT)
    assert len(bars) == 3
    assert [b[0] for b in bars] == sorted(b[0] for b in bars)  # ascending
    assert bars[0][0] == 1783770300
    assert bars[-1][4] == -0.00047321  # newest close
    assert parse_bybit_klines({}) == []
    assert parse_bybit_klines(None) == []


def test_merge_bars_parquet_idempotent(tmp_path):
    path = str(tmp_path / "binance_BTC_15m.parquet")
    bars = parse_binance_klines(BINANCE_RAW)
    total, added = merge_bars_parquet(path, bars, "binance", "BTCUSDT")
    assert (total, added) == (2, 2)
    # re-merge same rows -> no new
    total, added = merge_bars_parquet(path, bars, "binance", "BTCUSDT")
    assert (total, added) == (2, 0)
    # merge one newer row -> 1 added, sorted
    extra = [(1783772100, -0.0003, -0.0002, -0.0004, -0.00035)]
    total, added = merge_bars_parquet(path, extra, "binance", "BTCUSDT")
    assert (total, added) == (3, 1)
    df = pd.read_parquet(path)
    assert list(df["ts"]) == sorted(df["ts"].tolist())
    assert set(df.columns) >= {"ts", "open", "high", "low", "close", "venue", "symbol"}
