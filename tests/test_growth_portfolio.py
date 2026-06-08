"""Tests for the growth portfolio engine (weight schemes + causal backtest)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from growth_portfolio import (  # noqa: E402
    VOL_LOOKBACK,
    backtest_portfolio,
    w_6040,
    w_equal,
    w_inverse_vol,
)


def test_w_equal_sums_to_one():
    a = ["A", "B", "C", "D"]
    w = w_equal(None, 10, a)
    assert np.allclose(w, 0.25) and abs(w.sum() - 1) < 1e-12


def test_w_6040_places_60_40():
    a = ["SPY", "IEF", "GLD", "TLT"]
    w = w_6040(None, 10, a)
    assert w[a.index("SPY")] == 0.60 and w[a.index("IEF")] == 0.40
    assert w[a.index("GLD")] == 0.0 and abs(w.sum() - 1) < 1e-12


def test_inverse_vol_favors_low_vol_and_normalizes():
    rng = np.random.default_rng(0)
    a = ["LOW", "HIGH"]
    rets = np.column_stack([rng.normal(0, 0.005, 200), rng.normal(0, 0.03, 200)])
    w = w_inverse_vol(rets, 150, a, lookback=100)
    assert w[0] > w[1]                       # lower-vol asset gets more weight
    assert abs(w.sum() - 1.0) < 1e-9


def test_backtest_is_causal_future_does_not_affect_past():
    rng = np.random.default_rng(1)
    a = ["A", "B"]
    rets = rng.normal(0.0003, 0.01, (400, 2))
    p1 = backtest_portfolio(rets, a, w_inverse_vol)
    rets2 = rets.copy()
    rets2[300:] = rng.normal(0, 0.05, (100, 2))      # perturb the FUTURE
    p2 = backtest_portfolio(rets2, a, w_inverse_vol)
    k = 300 - (VOL_LOOKBACK + 1)                      # output index for day 300
    assert np.allclose(p1[:k], p2[:k])               # past portfolio returns unchanged


def test_voltarget_leverage_respects_cap():
    rng = np.random.default_rng(2)
    a = ["A", "B", "C"]
    rets = rng.normal(0.0002, 0.004, (500, 3))        # low vol -> overlay wants leverage
    lev_port = backtest_portfolio(rets, a, w_inverse_vol, target_vol=0.12, cap=2.0)
    nolev_port = backtest_portfolio(rets, a, w_inverse_vol, target_vol=0.12, cap=1.0)
    # levered book should have higher gross magnitude (cap allows scaling up calm low-vol assets)
    assert np.std(lev_port) >= np.std(nolev_port) - 1e-12
