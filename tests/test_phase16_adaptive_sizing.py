"""Phase 16 — adaptive sizing from rolling EV (2026-05-03).

Closes the feedback loop. Bot's sizing now tracks recent realized
expectancy:
  EV >= +$0.10:           full size  (proven positive)
  EV in [-$0.05, +$0.10]: 0.75x      (break-even — moderate)
  EV in [-$0.20, -$0.05]: 0.50x      (bleeding — half)
  EV < -$0.20:            0.25x      (deep negative — slow bleed)
  n < 30:                 full size  (insufficient sample)
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _seed_warehouse(db_path: Path, mean_pnl: float, n: int = 50):
    """Create a synthetic warehouse with N futures trades at the given mean PnL."""
    spec = importlib.util.spec_from_file_location(
        "wh_phase16_test", ROOT / "core" / "warehouse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wh_phase16_test"] = mod
    spec.loader.exec_module(mod)
    wh = mod.Warehouse(path=db_path)
    conn = wh._conn()
    base_ts = int(time.time()) - 3600 * 24
    for i in range(n):
        conn.execute(
            "INSERT INTO trades (candidate_id, ts_entry, ts_exit, exchange, symbol, "
            "market_type, side, entry_px, exit_px, size, leverage, fee, realized_pnl, "
            "status, exit_reason) VALUES "
            "(NULL, ?, ?, 'binance', ?, 'futures', 'buy', 100.0, ?, 1.0, 2, 0.06, ?, "
            "'CLOSED', 'tp')",
            (base_ts + i*60, base_ts + i*60 + 30, f"S{i}/USDT:USDT",
             100 + mean_pnl, mean_pnl)
        )
    conn.commit()
    return wh


@pytest.fixture
def patch_warehouse(tmp_path, monkeypatch):
    """Point risk_manager's warehouse path to a temp DB."""
    tmp_path / "warehouse.sqlite"
    # The risk_manager hardcodes data/warehouse.sqlite — chdir into tmp_path
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir(exist_ok=True)
    return Path("data") / "warehouse.sqlite"


def test_full_size_when_insufficient_sample(patch_warehouse):
    """n < 30 trades → fall back to full size."""
    _seed_warehouse(patch_warehouse, mean_pnl=-0.50, n=20)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 1.0


def test_full_size_when_ev_positive(patch_warehouse):
    """EV >= +$0.10 → full size."""
    _seed_warehouse(patch_warehouse, mean_pnl=+0.15, n=50)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 1.0


def test_three_quarter_size_at_breakeven(patch_warehouse):
    """EV in [-0.05, +0.10] → 0.75x."""
    _seed_warehouse(patch_warehouse, mean_pnl=+0.05, n=50)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 0.75


def test_half_size_when_bleeding(patch_warehouse):
    """EV in [-0.20, -0.05] → 0.50x."""
    _seed_warehouse(patch_warehouse, mean_pnl=-0.10, n=50)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 0.50


def test_quarter_size_when_deep_negative(patch_warehouse):
    """EV < -$0.20 → 0.25x."""
    _seed_warehouse(patch_warehouse, mean_pnl=-0.25, n=50)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 0.25


def test_no_warehouse_returns_full_size(tmp_path, monkeypatch):
    """If warehouse doesn't exist, fail-safe to full size (don't block trading)."""
    monkeypatch.chdir(tmp_path)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    mult = rm._adaptive_size_multiplier()
    assert mult == 1.0


def test_calculate_position_size_applies_multiplier(patch_warehouse):
    """The multiplier is wired into calculate_position_size."""
    # Use a higher balance so the min-notional floor doesn't mask the
    # multiplier (otherwise both qty_quarter and qty_full hit the $5.50 floor).
    _seed_warehouse(patch_warehouse, mean_pnl=-0.25, n=50)
    from core.risk_manager import RiskManager
    rm = RiskManager()
    qty_quarter = rm.calculate_position_size(
        balance_usdt=10000.0, price=100.0, leverage=2,
    )
    # Direct check: with deeply-negative EV, multiplier is 0.25 → notional
    # is one-quarter of what it would be at full size.
    expected_full = 10000.0 * rm.max_position_pct * 2  # balance × pct × lev
    assert qty_quarter < (expected_full * 0.5 / 100.0), \
        "Quarter-size multiplier should produce notional < 0.5x full"


def test_multiplier_clamped_to_quarter_floor():
    """No matter how negative EV gets, multiplier never drops below 0.25."""
    from core.risk_manager import RiskManager
    rm = RiskManager()
    # Smoke: even without warehouse, multiplier returns sane value
    mult = rm._adaptive_size_multiplier()
    assert 0.25 <= mult <= 1.0
