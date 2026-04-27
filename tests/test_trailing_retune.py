"""Phase 11 — trailing re-tune invariants.

Locks the post-2026-04-28 retune values so a future change has to break a
test (and thus think about why) rather than slip through silently.

Evidence and rationale: see config.py:300-310 and trailing_stop_manager.py
_lock_fraction_default docstring. The retune converges trailing exits on
the mcp_take_profit empirical distribution by adding a 0.5% net-PnL cost
floor via raised activation + tighter small-win lock.
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


def test_trailing_activation_threshold_is_2_pct():
    """RISK['trailing_activation'] = 0.020 per Phase 11."""
    from config import RISK
    assert RISK["trailing_activation"] == 0.020, (
        f"trailing_activation should be 0.020 (Phase 11); got {RISK['trailing_activation']}")


def test_lock_fraction_small_win_tier_is_55_pct():
    """Phase 11: peak_pnl < 3% → lock 0.55 (was 0.40 in 04-24 retune)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    # Tier boundary: peaks just-above activation should lock 55%
    assert cls._lock_fraction_default(0.005) == 0.55  # 0.5% peak
    assert cls._lock_fraction_default(0.015) == 0.55  # 1.5% peak
    assert cls._lock_fraction_default(0.025) == 0.55  # 2.5% peak (still <3%)


def test_lock_fraction_medium_tier_unchanged():
    """3-5% peak → 0.55 (unchanged from 04-24)."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    assert cls._lock_fraction_default(0.03) == 0.55
    assert cls._lock_fraction_default(0.04) == 0.55
    assert cls._lock_fraction_default(0.049) == 0.55


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


def test_lock_at_3pct_peak_matches_mcp_tp_median():
    """At peak 3%, lock 0.55 produces +1.45% net — matches mcp_take_profit
    empirical median of +1.47%. This is the convergence target."""
    mod = _load_trailing()
    cls = mod.TrailingStopManager
    peak = 0.030
    lock = cls._lock_fraction_default(peak)
    gross_gain_pct = peak * lock
    net_gain_pct = gross_gain_pct - 0.002
    # 3% × 0.55 = 1.65% gross, -0.2% fees = 1.45% net
    assert abs(net_gain_pct - 0.0145) < 0.001
