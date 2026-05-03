"""Agent backtester."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _ohlcv_cache_dir(tmp_path: Path, exchange: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    p = tmp_path / "data" / "ohlcv_cache" / exchange / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_cache(p: Path, tf: str, n: int, slope: float, base: float = 100.0):
    rng = np.random.RandomState(42 + len(tf))
    close = base + np.arange(n) * slope + rng.randn(n) * 1.5
    bars = []
    t0 = int(time.time() * 1000) - n * 3_600_000
    step_ms = {"5m": 5*60_000, "15m": 15*60_000, "1h": 3600_000, "4h": 4*3600_000}[tf]
    for i, c in enumerate(close):
        bars.append([t0 + i * step_ms,
                     float(c - 0.05),
                     float(c + abs(rng.randn()) * 0.5),
                     float(c - abs(rng.randn()) * 0.5),
                     float(c), 1000.0])
    (p / f"{tf}.json").write_text(json.dumps(bars), encoding="utf-8")


def test_simulate_exit_long_hits_tp():
    from scripts.agent_backtest import _simulate_exit
    from core.agents.base_agent import Proposal
    p = Proposal(agent_id="X", symbol="X", side="buy",
                 entry=100, sl=98, tp=104, confidence=0.7, reason="t")
    future = pd.Series([100, 101, 103, 105, 106])  # tp at 104 hit on bar 3
    pnl, reason = _simulate_exit(p, future, max_bars=10)
    assert reason == "tp"
    assert pnl > 0


def test_simulate_exit_long_hits_sl():
    from scripts.agent_backtest import _simulate_exit
    from core.agents.base_agent import Proposal
    p = Proposal(agent_id="X", symbol="X", side="buy",
                 entry=100, sl=98, tp=104, confidence=0.7, reason="t")
    future = pd.Series([100, 99, 97, 96])  # sl at 98 hit on bar 2
    pnl, reason = _simulate_exit(p, future, max_bars=10)
    assert reason == "sl"
    assert pnl < 0


def test_simulate_exit_short_hits_tp():
    from scripts.agent_backtest import _simulate_exit
    from core.agents.base_agent import Proposal
    p = Proposal(agent_id="X", symbol="X", side="sell",
                 entry=100, sl=102, tp=96, confidence=0.7, reason="t")
    future = pd.Series([100, 99, 97, 95])
    pnl, reason = _simulate_exit(p, future, max_bars=10)
    assert reason == "tp"
    assert pnl > 0


def test_simulate_exit_time_exit_partial():
    from scripts.agent_backtest import _simulate_exit
    from core.agents.base_agent import Proposal
    p = Proposal(agent_id="X", symbol="X", side="buy",
                 entry=100, sl=90, tp=110, confidence=0.7, reason="t")
    future = pd.Series([100, 101, 102, 101, 102, 101])
    pnl, reason = _simulate_exit(p, future, max_bars=5)
    assert reason == "time"


def test_load_ohlcv_cache_returns_none_when_missing(tmp_path, monkeypatch):
    from scripts.agent_backtest import _load_ohlcv_cache, ROOT
    # Point ROOT to tmp so cache lookups go there
    monkeypatch.setattr("scripts.agent_backtest.ROOT", tmp_path)
    df = _load_ohlcv_cache("binance", "BTC/USDT:USDT", "1h")
    assert df is None


def test_load_ohlcv_cache_reads_when_present(tmp_path, monkeypatch):
    from scripts.agent_backtest import _load_ohlcv_cache
    monkeypatch.setattr("scripts.agent_backtest.ROOT", tmp_path)
    cache_dir = _ohlcv_cache_dir(tmp_path, "binance", "BTC/USDT:USDT")
    _write_cache(cache_dir, "1h", 100, slope=0.5)
    df = _load_ohlcv_cache("binance", "BTC/USDT:USDT", "1h")
    assert df is not None
    assert len(df) == 100
    assert "close" in df.columns


def test_backtest_symbol_runs_all_agents(tmp_path, monkeypatch):
    from scripts.agent_backtest import backtest_symbol
    monkeypatch.setattr("scripts.agent_backtest.ROOT", tmp_path)
    cache_dir = _ohlcv_cache_dir(tmp_path, "binance", "BTC/USDT:USDT")
    for tf, slope in (("5m", 0.05), ("15m", 0.1), ("1h", 0.5), ("4h", 2.0)):
        _write_cache(cache_dir, tf, 300, slope=slope)
    stats = backtest_symbol("BTC/USDT:USDT", "binance", notional_usdt=200.0)
    expected = {"TrendAgent", "ScalpAgent", "MeanReversionAgent",
                "PatternAgent", "LiquidityAgent"}
    assert set(stats.keys()) == expected


def test_render_report_produces_markdown():
    from scripts.agent_backtest import render_report, BacktestStats
    sym_stats = {
        "BTC/USDT:USDT": {
            "TrendAgent": BacktestStats(agent="TrendAgent", n=10, wins=6,
                                        losses=4, sum_pnl=2.5),
            "ScalpAgent": BacktestStats(agent="ScalpAgent", n=5, wins=2,
                                        losses=3, sum_pnl=-0.4),
        }
    }
    txt = render_report({"BTC/USDT:USDT": sym_stats["BTC/USDT:USDT"]})
    assert "# Agent backtest report" in txt
    assert "TrendAgent" in txt
    assert "BTC/USDT:USDT" in txt
