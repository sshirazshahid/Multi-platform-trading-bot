"""Regression: the go-live falsification harness must stay LEAK-FREE (no look-ahead).

The 2026-06-03 adversarial audit (commit 35c5e74) confirmed by hand that
scripts/strategy_pattern_search._signals and scripts/find_patterns._returns are causal
— a position/labeled-return for bar t uses only data through bar t, never the future.
A look-ahead bug is the classic way a backtest manufactures a PHANTOM edge that then
loses real money live, so this pins the invariant: a future refactor that silently
introduces a +1 shift will fail here.

Method: perturb a single FUTURE close and assert that every decision made BEFORE that
bar is byte-for-byte unchanged. If a signal peeked ahead, a pre-perturbation position
would move.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import find_patterns as fp  # noqa: E402
import strategy_pattern_search as sps  # noqa: E402


def _series(n=120, seed=7):
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))


def test_strategy_signals_are_causal():
    """Perturbing closes[K] must not change any position decided before bar K (t < K)."""
    closes = _series()
    K = 80
    _, sigs0 = sps._signals(closes)
    c2 = closes.copy()
    c2[K] *= 1.5  # perturb one bar
    _, sigs1 = sps._signals(c2)
    a, b = dict(sigs0), dict(sigs1)
    assert set(a) == set(b)
    for name in a:
        assert np.array_equal(a[name][:K], b[name][:K]), \
            f"{name}: a position before the perturbed bar changed -> LOOK-AHEAD"


def test_breakout_suspect_uses_only_past_and_current():
    """The flagged breakout: pos[t] for t<=K must be immune to perturbing closes[K+1]."""
    closes = _series(seed=11)
    K = 60
    _, sigs0 = sps._signals(closes)
    c2 = closes.copy()
    c2[K + 1] *= 2.0  # perturb the bar AFTER K (a strict future bar for decisions up to K)
    _, sigs1 = sps._signals(c2)
    a, b = dict(sigs0), dict(sigs1)
    for name in ("brk10", "brk20", "mom20", "mr1"):
        assert np.array_equal(a[name][:K + 1], b[name][:K + 1]), \
            f"{name}: peeked at closes[K+1] -> LOOK-AHEAD"


def test_find_patterns_returns_are_causal():
    """_returns: ret[i] must depend only on closes through bar i, never the future."""
    import pandas as pd
    n = 100
    closes = _series(n=n, seed=3)
    df = pd.DataFrame({"ts": [1_700_000_000 + i * 3600 for i in range(n)], "close": closes})
    r0 = fp._returns(df)["ret"].to_numpy()
    K = 70
    df2 = df.copy()
    df2.loc[K + 1, "close"] *= 1.5  # perturb a future bar
    r1 = fp._returns(df2)["ret"].to_numpy()
    # returns for bars up to K must be unchanged (ret[i]=log c[i]-log c[i-1]); first row dropped by dropna
    assert np.allclose(r0[:K], r1[:K]), "find_patterns._returns leaked a future close"
