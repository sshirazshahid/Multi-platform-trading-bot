"""Unit tests for the three named-strategy causal signal generators.

Covers (TDD, per the strategy spec sheet):
  * core/signals/session_breakout.py  — Asian Range Breakout (ARB)
  * core/signals/swing_structure.py   — Dow Swing Structure
  * core/signals/squeeze_breakout.py  — Bollinger Squeeze Breakout (BSB)

Every signal obeys the interface contract:
  def signal(df, **params) -> pd.Series  in {-1, 0, +1}, strictly causal.
These are long-biased -> values are in {0, +1}.

Test style mirrors tests/test_tsmom_signal.py: small synthetic OHLCV frames,
named invariants, and by-construction verification of causality where relevant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.signals.session_breakout import signal as arb_signal
from core.signals.squeeze_breakout import signal as bsb_signal
from core.signals.squeeze_breakout import squeeze_state
from core.signals.swing_structure import signal as swing_signal

# Hourly bars, epoch seconds, ascending. Anchored to a 00:00 UTC boundary so
# session-hour math is exact (1751328000 == 2025-07-01 00:00:00 UTC).
_DAY0_UTC = 1_751_328_000
_HOUR = 3_600


def _frame(highs, lows, closes=None, opens=None, vols=None, ts0=_DAY0_UTC):
    """Build an OHLCV DataFrame with hourly ``ts`` from the given series."""
    n = len(highs)
    if closes is None:
        # Midpoint close keeps high/low the binding extremes.
        closes = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    if opens is None:
        opens = list(closes)
    if vols is None:
        vols = [1000.0] * n
    ts = [ts0 + i * _HOUR for i in range(n)]
    return pd.DataFrame(
        {
            "ts": ts,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        }
    )


def _assert_causal(sig_fn, df, **params):
    """By construction: signal[i] must not change when future rows are removed."""
    full = sig_fn(df, **params).to_numpy()
    for cut in (len(df) // 3, len(df) // 2, (2 * len(df)) // 3, len(df) - 1):
        if cut < 1:
            continue
        prefix = sig_fn(df.iloc[:cut].copy(), **params).to_numpy()
        assert np.array_equal(prefix, full[:cut]), f"non-causal at cut={cut}"


# ════════════════════════════════════════════════════════════════════════════
# Asian Range Breakout (ARB)
# ════════════════════════════════════════════════════════════════════════════
#
# Sessions: Asian window = UTC hours [0,6); active window = UTC hours >= 6.
# The volatility gate compares the *current* 14-bar ATR%% against the bottom
# percentile of the trailing 30-bar window. So "narrow" is RELATIVE: we prepend
# 2 days of WIDE intra-bar bars (high ATR%%), then a test day whose Asian-session
# bars are TIGHT intra-bar -> by hour 6 the ATR%% has decayed into the bottom
# quartile and the gate arms. A wide test day keeps ATR%% high -> gate blocks.

_PREFIX_DAYS = 2
_PREFIX_BARS = _PREFIX_DAYS * 24
_PRICE = 100.0


def _noisy_prefix(days=_PREFIX_DAYS, half=1.0):
    """Deterministic wide-range bars: each bar spans ``2*half`` around ``_PRICE``
    -> a high, stable ATR%% baseline that the gate must see the test day fall under."""
    highs, lows = [], []
    for _ in range(days * 24):
        highs.append(_PRICE + half)
        lows.append(_PRICE - half)
    return highs, lows


def _tight_asian_day(asian_half=0.05, active_half=0.05, breakout_at_hour=6, breakout_high=None):
    """24-bar day. Asian hours 0..5: tight intra-bar bars within a small band
    (low ATR%%). Active hours >= 6 stay inside the range unless a breakout high is
    injected. Asian session high/low define the breakout reference."""
    highs, lows = [], []
    asian_high = _PRICE + asian_half
    asian_low = _PRICE - asian_half
    for h in range(24):
        if h < 6:
            hi, lo = asian_high, asian_low
        else:
            hi, lo = _PRICE + active_half - 0.01, _PRICE - active_half + 0.01
        if h == breakout_at_hour and breakout_high is not None:
            hi = breakout_high
        highs.append(hi)
        lows.append(lo)
    return highs, lows, asian_high


def _wide_asian_day(asian_half=1.0, breakout_at_hour=6, breakout_high=None):
    """24-bar day whose Asian session bars are WIDE intra-bar (high ATR%%), so the
    volatility gate keeps reading the day as high-vol and refuses to arm."""
    highs, lows = [], []
    asian_high = _PRICE + asian_half
    asian_low = _PRICE - asian_half
    for h in range(24):
        hi, lo = asian_high, asian_low
        if h == breakout_at_hour and breakout_high is not None:
            hi = breakout_high
        highs.append(hi)
        lows.append(lo)
    return highs, lows, asian_high


def test_arb_narrow_range_breakout_signal():
    """Narrow Asian range; price breaks above range high at hour 6 -> +1 there."""
    ph, pl = _noisy_prefix()
    dh, dl, asian_high = _tight_asian_day(breakout_at_hour=6, breakout_high=_PRICE + 0.5)
    df = _frame(ph + dh, pl + dl)
    sig = arb_signal(df)

    day = _PREFIX_BARS
    assert _PRICE + 0.5 > asian_high  # sanity: it IS a breakout
    assert (sig.iloc[day : day + 6] == 0).all()  # Asian bars never signal
    assert sig.iloc[day + 6] == 1  # breakout bar (hour 6)
    assert set(sig.unique()).issubset({0, 1})


def test_arb_wide_range_no_trade():
    """Same breakout event but the Asian range is wide (high vol) -> gate blocks."""
    ph, pl = _noisy_prefix()
    dh, dl, _ = _wide_asian_day(asian_half=1.0, breakout_at_hour=6, breakout_high=_PRICE + 1.5)
    df = _frame(ph + dh, pl + dl)
    sig = arb_signal(df)
    day = _PREFIX_BARS
    assert (sig.iloc[day:] == 0).all(), "wide range must not trade"


def test_arb_no_breakout_narrow_range():
    """Narrow range but price never exceeds the range high -> 0 all day."""
    ph, pl = _noisy_prefix()
    dh, dl, _ = _tight_asian_day(breakout_at_hour=6, breakout_high=None)
    df = _frame(ph + dh, pl + dl)
    sig = arb_signal(df)
    day = _PREFIX_BARS
    assert (sig.iloc[day:] == 0).all()


def test_arb_multiple_sessions_daily_reset():
    """Day1 narrow breakout (+1); Day2 wide range, no signal. Range resets daily."""
    ph, pl = _noisy_prefix()
    d1h, d1l, _ = _tight_asian_day(breakout_at_hour=6, breakout_high=_PRICE + 0.5)
    d2h, d2l, _ = _wide_asian_day(asian_half=1.0, breakout_at_hour=6, breakout_high=_PRICE + 1.5)
    df = _frame(ph + d1h + d2h, pl + d1l + d2l)
    sig = arb_signal(df)

    d1 = _PREFIX_BARS
    d2 = _PREFIX_BARS + 24
    assert sig.iloc[d1 + 6] == 1  # Day1 breakout fires
    assert (sig.iloc[d1 : d1 + 6] == 0).all()  # Day1 Asian bars flat
    assert (sig.iloc[d2 : d2 + 24] == 0).all()  # Day2 wide range -> nothing


def test_arb_edge_case_thin_asia():
    """Asian session with only 2 bars (hours 0,1); breakout in the active window."""
    ph, pl = _noisy_prefix()
    # Day whose Asian session has only hours 0 and 1 (tight), hours 2..5 inside,
    # then active-window bars; breakout injected at hour 7.
    highs, lows = [], []
    asian_high = _PRICE + 0.05
    asian_low = _PRICE - 0.05
    for h in range(24):
        if h in (0, 1):
            hi, lo = asian_high, asian_low  # tight 2-bar Asian range
        elif h < 6:
            hi, lo = _PRICE, _PRICE  # hours 2..5: flat, inside range
        else:
            hi, lo = _PRICE + 0.04, _PRICE - 0.04  # active window, inside
        if h == 7:
            hi = _PRICE + 0.5  # breakout above asian_high
        highs.append(hi)
        lows.append(lo)
    df = _frame(ph + highs, pl + lows)
    sig = arb_signal(df)
    day = _PREFIX_BARS
    assert sig.iloc[day + 6] == 0  # no breakout at hour 6
    assert sig.iloc[day + 7] == 1, "breakout above 2-bar Asian high fires"


def test_arb_is_causal():
    ph, pl = _noisy_prefix()
    dh, dl, _ = _tight_asian_day(breakout_at_hour=6, breakout_high=_PRICE + 0.5)
    df = _frame(ph + dh, pl + dl)
    _assert_causal(arb_signal, df)


# ════════════════════════════════════════════════════════════════════════════
# Dow Swing Structure
# ════════════════════════════════════════════════════════════════════════════
#
# k_swing = 5: the first evaluable bar is index 5, whose prior-k window is bars
# 0..4. A bar confirms an uptrend when its high tops the prior-5 highs AND its
# low tops the prior-5 lows; that confirming bar also enters long (the first
# higher-low above the prior pivot). A lower-low (below the prior-5 min) while
# in an uptrend breaks structure and flattens. Bars 0..k-1 are unevaluable -> 0.
# Sequences follow the spec sheet directly (no padding).


def test_swing_uptrend_entry_on_pullback_low():
    """HH+HL at bar 5 confirms the uptrend and enters; next bar holds (+1)."""
    # Spec sequence, plus a trailing pullback-hold bar.
    highs = [10, 11, 10.5, 12, 11.5, 13, 12.8]
    lows = [9, 9.5, 9, 10, 10.5, 11, 10.8]
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    # Unevaluable warm-up bars 0..4 are flat.
    assert (sig.iloc[:5] == 0).all()
    # Bar 5: HH=13 > max(prior5)=12, HL=11 > min(prior5)=9 -> uptrend -> +1.
    assert sig.iloc[5] == 1
    # Bar 6: pullback low 10.8 still above structure -> hold long.
    assert sig.iloc[6] == 1
    assert set(sig.unique()).issubset({0, 1})


def test_swing_structure_break_cancels_position():
    """An active long flattens (-> 0) when a lower-low breaks structure."""
    highs = [10, 11, 10.5, 12, 11.5, 13, 12.5]
    lows = [9, 9.5, 9, 10, 10.5, 11, 8.5]  # bar6 low 8.5 < prior-5 min (9)
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    assert sig.iloc[5] == 1  # uptrend confirmed + long
    assert sig.iloc[6] == 0  # lower-low -> structure break -> flat


def test_swing_no_entry_without_hh_hl():
    """No higher-high -> no uptrend confirmation -> signal stays flat."""
    # Highs drift DOWN after bar 0, so a higher-high never forms.
    highs = [10, 9.8, 9.6, 9.5, 9.4, 9.3]
    lows = [9, 8.9, 8.8, 8.7, 8.6, 8.5]
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    assert (sig == 0).all(), "no HH+HL -> never long"


def test_swing_re_entry_after_reset():
    """After a structure break (0), a fresh HH+HL re-confirms and re-enters (+1)."""
    highs = [
        10,
        11,
        10.5,
        12,
        11.5,
        13,  # bar 5: confirm uptrend -> +1
        12.5,  # bar 6: lower-low break -> 0
        9,
        10,
        11,  # bars 7..9: consolidation
        14,  # bar 10: fresh HH+HL -> re-confirm -> +1
    ]
    lows = [
        9,
        9.5,
        9,
        10,
        10.5,
        11,
        8.0,  # bar 6: lower-low (< prior-5 min) -> break
        7.5,
        8.5,
        9.5,
        12.0,  # bar 10: higher-low + higher-high -> re-entry
    ]
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    assert sig.iloc[5] == 1  # first uptrend long
    assert sig.iloc[6] == 0  # structure break flattens
    assert sig.iloc[10] == 1  # re-entry after fresh HH+HL


def test_swing_narrow_hl_no_entry():
    """HH forms but the bar's low ties the prior-5 min -> no higher-low -> flat.

    Bar 5: high 12 tops prior-5 max (10) -> HH, but low 9.0 == prior-5 min (9.0)
    -> NOT a higher-low (and not a lower-low). Uptrend never confirms -> 0.
    """
    highs = [10, 10, 10, 10, 10, 12]
    lows = [9, 9.5, 9.2, 9.3, 9.4, 9.0]
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    assert (sig == 0).all(), "HH without HL -> uptrend false -> flat"


def test_swing_returns_only_long_or_flat():
    highs = [10, 11, 10.5, 12, 11.5, 13, 12.8, 8.0, 9, 10, 14]
    lows = [9, 9.5, 9, 10, 10.5, 11, 10.8, 7.0, 8, 9, 12]
    df = _frame(highs, lows)
    sig = swing_signal(df, k_swing=5)
    assert set(sig.unique()).issubset({0, 1})


def test_swing_is_causal():
    highs = [10, 11, 10.5, 12, 11.5, 13, 12.8, 8.0, 9, 10, 14, 13.5]
    lows = [9, 9.5, 9, 10, 10.5, 11, 10.8, 7.0, 8, 9, 12, 12.5]
    df = _frame(highs, lows)
    _assert_causal(swing_signal, df, k_swing=5)


# ════════════════════════════════════════════════════════════════════════════
# Bollinger Squeeze Breakout (BSB)
# ════════════════════════════════════════════════════════════════════════════


def _close_to_ohlc(closes, half_range=0.05):
    """Make OHLC where high/low straddle close by a tiny constant band so the
    Keltner ATR (mean high-low) is small and BB width drives the squeeze."""
    highs = [c + half_range for c in closes]
    lows = [c - half_range for c in closes]
    return highs, lows


def test_bsb_squeeze_entry_on_breakout():
    """Low-vol squeeze, then close expands above the upper band -> entry, held."""
    # 40 bars of very low volatility (tight squeeze), then a sharp breakout.
    rng = np.random.default_rng(0)
    squeeze = 100.0 + rng.normal(0, 0.02, size=40)  # tiny noise -> narrow bands
    breakout = [101.0, 103.0, 105.0, 107.0, 109.0, 111.0, 113.0, 115.0]
    closes = list(squeeze) + breakout
    highs, lows = _close_to_ohlc(closes)
    df = _frame(highs, lows, closes=closes)

    sig = bsb_signal(df, min_bars_held=6)
    # Some entry must occur in the breakout region.
    assert (sig.to_numpy()[40:] == 1).any(), "breakout after squeeze must enter"
    # First entry bar -> held for min_bars_held consecutive bars.
    entry_idx = int(np.argmax(sig.to_numpy() == 1))
    assert (sig.to_numpy()[entry_idx : entry_idx + 6] == 1).all()
    assert set(sig.unique()).issubset({0, 1})


def test_bsb_no_entry_without_squeeze():
    """Already-wide bands (no squeeze) + a break above upper band -> no entry."""
    rng = np.random.default_rng(1)
    # Persistently high volatility -> bandwidth never bottom-percentile, BB never
    # nested in KC. A final pop above the band must NOT trigger (no release).
    closes = list(100.0 + rng.normal(0, 5.0, size=60))
    closes += [130.0]  # big pop, but bands were already wide
    highs, lows = _close_to_ohlc(closes, half_range=0.05)
    df = _frame(highs, lows, closes=closes)
    sig = bsb_signal(df, min_bars_held=6)
    assert (sig == 0).all(), "no squeeze release -> no entry"


def test_bsb_squeeze_detection_via_width_percentile():
    """Bandwidth in the bottom percentile over the trailing window -> squeezed."""
    rng = np.random.default_rng(2)
    # Wide for 40 bars, then very tight for the last 20 -> last bars squeezed.
    wide = 100.0 + rng.normal(0, 4.0, size=40)
    tight = 100.0 + rng.normal(0, 0.01, size=20)
    closes = list(wide) + list(tight)
    highs, lows = _close_to_ohlc(closes)
    df = _frame(highs, lows, closes=closes)
    sq = squeeze_state(df, bb_period=20, squeeze_pctl=15)
    # The tail (deep into the tight regime) must register as squeezed.
    assert sq.iloc[-1] is np.True_ or bool(sq.iloc[-1]) is True
    assert bool(sq.iloc[55:].any())


def test_bsb_keltner_nesting_squeeze():
    """BB nested inside KC -> squeezed even if width isn't bottom-percentile."""
    # Construct a steady low-noise series: BB (2*std) collapses while KC (1.5*ATR
    # with a constant high-low band) stays wider -> nesting holds.
    closes = [100.0 + 0.001 * (i % 2) for i in range(40)]  # almost constant
    highs = [c + 0.5 for c in closes]  # wide H-L -> big KC ATR
    lows = [c - 0.5 for c in closes]
    df = _frame(highs, lows, closes=closes)
    sq = squeeze_state(df, bb_period=20, kc_period=20, kc_mult=1.5, squeeze_pctl=15)
    assert bool(sq.iloc[-1]) is True, "BB nested in KC must read as squeezed"


