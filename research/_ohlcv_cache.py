"""Shared OHLCV panel loader for research screens (read-only, never imported by the bot).

WHY THIS EXISTS: every screen in research/ re-inlines its own
``pd.read_parquet(...)`` plus a hand-rolled epoch-unit guard
(``screen_overnight_btc_seasonality.py:127`` is the exemplar). That duplication
is how a timestamp-unit bug reaches three scripts before anyone notices. One
loader, one guard, one resampler.

TWO SOURCES, ONE SCHEMA ``[ts, open, high, low, close, volume]``:
  * ``data/ohlcv_cache/<BASE>-USDT_1h.parquet`` — the bot's own venue cache
    (crypto perps; ~28.3k 1h bars back to 2023-05-26).
  * ``data/tv_cache/<EXCHANGE>_<TICKER>_<res>.parquet`` — deep TradingView
    history harvested by ``scripts/tv_client.py`` (SPY to 1993, EURUSD to 1971,
    GC1! to 1975, CL1! to 1983). Harvest with ``--harvest``.

``ts`` is epoch SECONDS. The house guard (``if t > 10**12: t //= 1000``) is
applied on load because both caches have historically held millisecond rows.

VOLUME IS NOT TRUSTWORTHY HERE: TradingView serves volume 0.0 for some index
and CFD series (``scripts/tv_client.py`` docstring). Nothing downstream may
key off volume.

Run: venv/Scripts/python.exe research/_ohlcv_cache.py --harvest
     venv/Scripts/python.exe research/_ohlcv_cache.py          (inventory)
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VENUE_CACHE = ROOT / "data" / "ohlcv_cache"
TV_CACHE = ROOT / "data" / "tv_cache"
COLS = ["ts", "open", "high", "low", "close", "volume"]

# Seconds per bar. Resampling only ever goes COARSER than the stored base.
TF_SEC = {"1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400, "1w": 604800}

# The six Lane-1 panels: the owner's named asset classes, each mapped to the
# deepest series actually retrievable. (source, symbol, base resolution)
PANELS = {
    "BTC": ("venue", "BTC-USDT", "1h"),
    "ETH": ("venue", "ETH-USDT", "1h"),
    "SPY": ("tv", "AMEX:SPY", "1D"),
    "GOLD": ("tv", "COMEX:GC1!", "1D"),
    "CRUDE": ("tv", "NYMEX:CL1!", "1D"),
    "EURUSD": ("tv", "FX:EURUSD", "1D"),
}
# Intraday companions for the TV panels (far shallower than the daily series).
TV_INTRADAY = {
    "SPY": "AMEX:SPY",
    "GOLD": "COMEX:GC1!",
    "CRUDE": "NYMEX:CL1!",
    "EURUSD": "FX:EURUSD",
}


def _tv_path(symbol: str, resolution: str) -> pathlib.Path:
    return TV_CACHE / f"{symbol.replace(':', '_')}_{resolution}.parquet"


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """House schema + epoch-SECONDS guard + strict chronological de-dup."""
    out = df.loc[:, [c for c in COLS if c in df.columns]].copy()
    for missing in set(COLS) - set(out.columns):
        out[missing] = 0.0
    out["ts"] = out["ts"].astype("int64")
    # Both caches have held millisecond rows in the past. Guard, don't assume.
    ms = out["ts"] > 10**12
    if ms.any():
        out.loc[ms, "ts"] = out.loc[ms, "ts"] // 1000
    out = out[COLS].dropna(subset=["open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return out.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)


def load_base(name: str) -> pd.DataFrame:
    """Load a panel's stored BASE series (deepest history actually on disk)."""
    if name not in PANELS:
        raise KeyError(f"unknown panel {name!r}; known: {sorted(PANELS)}")
    kind, symbol, _ = PANELS[name]
    path = VENUE_CACHE / f"{symbol}_1h.parquet" if kind == "venue" else _tv_path(symbol, "1D")
    if not path.exists():
        raise FileNotFoundError(
            f"{name}: missing {path}\n"
            "  venue panels: python scripts/backfill_universe_ohlcv.py\n"
            "  TV panels:    venv/Scripts/python.exe research/_ohlcv_cache.py --harvest"
        )
    return _normalise(pd.read_parquet(path))


