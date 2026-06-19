"""Ingest daily OHLCV (real gitignored parquet OR synthetic) -> NautilusTrader Bars.

Real parquet (data/ohlcv_cache/{BASE}-USDT_1d.parquet) is opened READ-ONLY. Schema:
[ts(int64 unix seconds), open, high, low, close, volume]. Synthetic source comes from
data_ingest.synth_fixture (no network, deterministic).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
from nautilus_trader.persistence.wranglers import BarDataWrangler  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "ohlcv_cache"
_COLS = ["open", "high", "low", "close", "volume"]


def load_frame(base: str, source: str = "synth", n_bars: int = 420) -> pd.DataFrame:
    if source == "parquet":
        p = CACHE / f"{base}-USDT_1d.parquet"
        if not p.exists():
            raise FileNotFoundError(f"missing {p}")
        df = pd.read_parquet(p)  # READ-ONLY
        missing = {"ts", *_COLS} - set(df.columns)
        if missing:
            raise ValueError(f"{p} missing columns {missing}")
        return df
    if source == "synth":
        from ntrader.data_ingest.synth_fixture import synth_daily
        return synth_daily(base, n_bars=n_bars)
    raise ValueError(f"unknown source {source!r}")


def frame_to_bars(df: pd.DataFrame, instrument, bar_type):
    df = df.sort_values("ts").reset_index(drop=True)  # ensure monotonic (NICE #4)
    idx = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
    ohlcv = df[_COLS].astype(float).copy()
    ohlcv.index = pd.DatetimeIndex(idx, name="timestamp")
    return BarDataWrangler(bar_type, instrument).process(ohlcv)


def closes_of(df: pd.DataFrame) -> list[float]:
    return [float(x) for x in df.sort_values("ts")["close"].tolist()]
