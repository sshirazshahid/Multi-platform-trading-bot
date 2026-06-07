"""Guard: the total-return sweep's inline strat-return matches the engine's run_backtest."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from index_strategy_total_return import DIV_YIELD, _strat_ret  # noqa: E402
from nifty_sma_backtest import run_backtest  # noqa: E402


def test_strat_ret_matches_engine_on_price_returns():
    close = np.array([100, 110, 105, 108, 108, 120], dtype=float)
    pos = np.array([0, 1, 1, 0, 1, 1], dtype=float)
    pr = np.zeros(len(close))
    pr[1:] = close[1:] / close[:-1] - 1.0
    a = _strat_ret(pos, pr, 0.0005)
    b = run_backtest(close, pos, 0.0005)["strat_ret"]
    assert np.allclose(a, b)


def test_total_return_adds_dividend_drift():
    # with a positive yield, an always-long total return exceeds the price-only return
    close = np.array([100, 101, 102, 103], dtype=float)
    pos = np.ones(len(close))
    pr = np.zeros(len(close)); pr[1:] = close[1:] / close[:-1] - 1.0
    tr = pr + (2.0 / 100.0) / 252  # +2%/yr
    tr[0] = 0.0
    assert _strat_ret(pos, tr, 0.0)[1:].sum() > _strat_ret(pos, pr, 0.0)[1:].sum()


def test_dax_yield_zero_already_total_return():
    assert DIV_YIELD["DAX"] == 0.0          # ^GDAXI is a performance/TR index
    assert DIV_YIELD["FTSE100"] > 3.0       # high-yield index (the decisive case)
