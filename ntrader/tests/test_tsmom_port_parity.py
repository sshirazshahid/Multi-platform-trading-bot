"""HARD GATE: NT Strategy A reproduces the pure-python discrete reference bar-for-bar."""
from __future__ import annotations

import importlib.util
import math
import pathlib

import pytest

from ntrader.backtests._run import run_strategy_a
from ntrader.strategies import tsmom_reference as ref
from ntrader.strategies.tsmom_reference import discrete_events

MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


@pytest.mark.parametrize("base", MAJORS)
def test_bar_for_bar_parity(base):
    res = run_strategy_a(base=base, source="synth", lookback=28, n_bars=420, enable_stop=False)
    expected = discrete_events(res["closes"], 28)
    got = res["decision_bars"]
    assert got == expected, f"{base}: NT {got[:10]} != ref {expected[:10]}"
    kinds = {k for _, k in expected}
    assert {"OPEN", "CLOSE"} <= kinds, f"{base}: vacuous fixture, kinds={kinds}"


def _live_module():
    p = pathlib.Path(__file__).resolve().parents[2] / "core" / "tsmom_signal.py"
    spec = importlib.util.spec_from_file_location("live_tsmom", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_constants_and_sizing_match_live_module():
    live = _live_module()
    assert ref._ANN == live._ANN
    assert ref._TARGET_ANN_VOL == live._TARGET_ANN_VOL
    assert ref._VOL_WINDOW == live._VOL_WINDOW
    assert ref._BASE_SIZE_PCT == live._BASE_SIZE_PCT
    sig = live.TSMOMSignal(exchanges={})
    closes = [100 * (1.01 ** i) * (1 + 0.01 * math.sin(i)) for i in range(40)]
    assert abs(ref.vol_scaled_size_pct(closes) - sig._vol_scaled_size_pct(closes)) < 1e-9
