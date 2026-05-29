# tests/test_alpha_registry.py
from __future__ import annotations

import numpy as np

from core.alpha_zoo import alphas
from core.alpha_zoo.panel import build_panel
import pandas as pd


def _panel():
    raw = {}
    rng = np.random.default_rng(0)
    for sym in [f"S{i}/USDT" for i in range(12)]:
        n = 120
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    return build_panel(raw, timeframe="1h", horizon=24)


def test_registry_ids_unique_and_tagged():
    ids = [a.id for a in alphas.ALPHAS]
    assert len(ids) == len(set(ids)), "duplicate alpha id"
    for a in alphas.ALPHAS:
        assert a.source in {"K101", "GTJA", "Qlib", "FF"}
        if a.computable:
            assert callable(a.fn)
        else:
            assert a.reason_if_dropped, f"{a.id} dropped without a reason"


def test_n_eff_is_double_n_computable():
    assert alphas.n_eff() == 2 * alphas.n_computable()
    assert alphas.n_computable() == len(alphas.computable_alphas())


def test_every_computable_alpha_returns_panel_shaped_frame():
    p = _panel()
    for a in alphas.computable_alphas():
        out = a.fn(p)
        assert out.shape == p.fields["close"].shape, f"{a.id} wrong shape"
        assert list(out.columns) == p.symbols
