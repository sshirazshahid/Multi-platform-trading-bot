"""Phase 2 / Task B: distribution-fitted SL/TP."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load("dist_fit_sl_test", "core/dist_fit_sl.py")


class _StubWarehouse:
    """Test double — returns canned rows for query()."""
    def __init__(self, rows):
        self._rows = rows

    def query(self, sql, params):
        return list(self._rows)


def _row(entry, exit_, pnl, reason="stop_loss"):
    return {"entry_px": entry, "exit_px": exit_,
            "realized_pnl": pnl, "exit_reason": reason}


def test_fallback_when_atr_missing(mod):
    """No ATR → reasonable static defaults, source='fallback'."""
    fit = mod.DistFitSL(warehouse=_StubWarehouse([])).compute(
        "BTC/USDT:USDT", "buy", 0.0)
    assert fit.source == "fallback"
    assert fit.sl_pct == pytest.approx(0.018)
    assert fit.tp_pct == pytest.approx(0.045)


def test_fallback_when_below_min_trades(mod):
    """Cell with <30 rows → ATR×1.8 / ATR×4.5 fallback."""
    rows = [_row(100, 98, -2) for _ in range(10)]
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.012)
    assert fit.source == "fallback"
    assert fit.n == 10
    assert fit.sl_pct == pytest.approx(0.012 * 1.8)
    assert fit.tp_pct == pytest.approx(0.012 * 4.5)


def test_fitted_sl_at_85th_quantile(mod):
    """All-loser sample: SL should sit near the 85th-percentile MAE."""
    # 30 losers, MAE = i/100 for i in 1..30  (1%..30%)
    rows = []
    for i in range(1, 31):
        # losing long: exit < entry, MAE = (entry - exit)/entry = i/100
        rows.append(_row(100.0, 100.0 * (1 - i / 100.0), -i, reason="stop_loss"))
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.05,    # ATR floor=5%, ceil=20%
    )
    assert fit.source == "fitted"
    assert fit.n == 30
    # 85th-quantile of 1%..30% with linear interp:
    # pos = 0.85 * 29 = 24.65 → between idx 24 (25%) and 25 (26%)
    # value = 25 + 0.65*(26-25) = 25.65% = 0.2565 → clamped to ATR ceil = 20%
    assert fit.sl_pct == pytest.approx(0.20, abs=1e-9)


def test_fitted_sl_clamped_to_atr_floor(mod):
    """All-winner-only sample: MAE list small → fallback."""
    rows = [_row(100, 102, 2, reason="take_profit") for _ in range(30)]
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.012)
    # No MAE rows at all → fallback path
    assert fit.source == "fallback"


def test_fitted_sl_respects_atr_ceiling(mod):
    """Big losers + tight ATR → SL clamped to 4× ATR ceiling."""
    rows = [_row(100.0, 60.0, -40, reason="stop_loss") for _ in range(40)]
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.01)  # ceil = 4%
    assert fit.source == "fitted"
    assert fit.sl_pct == pytest.approx(0.04, abs=1e-9)


def test_r_target_clamped(mod):
    """R_target can't exceed 3.0 even with huge MFE."""
    rows = []
    for _ in range(20):
        rows.append(_row(100.0, 95.0, -5, reason="stop_loss"))   # MAE=5%
    for _ in range(20):
        rows.append(_row(100.0, 200.0, 100, reason="take_profit"))  # MFE=100%
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.02)
    assert fit.source == "fitted"
    assert 1.5 <= fit.r_target <= 3.0


def test_low_winrate_caps_r_target(mod):
    """Win rate < 30% → R_target pinned to the floor."""
    rows = [_row(100.0, 95.0, -5, reason="stop_loss") for _ in range(35)]
    rows += [_row(100.0, 105.0, 5, reason="take_profit") for _ in range(5)]
    # wr = 5/40 = 0.125
    fit = mod.DistFitSL(warehouse=_StubWarehouse(rows)).compute(
        "BTC/USDT:USDT", "buy", 0.02)
    assert fit.source == "fitted"
    assert fit.r_target == pytest.approx(1.5)


def test_quantile_helper_endpoints(mod):
    assert mod._quantile([], 0.5) == 0.0
    assert mod._quantile([1], 0.5) == 1
    assert mod._quantile([1, 2, 3, 4, 5], 0.0) == 1
    assert mod._quantile([1, 2, 3, 4, 5], 1.0) == 5
    assert mod._quantile([1, 2, 3, 4, 5], 0.5) == pytest.approx(3.0)


def test_unknown_side_raises(mod):
    with pytest.raises(ValueError):
        mod.DistFitSL(warehouse=_StubWarehouse([])).compute(
            "BTC/USDT:USDT", "diagonal", 0.012)


# --------------------------------------------------------------------------
# Phase 4.1: tests for the pure-function compute_sl_tp() API.
# --------------------------------------------------------------------------


import numpy as np  # noqa: E402


