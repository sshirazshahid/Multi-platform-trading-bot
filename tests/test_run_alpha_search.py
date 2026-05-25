# tests/test_run_alpha_search.py
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.alpha_zoo.alphas import AlphaDef
from core.alpha_zoo.panel import build_panel
from core.alpha_zoo import operators as op
from scripts.run_alpha_search import run_search


def _panel(n=900, n_sym=15):
    rng = np.random.default_rng(5)
    raw = {}
    for sym in [f"S{i}/USDT" for i in range(n_sym)]:
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    return build_panel(raw, timeframe="1h", horizon=24)


def test_run_search_emits_verdict_and_full_table():
    p = _panel()
    registry = [
        AlphaDef("MOM10", "Qlib", lambda pl: op.rank(op.delta(pl.fields["close"], 10))),
        AlphaDef("REV1", "Qlib", lambda pl: -1.0 * op.delta(pl.fields["close"], 1)),
    ]
    result = run_search(p, registry)
    assert "verdict" in result and result["verdict"] in {"EDGE_FOUND", "NO_EDGE"}
    assert len(result["table"]) == 2
    for row in result["table"]:
        assert {"id", "ir_is", "category", "oos_sharpe", "dsr", "fdr_p"} <= set(row)
    assert "pbo" in result and "n_eff" in result


def test_run_search_writes_report(tmp_path):
    p = _panel()
    registry = [AlphaDef("MOM10", "Qlib",
                         lambda pl: op.rank(op.delta(pl.fields["close"], 10)))]
    result = run_search(p, registry, report_dir=tmp_path)
    md = list(tmp_path.glob("alpha_search_*.md"))
    js = list(tmp_path.glob("alpha_search_*.json"))
    assert md and js
    loaded = json.loads(js[0].read_text())
    assert loaded["verdict"] == result["verdict"]
