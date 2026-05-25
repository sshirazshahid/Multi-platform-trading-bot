# tests/test_alpha_lookahead_sentinel.py
"""Future-corruption sentinel: corrupting rows >= C must NOT change any
operator's output on rows < C. Proves operators are backward-only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.alpha_zoo import operators as op

rng = np.random.default_rng(7)


def _df(T=60, N=4):
    return pd.DataFrame(rng.normal(size=(T, N)) + 5.0,
                        columns=[f"S{i}" for i in range(N)])


# (callable, needs-two-args)
UNARY = [
    lambda d: op.delay(d, 3), lambda d: op.delta(d, 3),
    lambda d: op.ts_sum(d, 5), lambda d: op.sma(d, 5),
    lambda d: op.stddev(d, 5), lambda d: op.ts_min(d, 5),
    lambda d: op.ts_max(d, 5), lambda d: op.ts_argmax(d, 5),
    lambda d: op.ts_argmin(d, 5), lambda d: op.ts_rank(d, 5),
    lambda d: op.decay_linear(d, 5), lambda d: op.rank(d),
    lambda d: op.scale(d), lambda d: op.signed_power(d, 0.5),
]


@pytest.mark.parametrize("fn", UNARY)
def test_unary_operators_are_backward_only(fn):
    df = _df()
    C = 50
    out1 = fn(df)
    corrupt = df.copy()
    corrupt.iloc[C:] = corrupt.iloc[C:] * 999.0 + 123.0
    out2 = fn(corrupt)
    a = out1.iloc[:C].to_numpy()
    b = out2.iloc[:C].to_numpy()
    assert np.allclose(a, b, equal_nan=True), "future rows leaked into past output"


def test_correlation_is_backward_only():
    a, b = _df(), _df()
    C = 50
    out1 = op.correlation(a, b, 6)
    a2, b2 = a.copy(), b.copy()
    a2.iloc[C:] *= 999.0
    b2.iloc[C:] *= 999.0
    out2 = op.correlation(a2, b2, 6)
    assert np.allclose(out1.iloc[:C].to_numpy(), out2.iloc[:C].to_numpy(),
                       equal_nan=True)