def load_intraday(name: str) -> pd.DataFrame | None:
    """Load the 1h companion for a TV panel, or None if not harvested."""
    if name not in TV_INTRADAY:
        return None
    path = _tv_path(TV_INTRADAY[name], "60")
    return _normalise(pd.read_parquet(path)) if path.exists() else None


def resample(df: pd.DataFrame, target_sec: int) -> pd.DataFrame:
    """Aggregate to a coarser bar on FIXED epoch boundaries.

    Fixed-boundary bucketing (``ts // target * target``) rather than
    calendar/venue sessions: the chop-gate fix (975e6f3) landed exactly because
    venue-bucketed "1d" candles are not UTC days. A partial trailing bucket is
    DROPPED — a forming bar is the classic look-ahead.
    """
    if df.empty:
        return df.reset_index(drop=True)
    base_sec = int(df["ts"].diff().median()) if len(df) > 2 else target_sec
    if target_sec <= base_sec:
        return df.reset_index(drop=True)
    bucket = (df["ts"] // target_sec) * target_sec
    g = df.assign(bucket=bucket).groupby("bucket", sort=True)
    out = pd.DataFrame(
        {
            "ts": g["ts"].first().index.to_numpy().astype("int64"),
            "open": g["open"].first().to_numpy(),
            "high": g["high"].max().to_numpy(),
            "low": g["low"].min().to_numpy(),
            "close": g["close"].last().to_numpy(),
            "volume": g["volume"].sum().to_numpy(),
        }
    ).reset_index(drop=True)
    # Drop a trailing bucket the source data cannot have completed.
    if len(out) > 1:
        last_bucket_end = int(out["ts"].iloc[-1]) + target_sec
        if int(df["ts"].iloc[-1]) + base_sec < last_bucket_end:
            out = out.iloc[:-1].reset_index(drop=True)
    return out


def load_panel(name: str, timeframe: str) -> pd.DataFrame:
    """Load ``name`` at ``timeframe``, resampling from the deepest usable base.

    For TV panels the 1h companion is only ~6.3k bars while the daily series
    reaches 1971-1993; so 1h/4h come from the intraday file and 1d/1w from the
    daily file. Crypto resamples everything from its 28.3k-bar 1h base.
    """
    if timeframe not in TF_SEC:
        raise KeyError(f"unknown timeframe {timeframe!r}; known: {sorted(TF_SEC)}")
    target = TF_SEC[timeframe]
    if target < TF_SEC["1d"] and name in TV_INTRADAY:
        intraday = load_intraday(name)
        if intraday is None or intraday.empty:
            raise FileNotFoundError(
                f"{name} {timeframe}: 1h companion not harvested "
                "(research/_ohlcv_cache.py --harvest)"
            )
        return resample(intraday, target)
    return resample(load_base(name), target)


def harvest() -> None:
    """Pull the TV panels to data/tv_cache (deep history, no LLM context transit)."""
    from scripts.tv_client import fetch_ohlcv

    TV_CACHE.mkdir(parents=True, exist_ok=True)
    jobs = [(s, r) for r in ("1D", "60") for s in TV_INTRADAY.values()]
    for symbol, res in jobs:
        path = _tv_path(symbol, res)
        try:
            df = _normalise(fetch_ohlcv(symbol, res, countback=30000, timeout=120))
        except Exception as exc:  # noqa: BLE001 — best-effort harvest; report and continue
            print(f"{symbol:14s} {res:3s} FAIL {type(exc).__name__}: {str(exc)[:100]}")
            continue
        df.to_parquet(path, index=False)
        print(f"{symbol:14s} {res:3s} {len(df):7d} bars -> {path.name}")


def _inventory() -> None:
    for panel in PANELS:
        for tf in ("1h", "4h", "1d", "1w"):
            try:
                d = load_panel(panel, tf)
                lo = pd.to_datetime(int(d["ts"].min()), unit="s").date()
                hi = pd.to_datetime(int(d["ts"].max()), unit="s").date()
                print(f"{panel:7s} {tf:3s} {len(d):7d} bars  {lo} -> {hi}")
            except Exception as exc:  # noqa: BLE001
                print(f"{panel:7s} {tf:3s} {type(exc).__name__}: {str(exc)[:80]}")


if __name__ == "__main__":
    if "--harvest" in sys.argv:
        harvest()
    else:
        _inventory()
