# tests/test_alpha_panel.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo.panel import Panel, build_panel, split_panel


def _raw(n=200, start_ts=1_700_000_000, step=3600, base=100.0, drift=0.1):
    ts = np.arange(start_ts, start_ts + n * step, step, dtype="int64")
    close = base + drift * np.arange(n)
    return pd.DataFrame({
        "ts": ts, "open": close, "high": close + 1.0,
        "low": close - 1.0, "close": close, "volume": np.full(n, 1000.0),
    })


def test_build_panel_aligns_symbols_and_derives_fields():
    raw = {"AAA/USDT": _raw(drift=0.1), "BBB/USDT": _raw(drift=-0.2)}
    p = build_panel(raw, timeframe="1h", horizon=24)
    assert set(p.symbols) == {"AAA/USDT", "BBB/USDT"}
    assert list(p.fields["close"].columns) == p.symbols
    # vwap = (h+l+c)/3 ; here h=c+1, l=c-1 -> vwap == close
    assert np.allclose(p.fields["vwap"].values, p.fields["close"].values)
    # forward 24-bar return present for all but last 24 rows
    assert p.fwd_ret.iloc[:-24].notna().all().all()
    assert p.fwd_ret.iloc[-24:].isna().all().all()


def test_adv_is_rolling_dollar_volume():
    raw = {"AAA/USDT": _raw()}
    p = build_panel(raw, timeframe="1h", horizon=24)
    adv5 = p.adv(5)
    # close*volume constant-ish; rolling(5) of (close*1000)
    expected = (p.fields["close"] * p.fields["volume"]).rolling(5).mean()
    assert np.allclose(adv5.values, expected.values, equal_nan=True)


def test_split_panel_respects_embargo():
    raw = {"AAA/USDT": _raw(n=200), "BBB/USDT": _raw(n=200)}
    p = build_panel(raw, timeframe="1h", horizon=24)
    is_p, oos_p = split_panel(p, frac=0.6, embargo=24)
    # IS = first 120 bars; OOS starts at 120+24 = 144
    assert len(is_p.ts) == 120
    assert oos_p.ts[0] == p.ts[144]
    # no timestamp overlap
    assert set(is_p.ts).isdisjoint(set(oos_p.ts))
