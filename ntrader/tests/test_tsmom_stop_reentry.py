"""Regression: after the 8% disaster stop fires mid-uptrend, the NT port must
RE-ENTER on the next positive-momentum bar — matching the LIVE signal
(core/tsmom_signal.py keys re-entry on `pos is None`), NOT sit flat until
momentum flips negative.

Cloud-review bug_024 (2026-06-20): on_position_closed cleared _open_trade_id
but never reset _intended_long, so after a stop-out the entry gate
`mom_positive and not _intended_long` stayed False for the rest of the
uptrend. Both documented usage paths (phase1_tsmom_backtest.py,
live/paper_trade.py) run enable_stop=True, where this diverges from LIVE.
The existing parity test only runs enable_stop=False so it never caught it.
"""
from __future__ import annotations

import pandas as pd

from ntrader.backtests._run import run_strategy_a


def _uptrend_with_stop_wick(n=110, drop_bar=80):
    """Smooth daily uptrend (closes monotonic up -> 28d momentum always > 0,
    never a momentum-flip close), with ONE bar whose LOW wicks down hard to
    trigger the entry's 8% stop, then closes back on trend. drop_bar must be
    AFTER the first entry (min_bars(28) ~ 50, so the port opens ~bar 49)."""
    ts0, step = 1_600_000_000, 86400
    rows = []
    for i in range(n):
        close = 100.0 * (1.01 ** i)          # monotonic up
        openp = 100.0 * (1.01 ** max(i - 1, 0))
        high = max(openp, close) * 1.001
        low = min(openp, close) * 0.999
        if i == drop_bar:                    # deep wick -> stop fires intrabar
            low = 40.0
        rows.append((ts0 + i * step, openp, high, low, close, 1000.0))
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def test_reenters_after_disaster_stop():
    df = _uptrend_with_stop_wick()
    res = run_strategy_a(base="BTC", lookback=28, enable_stop=True, frame=df)

    # the engineered wick must have actually armed + fired a stop
    assert res["stop_orders"] >= 1, "no stop order was placed"

    opens = [d for d in res["decision_bars"] if d[1] == "OPEN"]
    # BUG: exactly 1 OPEN (initial), then flat for the rest of the uptrend.
    # FIXED: re-opens after the stop -> >= 2 OPENs and long at the end.
    assert len(opens) >= 2, (
        f"port did not re-enter after the stop fired; OPENs={opens} "
        f"(LIVE signal would reopen on the next positive bar)")
    assert res["open_at_end"] == 1, (
        "port should be long at the end of an ongoing uptrend after a stop-out, "
        f"got open_at_end={res['open_at_end']}")
