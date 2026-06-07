"""Tests for the Nifty SMA walk-forward core (fold logic, leak-free OOS stitching)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from nifty_sma_walkforward import walk_forward  # noqa: E402


def _const(n, v):
    return np.full(n, v, dtype=float)


def test_walk_forward_picks_in_sample_winner():
    # noisy (non-constant) series so Sharpe is defined and discriminating:
    # 'good' has positive mean, 'bad' negative mean; both same volatility.
    rng = np.random.default_rng(0)
    n = 1000
    noise = rng.normal(0, 0.001, n)
    strat = {"good": 0.0008 + noise, "bad": -0.0008 + noise}
    folds = walk_forward(strat, is_days=200, oos_days=50, step=50, objective="sharpe")
    assert len(folds) > 0
    assert all(f["param"] == "good" for f in folds)        # picks the positive-mean one IS
    assert all(f["oos_sharpe"] > 0 for f in folds)         # and it is positive OOS too


def test_walk_forward_oos_contiguous_nonoverlapping():
    n = 1000
    strat = {"a": _const(n, 0.001), "b": _const(n, 0.0005)}
    folds = walk_forward(strat, is_days=200, oos_days=50, step=50, objective="sharpe")
    for i in range(1, len(folds)):
        assert folds[i]["oos_lo"] == folds[i - 1]["oos_hi"]   # step==oos_days => contiguous
    assert folds[0]["oos_lo"] == 200                          # first OOS starts after first IS
    for f in folds:
        assert f["oos_hi"] - f["oos_lo"] == 50
        assert f["is_hi"] == f["oos_lo"]                      # IS ends exactly where OOS begins


def test_walk_forward_no_future_leak_in_selection():
    """The pick on fold k uses ONLY its IS slice: flip a param's sign mid-series and the
    earlier folds' choices must be unaffected."""
    n = 800
    base = {"x": _const(n, 0.0005), "y": _const(n, 0.0001)}
    f1 = walk_forward(base, is_days=200, oos_days=100, step=100, objective="sharpe")
    mod = {"x": base["x"].copy(), "y": base["y"].copy()}
    mod["y"][400:] = 0.01                                     # make y great only AFTER bar 400
    f2 = walk_forward(mod, is_days=200, oos_days=100, step=100, objective="sharpe")
    # fold whose IS window ends at/before 400 must be identical (no peeking at the future change)
    for a, b in zip(f1, f2):
        if a["is_hi"] <= 400:
            assert a["param"] == b["param"]


def test_empty_when_insufficient_data():
    strat = {"a": _const(100, 0.001)}
    assert walk_forward(strat, is_days=200, oos_days=50, step=50) == []