def test_bsb_min_bars_held_enforcement():
    """Position is held at least min_bars_held bars from the entry bar."""
    rng = np.random.default_rng(3)
    squeeze = 100.0 + rng.normal(0, 0.02, size=40)
    breakout = [101.0, 103.0, 105.0, 107.0, 109.0, 111.0, 113.0, 115.0, 117.0]
    closes = list(squeeze) + breakout
    highs, lows = _close_to_ohlc(closes)
    df = _frame(highs, lows, closes=closes)
    sig = bsb_signal(df, min_bars_held=6).to_numpy()
    entry_idx = int(np.argmax(sig == 1))
    assert sig[entry_idx] == 1
    # >= min_bars_held consecutive holds from entry.
    assert sig[entry_idx : entry_idx + 6].sum() == 6


def test_bsb_is_causal():
    rng = np.random.default_rng(4)
    squeeze = 100.0 + rng.normal(0, 0.02, size=40)
    breakout = [101.0, 103.0, 105.0, 107.0, 109.0, 111.0, 113.0, 115.0]
    closes = list(squeeze) + breakout
    highs, lows = _close_to_ohlc(closes)
    df = _frame(highs, lows, closes=closes)
    _assert_causal(bsb_signal, df, min_bars_held=6)


# ── cross-cutting contract: empty frame is handled, returns {-1,0,1} dtype ────


def test_all_signals_handle_empty_frame():
    empty = _frame([], [])
    for fn in (arb_signal, swing_signal, bsb_signal):
        out = fn(empty)
        assert isinstance(out, pd.Series)
        assert len(out) == 0
