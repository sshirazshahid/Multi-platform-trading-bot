"""Clamp-print zero-information screen — unit tests (prereg 39_, sha256 dda32c8cf71d...).

Hermetic: synthetic DataFrames only. NEVER reads data/ (the real run is the
screen CLI, executed only after the prereg commit — pipeline rule 2026-07-17).
Pins the four prereg-critical behaviors: R1 regime derivation from in-file
deltas, clamp classification against the regime-scaled baseline, informative-
strata construction (both arms non-empty), and the CMH decision statistic.
"""
from __future__ import annotations

import math

import pandas as pd

from research.screen_clamp_print import (
    add_regime_hours,
    build_cell_strata,
    classify_clamp,
    cmh_test,
)

H = 3600
T0 = 1_700_000_000


def _df(rows):
    return pd.DataFrame(rows, columns=["ts", "funding_rate", "venue", "symbol"])


def _series(symbol, start, deltas_h, rates, venue="bybit"):
    ts, t = [], start
    for d in [0] + list(deltas_h):
        t += d * H
        ts.append(t)
    return _df([(ts[i], rates[i], venue, symbol) for i in range(len(rates))])


# ── R1: regime from trailing in-file deltas ─────────────────────────────────

def test_regime_needs_three_deltas_then_snaps():
    df = _series("A", T0, [4] * 11, [5e-5] * 12)
    out = add_regime_hours(df)
    r = out["regime_h"].tolist()
    assert r[0] is None and r[1] is None and r[2] is None  # <3 trailing deltas
    assert all(v == 4 for v in r[3:])


def test_regime_switch_8h_to_4h_mid_history():
    # 12 x 8h then 12 x 4h — the TAO shape from the prereg worked example.
    df = _series("TAO", T0, [8] * 12 + [4] * 12, [1e-4] * 13 + [5e-5] * 11)
    r = add_regime_hours(df)["regime_h"].tolist()
    assert r[5] == 8                       # pre-switch
    assert r[-1] == 4                      # deep post-switch
    # just after the switch the trailing-10 median is still 8h — honest lag
    assert r[13] == 8


def test_regime_ragged_gaps_snap_to_8():
    df = _series("B", T0, [8, 8, 16, 8, 16, 8, 8, 8], [1e-4] * 9)
    r = add_regime_hours(df)["regime_h"].tolist()
    assert r[-1] == 8                      # median robust to missed settlements


def test_symbol_with_two_rows_fully_excluded():
    df = _series("C", T0, [8], [1e-4, 1e-4])
    assert add_regime_hours(df)["regime_h"].tolist() == [None, None]


# ── R1: clamp classification against regime-scaled baseline ─────────────────

def test_clamp_baseline_scales_with_regime():
    assert classify_clamp(1e-4, 8) is True
    assert classify_clamp(5e-5, 4) is True
    assert classify_clamp(2.5e-5, 2) is True
    assert classify_clamp(1.25e-5, 1) is True
    assert classify_clamp(1e-4, 4) is False    # 8h baseline in a 4h regime
    assert classify_clamp(3e-5, 4) is False
    assert classify_clamp(-1e-4, 8) is False   # negative print is never a clamp
    assert classify_clamp(None, 8) is False
    assert classify_clamp(1e-4, None) is False


# ── strata: both arms non-empty, outcomes required ──────────────────────────

def _strata_input(pairs):
    """pairs: list of (symbol, rate_now, rate_next). One shared ts, 8h regime."""
    rows = []
    for sym, r_now, r_next in pairs:
        rows += [(T0, r_now, "bybit", sym), (T0 + 8 * H, r_next, "bybit", sym)]
    df = _df(rows)
    df["regime_h"] = 8            # bypass warmup for strata-level tests
    return df


def test_stratum_informative_only_with_both_arms():
    both = build_cell_strata(_strata_input([
        ("CLAMP", 1e-4, 2e-4),    # clamp arm, next positive
        ("CTRL", 3e-4, -1e-4),    # control arm, next negative
    ]))[("bybit", 8)]
    assert len(both) == 1
    a, b, c, d = both[0]
    assert (a, b, c, d) == (1, 0, 0, 1)

    only_clamp = build_cell_strata(_strata_input([("CLAMP", 1e-4, 2e-4)]))
    assert ("bybit", 8) not in only_clamp   # control arm empty -> uninformative


def test_last_row_has_no_outcome_and_drops_from_arms():
    # control symbol's "now" row IS its last row -> no next print -> excluded
    rows = [
        (T0, 1e-4, "bybit", "CLAMP"), (T0 + 8 * H, 2e-4, "bybit", "CLAMP"),
        (T0, 3e-4, "bybit", "CTRL"),   # no successor row
    ]
    df = _df(rows)
    df["regime_h"] = 8
    assert ("bybit", 8) not in build_cell_strata(df)


def test_next_print_never_crosses_venues():
    """Codex INVALID_RUN finding: same symbol on two venues must not share a
    'next print'. The binance row's outcome may only come from binance."""
    rows = [
        # binance BTC: now clamp-positive, its OWN next print is NEGATIVE
        (T0, 1e-4, "binance", "BTC"), (T0 + 8 * H, -2e-4, "binance", "BTC"),
        # bybit BTC at the same timestamps: next print POSITIVE (must not leak)
        (T0, 1e-4, "bybit", "BTC"), (T0 + 8 * H, 3e-4, "bybit", "BTC"),
        # binance control so the binance stratum is informative
        (T0, 3e-4, "binance", "CTRL"), (T0 + 8 * H, 2e-4, "binance", "CTRL"),
    ]
    df = _df(rows)
    df["regime_h"] = 8
    cells = build_cell_strata(df)
    a, b, c, d = cells[("binance", 8)][0]
    # binance clamp arm: its own next print is negative -> failure, NOT success
    assert (a, b) == (0, 1), "binance outcome leaked from bybit's next print"
    assert (c, d) == (1, 0)


def test_negative_prints_join_neither_arm():
    out = build_cell_strata(_strata_input([
        ("CLAMP", 1e-4, 2e-4),
        ("NEG", -3e-4, 2e-4),     # negative print: not clamp, not control
    ]))
    assert ("bybit", 8) not in out


# ── CMH decision statistic ──────────────────────────────────────────────────

def test_cmh_null_identical_rates():
    strata = [(1, 1, 1, 1)] * 40
    chi2, p, or_mh, n = cmh_test(strata)
    assert n == 40
    assert chi2 < 1e-9 and p > 0.9
    assert abs(or_mh - 1.0) < 1e-9


def test_cmh_strong_signal_detected():
    strata = [(1, 0, 0, 1)] * 40          # clamp always right, control always wrong
    chi2, p, or_mh, n = cmh_test(strata)
    assert p < 1e-6
    assert or_mh == math.inf or or_mh > 100


def test_cmh_empty_input_is_untestable():
    chi2, p, or_mh, n = cmh_test([])
    assert n == 0 and p is None
