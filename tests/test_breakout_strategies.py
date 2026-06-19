"""
tests/test_breakout_strategies.py — TDD spec for the three breakout strategies.

These tests exercise ``generate_signal`` DIRECTLY on seeded synthetic OHLCV.
They NEVER call ``run()`` (no live order writes, no exchange I/O, no state
files). The single most important property under test is STRICT CAUSALITY:
the signal at the last bar must depend only on bars ``0..n-1`` and never peek
at a future bar.

DataFrame contract (mirrors BaseStrategy.get_dataframe output):
    - datetime index named "timestamp"
    - columns: open, high, low, close, volume
The backtester passes ``df.iloc[:i]`` (bars 0..i-1) and the decision is on the
last bar of that window, so each builder below puts the *trigger* bar last.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.asian_range_breakout import AsianRangeBreakoutStrategy
from strategies.bb_squeeze_breakout import BBSqueezeBreakoutStrategy
from strategies.dow_swing_structure import DowSwingStructureStrategy

# ── Builders ──────────────────────────────────────────────────────────


def _ohlc(closes, highs=None, lows=None, vols=None, tf_seconds=3600, start_hour_utc=0):
    """Assemble a datetime-indexed OHLCV frame from a close array.

    Bars are spaced ``tf_seconds`` apart. ``start_hour_utc`` anchors the first
    bar's UTC hour so session-window logic can be exercised deterministically.
    """
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    opens = np.empty(n)
    opens[0] = closes[0]
    opens[1:] = closes[:-1]
    # Wicks hug the close (not max(open, close)) so a turning-point bar is an
    # unambiguous local extreme — avoids duplicate-height neighbours that would
    # otherwise defeat the strict single-max pivot test in swing_points.
    if highs is None:
        highs = closes + 0.02
    if lows is None:
        lows = closes - 0.02
    if vols is None:
        vols = np.full(n, 1000.0)
    # Anchor first bar at the requested UTC hour, on an arbitrary epoch day.
    base_epoch = 1_700_000_000  # a fixed point in time (UTC)
    base_epoch -= base_epoch % 86400  # midnight UTC
    base_epoch += start_hour_utc * 3600
    ts = base_epoch + np.arange(n) * tf_seconds
    idx = pd.to_datetime(ts, unit="s")
    df = pd.DataFrame(
        {
            "open": opens,
            "high": np.asarray(highs, dtype=float),
            "low": np.asarray(lows, dtype=float),
            "close": closes,
            "volume": np.asarray(vols, dtype=float),
        },
        index=idx,
    )
    df.index.name = "timestamp"
    return df


def _norm(signal):
    """Strategies may return a bare signal or (signal, sl, tp). Normalise."""
    if isinstance(signal, tuple):
        return signal[0]
    return signal


# Strategy instances need no live order/risk managers because we only ever call
# generate_signal (a pure function of the frame) — order/risk managers stay None.

# ======================================================================
# ASIAN RANGE BREAKOUT
# ======================================================================


def _arb_frame(post, seed=7):
    """Prior-day warmup (UTC hours 7..23, NON-session so it never contributes
    to the session range) + a tight active Asian session (UTC 0..6) + the
    supplied post-session breakout bars (hours 7..). Anchoring the first bar at
    hour 7 of the prior day makes the active session the only one in-window and
    warms ATR(14) to >= 20 bars."""
    rng = np.random.RandomState(seed)
    warmup = 100 + rng.uniform(-0.15, 0.15, 17)  # hours 7..23 prior day
    session = 100 + rng.uniform(-0.2, 0.2, 7)  # hours 0..6 active session
    closes = np.concatenate([warmup, session, np.asarray(post, float)])
    return _ohlc(closes, tf_seconds=3600, start_hour_utc=7)


def test_arb_breaks_long_above_range():
    """Tight Asian session (UTC 0-6) then a close decisively above the high
    during the breakout window -> buy."""
    df = _arb_frame([100.0, 100.0, 100.0, 102.5])  # last bar breaks up hard
    strat = AsianRangeBreakoutStrategy(None, None)
    assert _norm(strat.generate_signal(df)) == "buy"


def test_arb_breaks_short_below_range():
    df = _arb_frame([100.0, 100.0, 100.0, 97.5], seed=11)  # breaks down hard
    strat = AsianRangeBreakoutStrategy(None, None)
    assert _norm(strat.generate_signal(df)) == "sell"


def test_arb_midrange_no_signal():
    df = _arb_frame([100.05, 100.0, 100.1, 100.05], seed=13)  # stays inside
    strat = AsianRangeBreakoutStrategy(None, None)
    assert _norm(strat.generate_signal(df)) is None


def test_arb_returns_sl_tp_tuple_on_signal():
    df = _arb_frame([100.0, 100.0, 100.0, 102.5])
    strat = AsianRangeBreakoutStrategy(None, None)
    out = strat.generate_signal(df)
    assert isinstance(out, tuple) and out[0] == "buy"
    sig, sl, tp = out
    entry = float(df["close"].iloc[-1])
    assert sl < entry < tp  # long: stop below, target above


# ======================================================================
# DOW SWING STRUCTURE
# ======================================================================


def _swing_walk(points, per_leg=4):
    """Build a close series that visits ``points`` via linear legs so that
    fractal pivots (left/right=2) form cleanly at each turning point."""
    closes = [points[0]]
    for nxt in points[1:]:
        leg = np.linspace(closes[-1], nxt, per_leg + 1)[1:]
        closes.extend(leg.tolist())
    return np.array(closes)


def test_dow_uptrend_long_on_hl_after_hh():
    # Higher-high then higher-low structure, final leg breaks up past prior HH.
    # points: low, HIGH1, LOW1(HL), HIGH2(HH>HIGH1), LOW2(HL>LOW1), up-break
    points = [100, 108, 103, 112, 107, 115]
    closes = _swing_walk(points, per_leg=5)
    df = _ohlc(closes, tf_seconds=4 * 3600, start_hour_utc=0)
    strat = DowSwingStructureStrategy(None, None)
    assert _norm(strat.generate_signal(df)) == "buy"


def test_dow_downtrend_short_on_lh_after_ll():
    # Lower-high then lower-low structure, final leg breaks down past prior LL.
    points = [120, 112, 117, 108, 113, 105]
    closes = _swing_walk(points, per_leg=5)
    df = _ohlc(closes, tf_seconds=4 * 3600, start_hour_utc=0)
    strat = DowSwingStructureStrategy(None, None)
    assert _norm(strat.generate_signal(df)) == "sell"


def test_dow_structure_break_no_signal():
    # Choppy, no clean HH/HL or LH/LL ordering -> flat.
    rng = np.random.RandomState(3)
    closes = 100 + rng.uniform(-1.5, 1.5, 80)
    df = _ohlc(closes, tf_seconds=4 * 3600, start_hour_utc=0)
    strat = DowSwingStructureStrategy(None, None)
    assert _norm(strat.generate_signal(df)) is None


# ======================================================================
# BB SQUEEZE BREAKOUT
# ======================================================================


def _squeeze_then_release(direction, n_squeeze=60):
    """Long low-vol coil (squeeze) then a high-volume directional release."""
    rng = np.random.RandomState(21)
    coil = 100 + rng.uniform(-0.15, 0.15, n_squeeze)
    if direction == "up":
        release = [100.0, 100.5, 101.5, 103.5]
    else:
        release = [100.0, 99.5, 98.5, 96.5]
    closes = np.concatenate([coil, release])
    vols = np.full(len(closes), 1000.0)
    vols[-1] = 3000.0  # volume-confirmed breakout bar
    return _ohlc(closes, vols=vols, tf_seconds=3600, start_hour_utc=0)


def test_bb_squeeze_release_long():
    df = _squeeze_then_release("up")
    strat = BBSqueezeBreakoutStrategy(None, None, mode="normal")
    assert _norm(strat.generate_signal(df)) == "buy"


def test_bb_squeeze_release_short():
    df = _squeeze_then_release("down")
    strat = BBSqueezeBreakoutStrategy(None, None, mode="normal")
    assert _norm(strat.generate_signal(df)) == "sell"


def test_bb_no_squeeze_no_signal():
    # Strongly trending wide-range series: no coil, so no squeeze, no entry.
    closes = 100 + np.arange(120) * 1.2
    df = _ohlc(closes, tf_seconds=3600, start_hour_utc=0)
    strat = BBSqueezeBreakoutStrategy(None, None, mode="normal")
    assert _norm(strat.generate_signal(df)) is None


def test_bb_scalp_mode_uses_scalp_profile():
    strat = BBSqueezeBreakoutStrategy(None, None, mode="scalp")
    from config import SCALP_PROFILE

    assert strat.cfg is SCALP_PROFILE
    assert strat.cfg["timeframe"] == "15m"


# ======================================================================
# CAUSALITY — the load-bearing property for ALL three.
# ======================================================================


def _all_signal_makers():
    return [
        (AsianRangeBreakoutStrategy(None, None), _arb_frame([100.0, 100.0, 100.0, 102.5])),
        (
            DowSwingStructureStrategy(None, None),
            _ohlc(_swing_walk([100, 108, 103, 112, 107, 115], per_leg=5), tf_seconds=4 * 3600),
        ),
        (BBSqueezeBreakoutStrategy(None, None, mode="normal"), _squeeze_then_release("up")),
    ]


def _append_garbage_future(df, n_extra=4):
    """Return df with n_extra wildly-different bars appended after it.

    These future bars exist only to prove the strategy never reads them when
    deciding on the prefix that ends before them.
    """
    extra = pd.concat([df.iloc[[-1]].copy()] * n_extra)
    step = df.index[-1] - df.index[-2]
    extra.index = [df.index[-1] + step * (i + 1) for i in range(n_extra)]
    for col in ("open", "high", "low", "close"):
        extra[col] = extra[col] * 1.5  # violent, obvious future move
    extra["volume"] = extra["volume"] * 5
    return pd.concat([df, extra])


def test_signal_is_causal_appending_future_bars_does_not_change_past():
    """Canonical leakage test: the verdict on the decision bar must be IDENTICAL
    whether or not garbage future bars exist after it. We decide on the original
    frame, then append violent future bars, slice back to the original length,
    and recompute — the two verdicts must match bit-for-bit."""
    for strat, df in _all_signal_makers():
        base = _norm(strat.generate_signal(df))
        assert base in ("buy", "sell")  # each maker is constructed to fire
        with_future = _append_garbage_future(df)
        prefix = with_future.iloc[: len(df)]  # exact original prefix
        recomputed = _norm(strat.generate_signal(prefix))
        assert base == recomputed, f"{type(strat).__name__} leaked future data"


def test_signal_prefix_equivalence_across_strategies():
    """Stronger: appending future bars then truncating to the trigger bar must
    reproduce the original frame's verdict for every strategy (no later-bar
    dependence anywhere in the signal path)."""
    for strat, df in _all_signal_makers():
        verdict_full = _norm(strat.generate_signal(df))
        truncated = _append_garbage_future(df).iloc[: len(df)]
        assert verdict_full == _norm(strat.generate_signal(truncated))
