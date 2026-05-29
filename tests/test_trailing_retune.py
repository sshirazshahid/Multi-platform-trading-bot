"""Phase 11 → Phase 15 trailing invariants.

Phase 11 (2026-04-28): activation 1.5→2.0%, lock_small_win 0.40→0.55,
converging on mcp_take_profit's empirical capture distribution.

Phase 15 (2026-05-03): combined with the AGE_LIMIT cut from 4h to 1.25h,
trailing must engage faster or AGE_LIMIT fires before lock takes hold.
Activation 2.0→1.2%, distance 1.0→0.8%, lock_small_win 0.55→0.65.

This file pins the CURRENT (Phase 15) values. A future change has to
break a test and think about why before slipping through silently.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_trailing():
    spec = importlib.util.spec_from_file_location(
        "trailing_test_phase11", ROOT / "core" / "trailing_stop_manager.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["trailing_test_phase11"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_trailing_activation_threshold_is_phase15():
    """RISK['trailing_activation'] = 0.012 per Phase 15."""
    from config import RISK
    assert RISK["trailing_activation"] == 0.012, (
        f"trailing_activation should be 0.012 (Phase 15); got {RISK['trailing_activation']}")


def test_lock_fraction_small_win_tier_is_65_pct():
    """Phase 15: peak_pnl < 3% → lock 0.65 (Phase 11 was 0.55)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    assert cls._lock_fraction_default(0.005) == 0.65
    assert cls._lock_fraction_default(0.015) == 0.65
    assert cls._lock_fraction_default(0.025) == 0.65


def test_lock_fraction_medium_tier_phase15():
    """Phase 15: 3-5% peak → 0.65 (matched to small-win for consistency)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    assert cls._lock_fraction_default(0.03) == 0.65
    assert cls._lock_fraction_default(0.04) == 0.65
    assert cls._lock_fraction_default(0.049) == 0.65


def test_lock_fraction_good_tier_unchanged():
    """5-8% peak → 0.70 (unchanged — sample-poor tier, fit can't speak to it)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    assert cls._lock_fraction_default(0.05) == 0.70
    assert cls._lock_fraction_default(0.07) == 0.70


def test_lock_fraction_exceptional_tier_unchanged():
    """8%+ peak → 0.80 (unchanged — only 4 historical TPs reached this)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    assert cls._lock_fraction_default(0.08) == 0.80
    assert cls._lock_fraction_default(0.15) == 0.80


def test_lock_at_phase11_targets_clears_cost_floor():
    """At peak 2% (the new activation), lock 0.55 must produce > 0.5% net.

    Math: SL = entry × (1 + peak × lock) = 1 + 2% × 0.55 = +1.1% gross.
    Round-trip futures fee ~0.2% → +0.9% net. Above the 0.5% cost floor
    that mcp_take_profit uses at bot_engine.py:2535-2557.
    """
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    peak = 0.020  # 2.0% — Phase 11 activation threshold
    lock = cls._lock_fraction_default(peak)
    gross_gain_pct = peak * lock
    net_gain_pct = gross_gain_pct - 0.002  # 0.2% round-trip futures fee
    assert net_gain_pct >= 0.005, (
        f"net gain {net_gain_pct:.4f} must clear 0.5% cost floor; "
        f"peak={peak}, lock={lock}")


def test_lock_at_3pct_peak_phase15_above_floor():
    """At peak 3% under Phase 15 lock 0.65: +1.95% gross, +1.75% net.
    Above the 0.5% cost floor — and above Phase 11's 1.45% target,
    reflecting the more aggressive lock-in under tighter age cap."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    peak = 0.030
    lock = cls._lock_fraction_default(peak)
    gross_gain_pct = peak * lock
    net_gain_pct = gross_gain_pct - 0.002
    # 3% × 0.65 = 1.95% gross, -0.2% fees = 1.75% net
    assert abs(net_gain_pct - 0.0175) < 0.001
    assert net_gain_pct >= 0.005  # cost floor invariant preserved
