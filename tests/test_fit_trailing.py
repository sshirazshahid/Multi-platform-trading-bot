"""Phase 2 / Task D: trailing-stop fit script — replay correctness +
JSON-loader on TrailingStopManager."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "fit_trailing_test", ROOT / "scripts" / "fit_trailing.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fit_trailing_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fit_mod():
    return _load_script()


def _bar(ts, o, h, lo, c):
    return [ts, o, h, lo, c, 0.0]


def test_replay_long_trails_at_lock_fraction(fit_mod):
    """Long winner that runs to +5%, holds, then retraces — locks at fraction
    × peak. Bars 0 and 1 are designed so activation does NOT trip the SL the
    same bar as peak-setting (intra-bar ordering is unknown so the replay is
    conservative)."""
    ohlc = [
        # Bar 0: small move up, peak_pnl=1% (< 5% activation) → not activated
        _bar(0, 100, 101, 99.5, 100.5),
        # Bar 1: peak=105 (5% — activates), low=103 stays above SL=102.5
        _bar(1, 100.5, 105, 103, 104),
        # Bar 2: low=102 trips SL=102.5
        _bar(2, 104, 104, 102, 102),
    ]
    sim = fit_mod._replay_winner(
        side="buy", entry=100.0, exit_=104.0, ohlc=ohlc,
        activation_pct=0.05,    # activates at +5%
        lock_fraction=0.5,      # SL = entry + 0.5*(peak-entry) = 102.5
    )
    assert sim == pytest.approx(102.5)


def test_replay_long_no_activation_returns_exit(fit_mod):
    """If trailing never activates, simulator returns the original exit."""
    ohlc = [
        _bar(0, 100, 100.5, 99.5, 100.2),
        _bar(1, 100.2, 100.6, 100.0, 100.3),
    ]
    sim = fit_mod._replay_winner(
        side="buy", entry=100.0, exit_=100.3, ohlc=ohlc,
        activation_pct=0.05,    # 5% — never reached
        lock_fraction=0.7,
    )
    assert sim == 100.3


def test_replay_short_trails_correctly(fit_mod):
    """Short winner that drops to -5% then bounces — locks at trough side."""
    ohlc = [
        # Bar 0: minor move, trough_pnl=1% (< 5% activation) → not activated
        _bar(0, 100, 101, 99.5, 100),
        # Bar 1: trough=95 (5% — activates), high=97 stays below SL=97.5
        _bar(1, 100, 97, 95, 96),
        # Bar 2: high=99 trips SL=97.5
        _bar(2, 96, 99, 95.5, 98.5),
    ]
    sim = fit_mod._replay_winner(
        side="sell", entry=100.0, exit_=96.0, ohlc=ohlc,
        activation_pct=0.05,    # activates at -5% (peak_pnl=trough side)
        lock_fraction=0.5,      # SL = entry - 0.5*(entry-trough) = 97.5
    )
    assert sim == pytest.approx(97.5)


def test_replay_no_ohlc_returns_exit(fit_mod):
    sim = fit_mod._replay_winner(
        side="buy", entry=100.0, exit_=110.0, ohlc=[],
        activation_pct=0.01, lock_fraction=0.5)
    assert sim == 110.0


# ── trailing_stop_manager loader ────────────────────────────────────────


def test_trailing_stop_manager_loads_fitted_params(monkeypatch, tmp_path):
    """When data/trailing_params.json exists, manager picks up activation
    and lock_override."""
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()
    (tmp_path / "data" / "trailing_params.json").write_text(json.dumps({
        "activation_pct": 0.015,
        "lock_fraction": 0.55,
        "n_winners": 50,
    }))

    # Fresh import so module-level constants pick up the new cwd
    from importlib import reload

    import core.trailing_stop_manager as tsm
    reload(tsm)
    mgr = tsm.TrailingStopManager()
    assert mgr.activation == pytest.approx(0.015)
    assert mgr._lock_override == pytest.approx(0.55)
    # Low-tier peak_pnl should use the override
    assert mgr._lock_fraction(0.02) == pytest.approx(0.55)
    # High-tier still uses graduated default
    assert mgr._lock_fraction(0.10) == tsm.TrailingStopManager._lock_fraction_default(0.10)


def test_trailing_stop_manager_falls_back_without_params(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()
    from importlib import reload

    import core.trailing_stop_manager as tsm
    reload(tsm)
    mgr = tsm.TrailingStopManager()
    assert mgr._lock_override is None
    # Falls through to the graduated table.
    # 2026-04-28 (Phase 11) re-retune: small-win tier 0.40 → 0.55.
    # 2026-05-03 (Phase 15): small-win tier 0.55 → 0.65 to lock faster
    # under the AGE_LIMIT cut (4h → 1.25h).
    assert mgr._lock_fraction(0.02) == pytest.approx(0.65)  # < 3% tier
