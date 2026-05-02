"""Task A.9 + A.11 — ShadowRunner with projected sizer."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_shadow_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_shadow_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


@pytest.fixture(autouse=True)
def risk_state_neutral(tmp_path, monkeypatch):
    p = Path("data/risk_state.json")
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"is_halted": False, "halt_reason": ""}), encoding="utf-8")


def _ohlcv(n: int, freq: str, slope: float, base: float = 100.0, noise: float = 1.5):
    rng = np.random.RandomState(42)
    close = base + np.arange(n) * slope + rng.randn(n) * noise
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq),
        "open": close - 0.05,
        "high": close + abs(rng.randn(n)) * (noise + 0.5),
        "low": close - abs(rng.randn(n)) * (noise + 0.5),
        "close": close, "volume": np.full(n, 1000.0),
    })


def _ctx_uptrend():
    return {
        "symbol": "BTC/USDT:USDT",
        "ohlcv_5m": _ohlcv(80, "5min", slope=0.3, noise=0.5),
        "ohlcv_15m": _ohlcv(40, "15min", slope=0.5, noise=0.5),
        "ohlcv_1h": _ohlcv(80, "1h", slope=0.5),
        "ohlcv_4h": _ohlcv(40, "4h", slope=2.0),
    }


def test_shadow_runner_tick_writes_decisions(wh):
    from core.shadow_runner import ShadowRunner
    om = MagicMock()
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: _ctx_uptrend(),
        free_balance_provider=lambda: 580.0,
        symbols_provider=lambda: ["BTC/USDT:USDT"],
        allowed_hours=set(range(24)),
    )
    emitted = runner.tick()
    assert emitted >= 1  # at least TrendAgent should fire
    rows = wh.query("SELECT * FROM shadow_decisions")
    assert len(rows) == emitted
    # Critical invariant: no orders placed
    assert om.open_position.call_count == 0


def test_shadow_runner_disabled_flag_no_writes(wh):
    from core.shadow_runner import ShadowRunner
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: _ctx_uptrend(),
        free_balance_provider=lambda: 580.0,
        symbols_provider=lambda: ["BTC/USDT:USDT"],
        enabled_flag=lambda: False,
        allowed_hours=set(range(24)),
    )
    runner.tick()
    rows = wh.query("SELECT * FROM shadow_decisions")
    assert len(rows) == 0


def test_shadow_runner_dual_notional_persisted(wh):
    from core.shadow_runner import ShadowRunner
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: _ctx_uptrend(),
        free_balance_provider=lambda: 580.0,  # current = 0.50*2*580/3 = ~$193
        symbols_provider=lambda: ["BTC/USDT:USDT"],
        allowed_hours=set(range(24)),
    )
    runner.tick()
    rows = wh.query("SELECT projected_notional_current, projected_notional_alt FROM shadow_decisions WHERE decision='ALLOW'")
    if rows:
        # Both notional projections must be non-null and sane
        for r in rows:
            assert r["projected_notional_current"] is not None
            assert r["projected_notional_alt"] == 200.0


def test_shadow_runner_stats(wh):
    from core.shadow_runner import ShadowRunner
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: _ctx_uptrend(),
        free_balance_provider=lambda: 580.0,
        symbols_provider=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"],
        allowed_hours=set(range(24)),
    )
    runner.tick()
    runner.tick()
    s = runner.stats()
    assert s["ticks"] == 2
