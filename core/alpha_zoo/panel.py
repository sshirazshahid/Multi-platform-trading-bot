# core/alpha_zoo/panel.py
"""Cross-sectional OHLCV panel for the alpha search.

A `Panel` holds wide (T bars × N symbols) DataFrames — one per field —
all sharing a common ascending integer `ts` (unix seconds) index and the
same symbol columns. Time-series alpha operators act down the rows (per
symbol); cross-sectional operators act across the columns (per bar).

`build_panel` aligns per-symbol raw OHLCV onto the union timestamp grid and
derives vwap/returns; `adv(d)` is rolling dollar-volume; `fwd_ret` is the
single pre-registered forward return (close[t+horizon]/close[t] - 1).
`split_panel` does the chronological 60/40 split with an embargo so no
forward-label window straddles the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_OHLCV = ("open", "high", "low", "close", "volume")


@dataclass
class Panel:
    fields: dict[str, pd.DataFrame]   # field -> (T×N) DataFrame, index=ts, cols=symbols
    fwd_ret: pd.DataFrame             # (T×N) forward `horizon`-bar return
    symbols: list[str]
    ts: np.ndarray                    # (T,) int64 unix seconds
    horizon: int

    def adv(self, d: int) -> pd.DataFrame:
        """Rolling `d`-bar mean dollar volume (close × volume), per symbol."""
        dollar = self.fields["close"] * self.fields["volume"]
        return dollar.rolling(int(d), min_periods=int(d)).mean()


def build_panel(raw: dict[str, pd.DataFrame], *, timeframe: str = "1h",
                horizon: int = 24) -> Panel:
    """Build a `Panel` from {symbol -> raw OHLCV DataFrame}.

    Each raw frame has columns ts/open/high/low/close/volume (ts = unix
    seconds). Symbols are aligned on the union of timestamps (outer join);
    missing cells stay NaN (staggered listings) and are masked per-bar
    downstream.
    """
    symbols = sorted(raw)
    per_field: dict[str, dict[str, pd.Series]] = {f: {} for f in _OHLCV}
    for sym in symbols:
        df = raw[sym].copy()
        df["ts"] = df["ts"].astype("int64")
        df = df.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
        for f in _OHLCV:
            per_field[f][sym] = df[f].astype(float)

    fields: dict[str, pd.DataFrame] = {}
    for f in _OHLCV:
        wide = pd.DataFrame(per_field[f]).sort_index()
        wide = wide.reindex(columns=symbols)
        fields[f] = wide

    ts = fields["close"].index.to_numpy(dtype="int64")
    fields["vwap"] = (fields["high"] + fields["low"] + fields["close"]) / 3.0
    fields["returns"] = fields["close"].pct_change()

    close = fields["close"]
    fwd_ret = close.shift(-int(horizon)) / close - 1.0

    return Panel(fields=fields, fwd_ret=fwd_ret, symbols=symbols, ts=ts,
                 horizon=int(horizon))


def _slice(panel: Panel, lo: int, hi: int) -> Panel:
    sl = slice(lo, hi)
    fields = {f: df.iloc[sl] for f, df in panel.fields.items()}
    return Panel(fields=fields, fwd_ret=panel.fwd_ret.iloc[sl],
                 symbols=panel.symbols, ts=panel.ts[lo:hi], horizon=panel.horizon)


def split_panel(panel: Panel, *, frac: float = 0.6,
                embargo: int = 24) -> tuple[Panel, Panel]:
    """Chronological split. IS = [0, cut); OOS = [cut+embargo, T).

    `embargo` (>= horizon) drops the bars whose IS forward-labels would
    overlap OOS feature windows, so Stage-2 is genuinely out-of-sample.
    """
    T = len(panel.ts)
    cut = int(T * float(frac))
    is_p = _slice(panel, 0, cut)
    oos_p = _slice(panel, cut + int(embargo), T)
    return is_p, oos_p
