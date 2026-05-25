# tests/test_alpha_operators.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import operators as op


def _df():
    # 6 rows × 3 cols
    return pd.DataFrame({
        "A": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "B": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
        "C": [1.0, 1.0, 2.0, 2.0, 3.0, 3.0],
    })


def test_rank_is_cross_sectional_pct():
    out = op.rank(_df())
    # row 0: A=1,B=6,C=1 -> ranks pct: A and C tie low, B high
    assert out.loc[0, "B"] == 1.0
    assert out.loc[0, "A"] < out.loc[0, "B"]


def test_delta_and_delay_are_backward():
    df = _df()
    assert np.isnan(op.delay(df, 1).loc[0, "A"])
    assert op.delay(df, 1).loc[1, "A"] == 1.0
    assert op.delta(df, 1).loc[1, "A"] == 1.0  # 2 - 1


def test_ts_max_min_argmax_window():
    df = _df()
    assert op.ts_max(df, 3).loc[2, "A"] == 3.0
    assert op.ts_min(df, 3).loc[2, "B"] == 4.0
    # argmax over last 3: A is increasing -> most recent is max -> position (d-1)
    assert op.ts_argmax(df, 3).loc[2, "A"] == 2.0


def test_decay_linear_weights_recent_more():
    df = pd.DataFrame({"A": [0.0, 0.0, 3.0]})
    # weights (1,2,3)/6 over window 3 -> (0*1+0*2+3*3)/6 = 1.5
    assert abs(op.decay_linear(df, 3).loc[2, "A"] - 1.5) < 1e-9


def test_correlation_runs_and_is_bounded():
    a = _df()[["A"]]
    b = _df()[["B"]]
    c = op.correlation(a, b, 4)
    assert c.loc[5, "A"] <= 1.0 and c.loc[5, "A"] >= -1.0


def test_signed_power_preserves_sign():
    df = pd.DataFrame({"A": [-4.0, 4.0]})
    out = op.signed_power(df, 0.5)
    assert out.loc[0, "A"] == -2.0 and out.loc[1, "A"] == 2.0


def test_scale_normalizes_abs_sum():
    df = pd.DataFrame({"A": [1.0, 2.0], "B": [1.0, 2.0]})
    out = op.scale(df, 1.0)
    assert abs(out.loc[0].abs().sum() - 1.0) < 1e-9
