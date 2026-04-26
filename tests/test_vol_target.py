"""Tests for Phase 4.2 volatility-targeting sizer.

Per compiled-pondering-key Phase 4.2.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
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


@pytest.fixture(scope="module")
def mod():
    return _load("vol_target_p42", "core/vol_target.py")


# --------------------------------------------------------------------- #
# 1. Higher vol → smaller position                                      #
# --------------------------------------------------------------------- #
def test_higher_vol_smaller_position(mod):
    low = mod.VolTarget.size(balance_usd=10_000.0, vol_forecast=0.005)
    high = mod.VolTarget.size(balance_usd=10_000.0, vol_forecast=0.020)
    assert high < low


# --------------------------------------------------------------------- #
# 2. Target vol scales notional linearly (within clamp)                 #
# --------------------------------------------------------------------- #
def test_target_vol_scales_linearly(mod):
    # Pick parameters that keep both results inside the [5%, 200%] clamp.
    # vol_forecast = 0.01 / hour, balance 10k, bars_per_year 8760 →
    # annual_vol ≈ 0.01 * 93.6 = 0.936 → notional @ tgt 0.10 ≈ 1067 (10.7%)
    # @ tgt 0.20 ≈ 2135 (21.3%) — both inside clamp.
    a = mod.VolTarget.size(
        balance_usd=10_000.0, vol_forecast=0.01, target_annual_vol=0.10
    )
    b = mod.VolTarget.size(
        balance_usd=10_000.0, vol_forecast=0.01, target_annual_vol=0.20
    )
    assert b == pytest.approx(2.0 * a, rel=1e-9)


# --------------------------------------------------------------------- #
# 3. bars_per_year correctness — 1h vs 4h sqrt scaling                  #
# --------------------------------------------------------------------- #
def test_bars_per_year_sqrt_scaling(mod):
    """Same vol_forecast on different timeframes scales by sqrt(bpy_ratio).

    annual_vol = vf * sqrt(bpy); notional ∝ 1 / annual_vol = 1 / sqrt(bpy).
    With 4h bars (2190) vs 1h bars (8760), the ratio is sqrt(8760/2190)=2.
    So the 4h-frame sizing is 2x the 1h-frame sizing for the same vf.
    """
    # Use a target/vf combo that keeps both sides inside clamps.
    # vf=0.02, balance=10k, target=0.10:
    #   1h: annual=0.02*sqrt(8760)=1.872 → notional=534 (5.34%) — above floor
    #   4h: annual=0.02*sqrt(2190)=0.936 → notional=1068 (10.68%) — below ceiling
    n_1h = mod.VolTarget.size(
        balance_usd=10_000.0, vol_forecast=0.02, bars_per_year=8760.0
    )
    n_4h = mod.VolTarget.size(
        balance_usd=10_000.0, vol_forecast=0.02, bars_per_year=2190.0
    )
    ratio = n_4h / n_1h
    assert ratio == pytest.approx(math.sqrt(8760.0 / 2190.0), rel=1e-9)
    # Specifically that ratio is 2.0
    assert ratio == pytest.approx(2.0, rel=1e-9)


# --------------------------------------------------------------------- #
# 4. Clamps enforced — tiny vf hits ceiling, huge vf hits floor         #
# --------------------------------------------------------------------- #
def test_tiny_vol_clamped_to_ceiling(mod):
    n = mod.VolTarget.size(
        balance_usd=10_000.0,
        vol_forecast=1e-6,
        notional_ceiling_pct=2.00,
    )
    # Unclamped would be enormous (≈ 10^9). Must equal ceiling.
    assert n == pytest.approx(10_000.0 * 2.00)


def test_huge_vol_clamped_to_floor(mod):
    n = mod.VolTarget.size(
        balance_usd=10_000.0,
        vol_forecast=0.5,            # 50% per bar — astronomical
        notional_floor_pct=0.05,
    )
    assert n == pytest.approx(10_000.0 * 0.05)


def test_custom_clamps_respected(mod):
    n_floor = mod.VolTarget.size(
        balance_usd=1_000.0,
        vol_forecast=0.5,
        notional_floor_pct=0.10,
        notional_ceiling_pct=0.50,
    )
    assert n_floor == pytest.approx(100.0)
    n_ceil = mod.VolTarget.size(
        balance_usd=1_000.0,
        vol_forecast=1e-6,
        notional_floor_pct=0.10,
        notional_ceiling_pct=0.50,
    )
    assert n_ceil == pytest.approx(500.0)


# --------------------------------------------------------------------- #
# 5. Degenerate input — zero / negative / NaN vol_forecast → floor      #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_vf", [0.0, -0.01, float("nan"), float("inf"), None])
def test_degenerate_vol_returns_floor(mod, bad_vf):
    floor = mod.VolTarget.size(balance_usd=1_000.0, vol_forecast=bad_vf)
    assert floor == pytest.approx(1_000.0 * 0.05)


def test_zero_balance_returns_zero(mod):
    assert mod.VolTarget.size(balance_usd=0.0, vol_forecast=0.01) == 0.0
    assert mod.VolTarget.size(balance_usd=-100.0, vol_forecast=0.01) == 0.0


def test_invalid_bpy_raises(mod):
    with pytest.raises(ValueError):
        mod.VolTarget.size(balance_usd=1000.0, vol_forecast=0.01, bars_per_year=0)
    with pytest.raises(ValueError):
        mod.VolTarget.size(balance_usd=1000.0, vol_forecast=0.01, bars_per_year=-10)


def test_invalid_target_raises(mod):
    with pytest.raises(ValueError):
        mod.VolTarget.size(
            balance_usd=1000.0, vol_forecast=0.01, target_annual_vol=0
        )


# --------------------------------------------------------------------- #
# 6. Monte Carlo: realized portfolio vol within ±20% of target          #
# --------------------------------------------------------------------- #
def test_monte_carlo_realized_vol_within_band(mod):
    """Simulate 30 days of hourly returns and verify the realized
    annualized portfolio vol from VolTarget-sized positions falls within
    ±20% of the target.

    Setup:
      - balance 10k, target_annual_vol 0.10, bars_per_year 8760
      - per-bar vol_forecast = sigma_per_bar (held constant)
      - draw r_t ~ N(0, sigma_per_bar) for 30*24 = 720 bars
      - position_return_t = (notional/balance) * r_t  (ungeared portfolio
        contribution from a single asset). With vf well-calibrated and no
        clamp this should yield realized annual vol == target.
    """
    rng = np.random.default_rng(seed=42)  # deterministic; not in the function
    balance = 10_000.0
    target = 0.10
    bpy = 8760.0
    sigma_per_bar = 0.01  # 1% per hour — realistic crypto

    notional = mod.VolTarget.size(
        balance_usd=balance,
        vol_forecast=sigma_per_bar,
        bars_per_year=bpy,
        target_annual_vol=target,
        notional_floor_pct=0.0,    # disable clamps to validate the math
        notional_ceiling_pct=10.0,
    )

    n_bars = 30 * 24
    # Run multiple paths to reduce sampling noise on the variance estimator.
    realized_annuals = []
    for _ in range(200):
        r = rng.normal(0.0, sigma_per_bar, size=n_bars)
        portfolio_returns = (notional / balance) * r
        realized_per_bar = float(np.std(portfolio_returns, ddof=1))
        realized_annuals.append(realized_per_bar * math.sqrt(bpy))
    realized_annual = float(np.mean(realized_annuals))

    assert abs(realized_annual - target) / target <= 0.20, (
        f"realized {realized_annual:.4f} not within ±20% of target {target}"
    )


# --------------------------------------------------------------------- #
# 7. Reproducibility — no internal random state                         #
# --------------------------------------------------------------------- #
def test_reproducible(mod):
    args = dict(balance_usd=10_000.0, vol_forecast=0.01, bars_per_year=8760.0,
                target_annual_vol=0.10)
    a = mod.VolTarget.size(**args)
    b = mod.VolTarget.size(**args)
    c = mod.VolTarget.size(**args)
    assert a == b == c


# --------------------------------------------------------------------- #
# Sanity: closed-form match                                             #
# --------------------------------------------------------------------- #
def test_closed_form(mod):
    """Verify exact formula notional = balance * target / (vf * sqrt(bpy))."""
    balance = 10_000.0
    vf = 0.01
    bpy = 8760.0
    tgt = 0.10
    expected = balance * tgt / (vf * math.sqrt(bpy))
    got = mod.VolTarget.size(
        balance_usd=balance, vol_forecast=vf, bars_per_year=bpy,
        target_annual_vol=tgt,
        notional_floor_pct=0.0, notional_ceiling_pct=10.0,
    )
    assert got == pytest.approx(expected, rel=1e-12)
