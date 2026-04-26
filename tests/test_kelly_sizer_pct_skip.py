"""kelly_sizer — None-pct hygiene (Bug 2D, sibling to position_tracker 2A).

When a closed trade has `pnl_pct = None` (no-context Binance income reconciled
without entry/size), kelly_sizer must:
  * skip it from `wins` / `losses` count (drives WR / Kelly p)
  * still include the dollar in `total_win` / `total_loss` (ledger integrity)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_kelly(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "kelly_sizer_test_copy", ROOT / "core" / "kelly_sizer.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kelly_sizer_test_copy"] = mod
    spec.loader.exec_module(mod)
    # Redirect kelly_stats.json under the module's Path("data/kelly_stats.json").
    # The module hardcodes the path string; we monkey the cwd-equivalent by
    # creating a "data" dir under tmp_path and chdir'ing.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    return mod


@pytest.fixture
def kelly(tmp_path, monkeypatch):
    mod = _load_kelly(tmp_path, monkeypatch)
    return mod.KellySizer()


def _trade(pnl, pnl_pct, *, strategy="multitf_futures", paper=False):
    """paper=False so the test fixture is not skipped under CONTROLLED_LIVE."""
    return {
        "strategy":    strategy,
        "pnl":         pnl,
        "pnl_pct":     pnl_pct,
        "paper_trade": paper,
    }


def test_none_pct_dollar_kept_count_skipped(kelly):
    """1 None-pct loser ($-1.50) + 4 normal losers ($-1.0) + 5 normal winners ($+2.0).

    Expected (after fix):
      losses count = 4 (None-pct skipped from count)
      wins count = 5
      total_loss (dollar ledger) = 4*1.0 + 1.50 = 5.50  (None-pct $ included)
      total_win = 5 * 2.0 = 10.0
    """
    trades = [_trade(-1.50, None)]                       # no-context
    trades += [_trade(-1.0, -0.05) for _ in range(4)]    # normal losers
    trades += [_trade(+2.0, +0.10) for _ in range(5)]    # normal winners

    kelly.update_from_trades(trades)

    s = kelly._stats["multitf_futures"]
    # Counts skip None-pct (Kelly p = 5/(5+4) = 55.6%)
    assert s["wins"] == 5, "wins count must skip None-pct row"
    assert s["losses"] == 4, "losses count must skip None-pct row"
    # Dollar ledger keeps everything (matches Spec §12 daily_pnl reconciliation)
    assert s["total_win"] == pytest.approx(10.0, abs=0.001)
    assert s["total_loss"] == pytest.approx(5.50, abs=0.001), (
        "total_loss must include the None-pct $-1.50 for ledger integrity"
    )


def test_none_pct_persisted_to_json(kelly, tmp_path):
    """kelly_stats.json on disk must reflect dollar-ledger total_loss."""
    trades = [_trade(-1.50, None)]
    trades += [_trade(-1.0, -0.05)] * 4
    trades += [_trade(+2.0, +0.10)] * 5
    kelly.update_from_trades(trades)

    saved = json.loads((tmp_path / "data" / "kelly_stats.json").read_text())
    s = saved["multitf_futures"]
    assert s["total_loss"] == pytest.approx(5.50, abs=0.001)
    assert s["losses"] == 4


def test_clean_pct_path_unchanged(kelly):
    """Sanity: when no None-pct rows exist, accounting matches pre-fix."""
    trades = [_trade(-1.0, -0.05)] * 4
    trades += [_trade(+2.0, +0.10)] * 6
    kelly.update_from_trades(trades)

    s = kelly._stats["multitf_futures"]
    assert s["wins"] == 6
    assert s["losses"] == 4
    assert s["total_win"] == pytest.approx(12.0, abs=0.001)
    assert s["total_loss"] == pytest.approx(4.0, abs=0.001)
