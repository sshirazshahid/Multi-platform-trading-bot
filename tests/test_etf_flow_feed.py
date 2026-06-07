"""Tests for the spot-BTC-ETF flow feed (WS3b new-information-source screen).

Covers: HTML table parsing (Farside format: parenthesised negatives, '-' blanks,
'Average'/summary footer rows dropped) and the causal (no-look-ahead) rolling signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.data_feeds.etf_flow_feed import etf_flow_signal, parse_etf_flow_series

_FIXTURE = """
<html><body><table>
<tr><th>Date</th><th>IBIT</th><th>GBTC</th><th>Total</th></tr>
<tr><td>11 Jan 2024</td><td>100.0</td><td>(50.0)</td><td>655.3</td></tr>
<tr><td>12 Jan 2024</td><td>200.0</td><td>-</td><td>203.0</td></tr>
<tr><td>15 Jan 2024</td><td>0.0</td><td>0.0</td><td>(120.5)</td></tr>
<tr><td>Average</td><td>100.0</td><td>-16.7</td><td>245.9</td></tr>
</table></body></html>
"""


def test_parse_total_column_with_negatives_and_blanks():
    s = parse_etf_flow_series(_FIXTURE)
    # 3 real date rows; the 'Average' summary footer is dropped (not a date).
    assert len(s) == 3
    assert s.iloc[0] == 655.3
    assert s.iloc[1] == 203.0
    assert s.iloc[2] == -120.5  # parenthesised negative
    assert str(s.index[0].date()) == "2024-01-11"
    assert str(s.index[-1].date()) == "2024-01-15"


def test_parse_returns_empty_on_no_table():
    assert parse_etf_flow_series("<html><body>no table here</body></html>").empty


def test_signal_is_causal_no_look_ahead():
    # A spike in the FINAL bar must not change any earlier signal value.
    idx = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    base = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], index=idx)
    spiked = base.copy()
    spiked.iloc[-1] = 9999.0

    sig_base = etf_flow_signal(base, window=3)
    sig_spiked = etf_flow_signal(spiked, window=3)

    # All overlapping signal values EXCEPT the last must be identical (rolling is backward).
    common = sig_base.index.intersection(sig_spiked.index)[:-1]
    assert len(common) > 0
    assert np.allclose(sig_base.loc[common].to_numpy(), sig_spiked.loc[common].to_numpy())


def test_signal_rolling_sum_value():
    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    s = pd.Series([10.0, 20.0, 30.0, 40.0], index=idx)
    sig = etf_flow_signal(s, window=2)
    # window=2 rolling sum: [30 (10+20), 50 (20+30), 70 (30+40)]
    assert sig.iloc[0] == 30.0
    assert sig.iloc[-1] == 70.0
