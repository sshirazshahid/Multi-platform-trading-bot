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


def test_correlation_per_column_on_matching_symbols():
    # Real usage: correlation(open, volume, d) — both frames carry the SAME
    # symbol columns, so rolling().corr() pairs them per column.
    a = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                      "B": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
    b = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],   # tracks A: +corr
                      "B": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]})   # opposes B: -corr
    c = op.correlation(a, b, 4)
    assert c.loc[5, "A"] > 0.99
    assert c.loc[5, "B"] < -0.99


def test_signed_power_preserves_sign():
    df = pd.DataFrame({"A": [-4.0, 4.0]})
    out = op.signed_power(df, 0.5)
    assert out.loc[0, "A"] == -2.0 and out.loc[1, "A"] == 2.0


def test_scale_normalizes_abs_sum():
    df = pd.DataFrame({"A": [1.0, 2.0], "B": [1.0, 2.0]})
    out = op.scale(df, 1.0)
    assert abs(out.loc[0].abs().sum() - 1.0) < 1e-9


def test_product_is_rolling_product():
    df = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0]})
    assert np.isnan(op.product(df, 2).loc[0, "A"])
    assert op.product(df, 2).loc[3, "A"] == 12.0  # 3 * 4


def test_sma_m_is_recursive_ewm():
    df = pd.DataFrame({"A": [1.0, 1.0, 1.0, 10.0]})
    # alpha = M/N = 1/2 ; y3 = 0.5*10 + 0.5*1 = 5.5
    assert abs(op.sma_m(df, 2, 1).loc[3, "A"] - 5.5) < 1e-9


def test_wma_weights_recent_more():
    df = pd.DataFrame({"A": [0.0, 0.0, 1.0]})
    # weights 0.9**[2,1,0] = [0.81,0.9,1.0] normalized; only newest is nonzero
    assert abs(op.wma(df, 3).loc[2, "A"] - 1.0 / (0.81 + 0.9 + 1.0)) < 1e-9


def test_count_rolling_true():
    df = pd.DataFrame({"A": [1.0, -1.0, 2.0, 3.0]})
    out = op.count(df > 0, 3)
    assert out.loc[3, "A"] == 2.0  # last 3 = [-1,2,3] -> 2 positive


def test_highday_lowday():
    df = pd.DataFrame({"A": [1.0, 2.0, 3.0, 2.0]})
    assert op.highday(df, 4).loc[3, "A"] == 1.0   # max(=3) at pos 2 -> 3-2 = 1 bar ago
    assert op.lowday(df, 4).loc[3, "A"] == 3.0     # min(=1) at pos 0 -> 3-0 = 3 bars ago


def test_regbeta_recovers_slope():
    b = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
    a = 2.0 * b + 5.0
    assert abs(op.regbeta(a, b, 4).loc[5, "A"] - 2.0) < 1e-9
