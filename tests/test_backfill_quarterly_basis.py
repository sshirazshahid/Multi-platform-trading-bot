"""Offline tests for scripts/backfill_quarterly_basis.py parsing + merge logic."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backfill_quarterly_basis import merge_parquet, parse_klines  # noqa: E402

# Real Binance continuousKlines row shape (numerics as strings, openTime ms first).
RAW = [
    [1612483200000, "1665.00", "1670.10", "1660.00", "1668.30", "1000",
     1612486799999, "0", 100, "0", "0", "0"],
    [1612486800000, "1668.30", "1675.00", "1665.20", "1671.90", "1100",
     1612490399999, "0", 100, "0", "0", "0"],
]


def test_parse_klines():
    bars = parse_klines(RAW)
    assert len(bars) == 2
    assert bars[0] == (1612483200, 1665.00, 1670.10, 1660.00, 1668.30)
    assert bars[1][0] - bars[0][0] == 3600
    assert parse_klines([]) == []
    assert parse_klines(None) == []
    assert len(parse_klines(RAW + [["bad"]])) == 2


def test_merge_parquet_idempotent(tmp_path):
    path = str(tmp_path / "ETH-USDT_qtrcont_1h.parquet")
    bars = parse_klines(RAW)
    assert merge_parquet(path, bars) == (2, 2)
    assert merge_parquet(path, bars) == (2, 0)
    assert merge_parquet(path, [(1612490400, 1.0, 2.0, 0.5, 1.5)]) == (3, 1)
    df = pd.read_parquet(path)
    assert list(df["ts"]) == sorted(df["ts"].tolist())
