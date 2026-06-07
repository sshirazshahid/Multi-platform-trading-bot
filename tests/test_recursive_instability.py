"""Tests for the recursive/repaint detector added to bias_checks (freqtrade recursive-analysis)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alpha_zoo.bias_checks import (  # noqa: E402
    is_history_stable,
    lookahead_violations,
    recursive_instability,
)


def _series():
    rng = np.random.default_rng(0)
    return pd.Series(100 + np.cumsum(rng.normal(0, 1, 1000)))


def test_fixed_window_sma_is_history_stable():
    """SMA over a FIXED window depends only on the last k bars → final value invariant to history."""
    sma5 = lambda s: pd.Series(s).rolling(5).mean()  # noqa: E731
    s = _series()
    rec = recursive_instability(sma5, s)
    assert rec["stable"] is True
    assert rec["max_rel_drift"] < 1e-9          # identical regardless of how much history precedes
    assert is_history_stable(sma5, s) is True


def test_whole_series_normaliser_is_unstable():
    """z-score over the WHOLE series: final-bar value changes with history length → unstable."""
    def zscore(s):
        s = pd.Series(np.asarray(s, dtype=float))
        return (s - s.mean()) / (s.std(ddof=0) + 1e-12)
    s = _series()
    rec = recursive_instability(zscore, s)
    assert rec["stable"] is False
    assert rec["max_rel_drift"] > 1e-2          # materially different across history lengths
    assert is_history_stable(zscore, s) is False


def test_recursive_ema_converges_with_warmup():
    """EMA(adjust=False) is recursive: drifts a little, but stabilises once warmup accrues."""
    ema = lambda s: pd.Series(s).ewm(span=20, adjust=False).mean()  # noqa: E731
    s = _series()
    rec = recursive_instability(ema, s, history_lengths=[50, 100, 200, 400, 800], rel_tol=1e-3)
    # a warmup length exists past which the final value is within tolerance of the full-history ref
    assert rec["min_stable_len"] is not None
    assert rec["min_stable_len"] <= len(s)


def test_lookahead_detects_future_leak_but_not_causal():
    """Sanity: the existing look-ahead detector flags a forward-shift and clears a causal SMA."""
    leak = lambda s: pd.Series(s).shift(-1).bfill()      # uses the NEXT bar  # noqa: E731
    causal = lambda s: pd.Series(s).rolling(5).mean()    # noqa: E731
    s = _series()
    assert len(lookahead_violations(leak, s, check_indices=range(900, 990))) > 0
    assert lookahead_violations(causal, s, check_indices=range(900, 990)) == []
