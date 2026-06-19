"""Deterministic synthetic daily OHLCV — no network, seeded, reproducible.

Single source of truth for every test/backtest that must run WITHOUT the real
gitignored parquet or network access. Schema matches data/ohlcv_cache/*.parquet
exactly: columns [ts, open, high, low, close, volume], ts = int64 unix SECONDS,
one row per UTC day.

The price path is a geometric random walk with a slow regime-switching drift, so
each series contains BOTH sustained uptrends and downtrends — this guarantees the
discrete TSMOM rule produces both OPEN and CLOSE events (otherwise the parity test
would be vacuous).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

# 2023-06-01T00:00:00Z — roughly matches the real BTC-USDT_1d cache start.
_START_TS = 1685577600
_DAY = 86_400


def _seed_for(base: str) -> int:
    return sum(ord(c) for c in base) * 7 + 101


def synth_daily(base: str, n_bars: int = 420, start_price: float | None = None) -> pd.DataFrame:
    """Return a deterministic daily OHLCV DataFrame for ``base``.

    Deterministic per (base, n_bars): same inputs -> identical frame.
    """
    rng = np.random.default_rng(_seed_for(base) + n_bars)
    px0 = float(start_price if start_price is not None else 100.0 + (_seed_for(base) % 900))

    closes = np.empty(n_bars, dtype=float)
    px = px0
    for i in range(n_bars):
        # Regime-switching drift: ~120-day cycle -> alternating up/down trends.
        drift = 0.004 * math.sin(2.0 * math.pi * i / 120.0)
        shock = float(rng.normal(0.0, 0.02))      # ~2% daily vol
        px *= math.exp(drift + shock)
        closes[i] = px

    # Build OHLCV around the close path deterministically.
    opens = np.empty(n_bars, dtype=float)
    highs = np.empty(n_bars, dtype=float)
    lows = np.empty(n_bars, dtype=float)
    vols = np.empty(n_bars, dtype=float)
    prev = px0
    for i in range(n_bars):
        c = closes[i]
        o = prev
        hi = max(o, c) * (1.0 + abs(float(rng.normal(0.0, 0.005))))
        lo = min(o, c) * (1.0 - abs(float(rng.normal(0.0, 0.005))))
        opens[i] = o
        highs[i] = hi
        lows[i] = lo
        vols[i] = 1_000.0 + abs(float(rng.normal(0.0, 250.0)))
        prev = c

    ts = np.arange(n_bars, dtype=np.int64) * _DAY + _START_TS
    return pd.DataFrame(
        {
            "ts": ts.astype(np.int64),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        }
    )


def synth_all(n_bars: int = 420) -> dict[str, pd.DataFrame]:
    """All five majors as a {base: DataFrame} map."""
    return {b: synth_daily(b, n_bars=n_bars) for b in MAJORS}
