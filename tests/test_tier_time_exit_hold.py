"""Tier-geometry time-exit hold (2026-08-03 owner-directed STALE fix).

Post-AccBand tier geometry (TP 2.0-3.75% vs SL 0.9-1.5%) was being harvested
by the Phase-14-era STALE/AGE cutoffs before either barrier could be hit:
4 of the first 10 post-fix resolved trades exited STALE with 0 full
take-profits. While planned R:R >= 1 and inside the 72h horizon, first-touch
SL/TP governs — mirroring the 2026-07-10 ACCURACY band hold precedent.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.order_manager import _tier_geometry_hold_active


@pytest.fixture()
def hold_cfg():
    """The live TIER_GEOMETRY_TIME_EXIT_HOLD dict, resolved at call time.

    tests/test_scalp_kill_switch.py calls importlib.reload(config), which
    rebinds config dicts to new objects. A module-level `from config import
    ...` would leave these tests patching a stale dict while
    _tier_geometry_hold_active (which re-imports inside the function) reads
    the fresh one — passing alone, failing in full-suite order.
    """
    import config as _cfg
    return _cfg.TIER_GEOMETRY_TIME_EXIT_HOLD


def _pos(side="buy", entry=100.0, sl=98.5, tp=102.0):
    return SimpleNamespace(side=side, entry_price=entry, stop_loss=sl,
                           take_profit=tp)


def test_buy_tier_geometry_holds_inside_horizon(monkeypatch, hold_cfg):
    """The live defect case: buy SL 1.5% / TP 2.0% (R:R 1.33) at 5h age —
    past the 4h stale limit but inside the hold horizon — must defer."""
    monkeypatch.setitem(hold_cfg, "enabled", True)
    assert _tier_geometry_hold_active(_pos(), age_hours=5.0) is True


def test_sell_tier_geometry_holds(monkeypatch, hold_cfg):
    monkeypatch.setitem(hold_cfg, "enabled", True)
    # sell: SL 0.9% above, TP 2.0% below -> R:R 2.22
    p = _pos(side="sell", entry=100.0, sl=100.9, tp=98.0)
    assert _tier_geometry_hold_active(p, age_hours=10.0) is True


def test_horizon_zombie_protection(monkeypatch, hold_cfg):
    monkeypatch.setitem(hold_cfg, "enabled", True)
    monkeypatch.setitem(hold_cfg, "max_hold_hours", 72.0)
    assert _tier_geometry_hold_active(_pos(), age_hours=72.0) is False
    assert _tier_geometry_hold_active(_pos(), age_hours=100.0) is False


def test_compressed_geometry_never_holds(monkeypatch, hold_cfg):
    """AccBand-style compression (TP 0.35% vs SL 0.90%, R:R 0.39) must NOT
    earn the hold — the deferral exists for wide targets only."""
    monkeypatch.setitem(hold_cfg, "enabled", True)
    p = _pos(entry=100.0, sl=99.1, tp=100.35)
    assert _tier_geometry_hold_active(p, age_hours=5.0) is False


def test_missing_tp_sentinel_never_holds(monkeypatch, hold_cfg):
    monkeypatch.setitem(hold_cfg, "enabled", True)
    assert _tier_geometry_hold_active(_pos(tp=0.0), age_hours=5.0) is False
    assert _tier_geometry_hold_active(_pos(sl=0.0), age_hours=5.0) is False


def test_inverted_barriers_never_hold(monkeypatch, hold_cfg):
    """A buy whose SL sits above entry (degenerate/trailed state) yields
    non-positive risk — the hold must decline rather than divide."""
    monkeypatch.setitem(hold_cfg, "enabled", True)
    p = _pos(entry=100.0, sl=101.0, tp=103.0)
    assert _tier_geometry_hold_active(p, age_hours=5.0) is False


def test_flag_off_is_byte_identical(monkeypatch, hold_cfg):
    monkeypatch.setitem(hold_cfg, "enabled", False)
    assert _tier_geometry_hold_active(_pos(), age_hours=5.0) is False
