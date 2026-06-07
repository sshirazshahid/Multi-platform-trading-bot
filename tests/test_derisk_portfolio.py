"""Tests for the combined core+overlay portfolio (return construction + equity)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from derisk_portfolio import CAP, LOOKBACK, _combined, _tr_ret  # noqa: E402


def test_tr_ret_adds_dividend_drift():
    close = np.array([100.0, 101.0, 102.0, 103.0])
    base = _tr_ret(close, 0.0)
    withdiv = _tr_ret(close, 2.0)            # +2%/yr
    assert withdiv[0] == 0.0
    assert np.all(withdiv[1:] > base[1:])    # dividend lifts every daily return


def test_combined_equity_matches_cumprod_and_no_leverage():
    rng = np.random.default_rng(0)
    tr = np.concatenate([[0.0], rng.normal(0.0003, 0.01, 600)])
    pos, strat, eq, bh_eq = _combined(tr)
    assert np.allclose(eq, np.cumprod(1.0 + strat))
    assert np.allclose(bh_eq, np.cumprod(1.0 + tr))
    assert pos.max() <= CAP + 1e-9 and pos.min() >= 0.0     # de-risk-only: never leveraged
    assert pos.max() <= 1.0 + 1e-9


def test_overlay_reduces_volatility_vs_buyhold():
    rng = np.random.default_rng(3)
    tr = np.concatenate([rng.normal(0.0004, 0.005, 400), rng.normal(-0.001, 0.03, 400)])
    tr[0] = 0.0
    _, strat, _, _ = _combined(tr)
    assert np.std(strat[LOOKBACK:]) <= np.std(tr[LOOKBACK:])  # overlay dampens vol
