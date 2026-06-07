"""Tests for the freqtrade-style trade-analysis breakdown (pure aggregation core)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from trade_analysis_breakdown import group_breakdown, norm_exit, summarize  # noqa: E402


def test_summarize_basic():
    pnl = np.array([2.0, -1.0, -1.0, 2.0, -1.0])  # 2 wins, 3 losses
    s = summarize(pnl)
    assert s["n"] == 5
    assert abs(s["win_rate"] - 0.4) < 1e-9
    assert abs(s["total_pnl"] - 1.0) < 1e-9
    assert abs(s["avg_win"] - 2.0) < 1e-9
    assert abs(s["avg_loss"] - (-1.0)) < 1e-9
    assert abs(s["payoff"] - 2.0) < 1e-9
    assert abs(s["profit_factor"] - (4.0 / 3.0)) < 1e-9
    # breakeven WR = |avg_loss| / (avg_win + |avg_loss|) = 1/3
    assert abs(s["breakeven_wr"] - (1.0 / 3.0)) < 1e-9
    assert s["max_consec_losses"] == 2


def test_summarize_empty():
    assert summarize(np.array([]))["n"] == 0


def test_norm_exit_buckets_freetext():
    assert norm_exit("stop_loss") == "stop_loss"
    assert norm_exit("STALE") == "stale"                       # case-normalized known token
    assert norm_exit("ghost_sync") == "ghost_sync"
    assert norm_exit(None) == "∅"
    assert norm_exit(float("nan")) == "∅"
    # long Claude commentary used as exit_reason → one bucket
    assert norm_exit("509m, -1.5%, trend mixed, SL zone") == "claude_freetext"
    assert norm_exit("4h EMA down invalidates long; losing") == "claude_freetext"
    # short novel single token is kept (possible new categorical reason)
    assert norm_exit("AGE_LIMIT") == "AGE_LIMIT"


def test_group_breakdown_sorts_worst_first():
    df = pd.DataFrame({
        "grp": ["a", "a", "b", "b", "c"],
        "realized_pnl": [1.0, 1.0, -5.0, 1.0, 0.5],   # a=+2, b=-4, c=+0.5
        "r_multiple": [1.0, 1.0, -2.0, 0.5, 0.2],
    })
    rows = group_breakdown(df, "grp")
    assert [r["group"] for r in rows] == ["b", "c", "a"]       # worst (most negative) first
    b = next(r for r in rows if r["group"] == "b")
    assert b["n"] == 2 and abs(b["sum_pnl"] - (-4.0)) < 1e-9
    assert abs(b["win_rate"] - 0.5) < 1e-9


def test_group_breakdown_missing_col():
    assert group_breakdown(pd.DataFrame(), "nope") == []
