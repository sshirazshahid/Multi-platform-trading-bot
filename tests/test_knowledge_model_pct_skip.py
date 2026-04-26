"""knowledge_model — None-pct hygiene (Bug 2D, sibling to position_tracker 2A).

When `reconcile_closed_pnl` cannot reconstruct entry/size from a Binance income
event, it sets `pos.pnl_pct = None` (no synthesized pct). knowledge_model must:
  * skip pnl_pct is None rows from count-based WR / hour_scores / symbol_scores
  * still include dollar `pnl` in net_pnl / avg_pnl totals (ledger integrity)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_km(tmp_path, monkeypatch):
    """Load core/knowledge_model.py with MODEL_FILE/BACKUP_FILE redirected to tmp."""
    spec = importlib.util.spec_from_file_location(
        "knowledge_model_test_copy", ROOT / "core" / "knowledge_model.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["knowledge_model_test_copy"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "MODEL_FILE", tmp_path / "knowledge_model.json")
    monkeypatch.setattr(mod, "BACKUP_FILE", tmp_path / "knowledge_model.bak.json")
    return mod


@pytest.fixture
def km(tmp_path, monkeypatch):
    mod = _load_km(tmp_path, monkeypatch)
    return mod.KnowledgeModel()


def _trade(pnl, pnl_pct, *, strategy="multitf_futures", symbol="BTC/USDT:USDT",
           open_time=1_700_000_000, close_reason="stop_loss"):
    """Shape mirrors data/positions.json closed entries."""
    return {
        "strategy":    strategy,
        "symbol":      symbol,
        "open_time":   open_time,
        "pnl":         pnl,
        "pnl_pct":     pnl_pct,
        "total_fees":  0.0,
        "paper_trade": True,
        "close_reason": close_reason,
    }


def test_none_pct_row_excluded_from_winrate_but_dollar_kept(km):
    """1 None-pct loser ($-1.50) + 4 normal losers ($-1.0 each, pct=-0.05) +
    5 normal winners ($+2.0 each, pct=+0.10).

    Expected:
      strategy_scores[s].dry.win_rate computed from 9 rows (skip None) = 5/9 = 55.6%
      strategy_scores[s].dry.trades = 9 (skip None)
      strategy_scores[s].dry.avg_pnl = (5*2.0 + 4*(-1.0) + (-1.50)) / 9 = 4.50/9 = 0.5
        (dollar-ledger keeps the None-pct trade)
    """
    trades = [_trade(-1.50, None)]                                        # no-context
    trades += [_trade(-1.0, -0.05) for _ in range(4)]                     # normal losers
    trades += [_trade(+2.0, +0.10) for _ in range(5)]                     # normal winners

    km.update_from_trades(trades, mode="dry")

    s = km._model["strategy_scores"]["multitf_futures"]["dry"]
    assert s["trades"] == 9, "None-pct row must NOT be counted in WR sample"
    assert s["wins"] == 5
    assert s["win_rate"] == pytest.approx(55.6, abs=0.1)
    # Dollar ledger keeps the None-pct $-1.50 → net = 10 - 4 - 1.5 = 4.5; avg = 4.5/9 = 0.5
    assert s["avg_pnl"] == pytest.approx(0.5, abs=0.001)


def test_none_pct_does_not_poison_hour_scores(km):
    """A bucket of all-None-pct rows for one hour must yield NO hour_scores entry
    for that hour (because every row was skipped)."""
    # Hour 0 (all None-pct → must be skipped entirely, no entry produced)
    h0_ts = 1_700_000_000  # whatever
    import datetime as _dt
    h0 = _dt.datetime.fromtimestamp(h0_ts).hour
    trades = [_trade(-1.50, None, open_time=h0_ts) for _ in range(3)]

    # Hour H+? — different hour, with a clean-pct trade so something gets recorded
    h1_ts = h0_ts + 7200  # +2h to land on a different hour
    trades.append(_trade(+2.0, +0.10, open_time=h1_ts))
    h1 = _dt.datetime.fromtimestamp(h1_ts).hour

    km.update_from_trades(trades, mode="dry")

    hs = km._model["hour_scores"]
    assert str(h1) in hs and hs[str(h1)]["trades"] == 1
    # h0 should either be absent OR have trades=0
    assert hs.get(str(h0), {"trades": 0})["trades"] == 0, (
        "All-None-pct hour must not be counted in WR sample"
    )


def test_clean_pct_path_unchanged(km):
    """Sanity: when no None-pct rows exist, behavior matches pre-fix expectations."""
    trades = [_trade(-1.0, -0.05) for _ in range(4)]
    trades += [_trade(+2.0, +0.10) for _ in range(6)]

    km.update_from_trades(trades, mode="dry")

    s = km._model["strategy_scores"]["multitf_futures"]["dry"]
    assert s["trades"] == 10
    assert s["wins"] == 6
    assert s["win_rate"] == pytest.approx(60.0, abs=0.1)
    assert s["avg_pnl"] == pytest.approx((6 * 2.0 + 4 * -1.0) / 10, abs=0.001)
