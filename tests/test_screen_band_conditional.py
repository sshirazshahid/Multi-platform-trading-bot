"""Tests for research/screen_band_conditional.py — the pre-registered
conditional-WR screen on the accuracy-band geometry (13_band_conditional_screen).

Pure-function tests only: bucketing boundaries exactly as pre-registered,
entry-anchored bar selection, trailing percentile, exact binomial tail,
Bonferroni, and joint gate evaluation. No network, no warehouse.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.screen_band_conditional import (  # noqa: E402
    BONFERRONI_M,
    binom_p_upper,
    bonferroni,
    bucket_f1,
    bucket_f3,
    bucket_f4,
    bucket_f6,
    bucket_pctl3,
    evaluate_gates,
    last_closed_idx,
    trailing_pctl,
)


# ---------------------------------------------------------------- bucketing
def test_bucket_f1_boundaries():
    assert bucket_f1(0.69) == "<0.7"
    assert bucket_f1(0.7) == "0.7-1.3"
    assert bucket_f1(1.3) == "0.7-1.3"
    assert bucket_f1(1.31) == ">1.3"


def test_bucket_pctl3_boundaries():
    assert bucket_pctl3(24.9) == "<25th"
    assert bucket_pctl3(25.0) == "25-75th"
    assert bucket_pctl3(75.0) == "25-75th"
    assert bucket_pctl3(75.1) == ">75th"


def test_bucket_f3_boundaries():
    assert bucket_f3(19.9) == "<20"
    assert bucket_f3(20.0) == "20-30"
    assert bucket_f3(30.0) == "20-30"
    assert bucket_f3(30.1) == ">30"


def test_bucket_f4_boundaries():
    assert bucket_f4(49.9) == "<50th"
    assert bucket_f4(50.0) == ">=50th"


def test_bucket_f6_boundaries_and_residual():
    assert bucket_f6(0.10) is None  # below required-condition floor: residual, not gated
    assert bucket_f6(0.15) == "0.15-0.30%"
    assert bucket_f6(0.30) == "0.30-0.60%"
    assert bucket_f6(0.60) == "0.30-0.60%"
    assert bucket_f6(0.61) == ">0.60%"


# ------------------------------------------------- entry-anchored selection
def test_last_closed_idx_takes_last_closed_bar_only():
    tf_sec = 3600
    opens_ms = [0, 3_600_000, 7_200_000]  # bars 00:00, 01:00, 02:00
    # entry at 01:30 -> bar 01:00 still forming; last CLOSED is 00:00 (idx 0)
    assert last_closed_idx(opens_ms, entry_ts_s=5400, tf_sec=tf_sec) == 0
    # entry exactly at 02:00 -> bar 01:00 closes at 02:00 (idx 1)
    assert last_closed_idx(opens_ms, entry_ts_s=7200, tf_sec=tf_sec) == 1
    # entry before any close -> None
    assert last_closed_idx(opens_ms, entry_ts_s=1800, tf_sec=tf_sec) is None


# ------------------------------------------------------- trailing percentile
def test_trailing_pctl_midrank_ties():
    assert trailing_pctl([1.0, 2.0, 3.0, 4.0], 2.5) == 50.0
    # ties get half weight: (1 below + 0.5*1 tie) / 4 = 37.5
    assert trailing_pctl([1.0, 2.0, 3.0, 4.0], 2.0) == 37.5
    assert trailing_pctl([], 1.0) is None


# --------------------------------------------------------------- statistics
def test_binom_p_upper_known_value():
    # P(X >= 6), n=10, p=0.5 = 0.376953125 exactly
    assert binom_p_upper(6, 10, 0.5) == pytest.approx(0.376953125, abs=1e-12)


def test_bonferroni_charges_full_m_and_caps_at_1():
    assert BONFERRONI_M == 16  # pre-registered trial count, frozen
    assert bonferroni(0.001) == pytest.approx(0.016)
    assert bonferroni(0.5) == 1.0


# ----------------------------------------------------------------- gates
def _mk_records(n_win, n_loss, pnl_win=1.0, pnl_loss=-1.0, ts0=0):
    """Ratio-pattern interleave (via gcd) so both chronological halves have
    exactly the overall WR."""
    import math

    g = math.gcd(n_win, n_loss) or 1
    pattern = ["w"] * (n_win // g) + ["l"] * (n_loss // g)
    seq = pattern * g
    return [
        {
            "ts": ts0 + k,
            "net_pnl": pnl_win if kind == "w" else pnl_loss,
            "r_multiple": 0.35 if kind == "w" else -1.0,
        }
        for k, kind in enumerate(seq)
    ]


def test_evaluate_gates_all_pass():
    # 300 wins / 100 losses = 75% WR, n=400: raw binomial p ~4e-5, survives x16
    recs = _mk_records(300, 100, pnl_win=0.7, pnl_loss=-1.0)
    g = evaluate_gates(recs, total_resolved=1000)
    assert g["g1_wr_ge_68"]["ok"]
    assert g["g2_n_ge_300"]["ok"]
    assert g["g3_halves_ge_65"]["ok"]
    assert g["g4_expectancy_pos"]["ok"]  # 300*0.7 - 100*1.0 = +110
    assert g["g5_bonferroni_p"]["ok"]
    assert g["g6_flow_ge_10pct"]["ok"]  # 400/1000 = 40%
    assert g["verdict_go"] is True


def test_evaluate_gates_small_n_fails_g2():
    recs = _mk_records(80, 20)
    g = evaluate_gates(recs, total_resolved=1000)
    assert not g["g2_n_ge_300"]["ok"]
    assert g["verdict_go"] is False


def test_evaluate_gates_low_wr_fails_g1():
    recs = _mk_records(200, 200)  # 50%
    g = evaluate_gates(recs, total_resolved=1000)
    assert not g["g1_wr_ge_68"]["ok"]
    assert g["verdict_go"] is False