def test_compute_sl_tp_recovers_empirical_quantile(mod):
    """Gaussian MAE → SL within 5% of empirical 85th percentile."""
    rng = np.random.default_rng(42)
    mae = np.abs(rng.normal(0.0, 0.01, size=1000))
    mfe = np.abs(rng.normal(0.0, 0.01, size=1000))
    # Wide ATR band so floor/ceiling don't bind.
    sl, _tp = mod.compute_sl_tp(
        mae, mfe, atr_pct=0.05, win_rate=0.5,
        sl_quantile=0.85, atr_floor=0.01, atr_ceiling=100.0,
    )
    empirical_q = float(np.quantile(mae, 0.85))
    assert abs(sl - empirical_q) / empirical_q < 0.05


def test_compute_sl_tp_high_vol_regime_widens_sl(mod):
    """Higher MAE σ → larger fitted SL."""
    rng = np.random.default_rng(7)
    mae_low = np.abs(rng.normal(0.0, 0.005, size=500))
    mae_high = np.abs(rng.normal(0.0, 0.02, size=500))
    mfe = np.abs(rng.normal(0.0, 0.01, size=500))
    sl_low, _ = mod.compute_sl_tp(
        mae_low, mfe, atr_pct=0.10, win_rate=0.5,
        atr_floor=0.0, atr_ceiling=100.0,
    )
    sl_high, _ = mod.compute_sl_tp(
        mae_high, mfe, atr_pct=0.10, win_rate=0.5,
        atr_floor=0.0, atr_ceiling=100.0,
    )
    assert sl_high > sl_low


def test_compute_sl_tp_degenerate_falls_back(mod):
    """All-zero MAE history → legacy ATR×1.8 / ATR×4.5 fallback."""
    mae = np.zeros(50)
    mfe = np.zeros(50)
    sl, tp = mod.compute_sl_tp(
        mae, mfe, atr_pct=0.01, win_rate=0.5,
    )
    assert sl == pytest.approx(0.018)
    assert tp == pytest.approx(0.045)


def test_compute_sl_tp_below_min_sample_falls_back(mod):
    """Sample < min_sample → ATR×1.8 / ATR×4.5 fallback regardless of values."""
    mae = np.array([0.05, 0.10, 0.20, 0.15, 0.08])
    mfe = np.array([0.02, 0.03, 0.01, 0.04, 0.05])
    sl, tp = mod.compute_sl_tp(
        mae, mfe, atr_pct=0.01, win_rate=0.5, min_sample=30,
    )
    assert sl == pytest.approx(0.018)
    assert tp == pytest.approx(0.045)


def test_compute_sl_tp_tp_scales_with_winrate(mod):
    """Lower WR → higher R_target → wider TP for the same MAE/MFE."""
    rng = np.random.default_rng(11)
    mae = np.abs(rng.normal(0.0, 0.01, size=200))
    mfe = np.abs(rng.normal(0.0, 0.01, size=200))
    _, tp_low_wr = mod.compute_sl_tp(
        mae, mfe, atr_pct=0.10, win_rate=0.4,
        atr_floor=0.0, atr_ceiling=100.0,
    )
    _, tp_high_wr = mod.compute_sl_tp(
        mae, mfe, atr_pct=0.10, win_rate=0.6,
        atr_floor=0.0, atr_ceiling=100.0,
    )
    assert tp_low_wr > tp_high_wr


def test_compute_sl_tp_clamps_floor_and_ceiling(mod):
    """Tiny MAE → clamped to atr_floor; huge MAE → clamped to atr_ceiling."""
    # Tiny MAE: 0.01% values, atr_pct=2% → floor=2% should bind.
    tiny = np.full(100, 0.0001)
    sl_tiny, _ = mod.compute_sl_tp(
        tiny, tiny, atr_pct=0.02, win_rate=0.5,
        atr_floor=1.0, atr_ceiling=4.0,
    )
    assert sl_tiny == pytest.approx(0.02)

    # Huge MAE: 50% values, atr_pct=1% → ceiling=4% should bind.
    huge = np.full(100, 0.50)
    sl_huge, _ = mod.compute_sl_tp(
        huge, huge, atr_pct=0.01, win_rate=0.5,
        atr_floor=1.0, atr_ceiling=4.0,
    )
    assert sl_huge == pytest.approx(0.04)


def test_compute_sl_tp_is_reproducible(mod):
    """Same inputs → identical outputs across calls (no internal RNG)."""
    rng = np.random.default_rng(123)
    mae = np.abs(rng.normal(0.0, 0.01, size=500))
    mfe = np.abs(rng.normal(0.0, 0.01, size=500))
    out1 = mod.compute_sl_tp(mae, mfe, atr_pct=0.02, win_rate=0.55)
    out2 = mod.compute_sl_tp(mae, mfe, atr_pct=0.02, win_rate=0.55)
    out3 = mod.compute_sl_tp(
        np.array(mae, copy=True), np.array(mfe, copy=True),
        atr_pct=0.02, win_rate=0.55,
    )
    assert out1 == out2 == out3
