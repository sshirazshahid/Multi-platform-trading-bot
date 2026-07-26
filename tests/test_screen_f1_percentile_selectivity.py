"""Tests for research/screen_f1_percentile_selectivity.py (15b screen).

Covers the pure mechanics frozen in the 15b pre-registration:
  * percentile threshold uses strictly-prior prints (no look-ahead), fail-closed
    on short windows,
  * incumbent entry replicates the funding-observable Rev-5 gate (positive
    current print, positive trailing-21 mean, edge floor, bootstrap LB > 0),
  * variant entry = incumbent AND top-quartile persistence (strict subset),
  * decay exit fires on 2 consecutive prints below the trailing-30d median,
  * episode accounting: cost charged once, funding accrued per settlement,
    consec-negative exit, data-end episodes flagged unresolved,
  * primary endpoint (net per settlement-deployed) arithmetic.
"""
from __future__ import annotations

import numpy as np
import pytest

from research.screen_f1_percentile_selectivity import (
    V1_CFG,
    efficiency,
    incumbent_entry_ok,
    make_variant_entry_fn,
    pct_threshold,
    simulate,
)

COST = 0.0050          # frozen binance round-trip fraction
RICH = 0.001           # 10bp/settlement: edge = 0.001*21-0.005 = +160bps >= 150 floor
BASE = 0.0001          # 1bp/settlement baseline: fails the edge floor


def make(values):
    return np.asarray(values, dtype=float)


def ts_for(rates):
    return np.arange(len(rates), dtype="int64") * 28800 + 1_600_000_000


def rich_regime(n_base=270, n_rich=60):
    """Baseline funding then a sustained rich regime that passes ALL gates."""
    return make([BASE] * n_base + [RICH] * n_rich)


# ── percentile threshold ─────────────────────────────────────────────────────
def test_pct_threshold_excludes_current_print():
    rates = make([BASE] * 100)
    rates[99] = 0.05  # huge current print must NOT enter its own threshold
    thr = pct_threshold(rates, 99, window=90, min_prints=60)
    assert thr == pytest.approx(BASE)


def test_pct_threshold_fail_closed_on_short_window():
    rates = make([BASE] * 50)
    assert pct_threshold(rates, 49, window=90, min_prints=60) is None


# ── incumbent entry gate ─────────────────────────────────────────────────────
def test_incumbent_rejects_low_funding_edge_floor():
    assert not incumbent_entry_ok(make([BASE] * 100), 50, COST)


def test_incumbent_accepts_rich_regime():
    assert incumbent_entry_ok(make([RICH] * 100), 50, COST)


def test_incumbent_rejects_nonpositive_current_print():
    rates = make([RICH] * 100)
    rates[50] = 0.0
    assert not incumbent_entry_ok(rates, 50, COST)


def test_incumbent_projection_uses_min_of_current_and_trailing():
    # trailing mean is baseline-poor; a single rich current print must not enter.
    rates = make([BASE] * 100)
    rates[50] = RICH * 10
    assert not incumbent_entry_ok(rates, 50, COST)


# ── variant entry (percentile + persistence overlay) ────────────────────────
def test_variant_requires_persistence():
    rates = rich_regime()
    fn = make_variant_entry_fn(V1_CFG)
    i = 300  # deep inside the rich block: thr(P75 of prior 270) < RICH
    assert fn(rates, i, COST)
    dipped = rates.copy()
    dipped[i - 1] = BASE / 2  # break the middle of the 3-print persistence run
    assert not fn(dipped, i, COST)


def test_variant_is_strict_subset_of_incumbent():
    rates = rich_regime()
    fn = make_variant_entry_fn(V1_CFG)
    for i in range(len(rates)):
        if fn(rates, i, COST):
            assert incumbent_entry_ok(rates, i, COST)


def test_variant_fail_closed_without_percentile_history():
    rates = make([RICH] * 100)  # < pct_min prints available before any i
    fn = make_variant_entry_fn(V1_CFG)
    assert not any(fn(rates, i, COST) for i in range(len(rates)))


# ── episode simulation ───────────────────────────────────────────────────────
def test_episode_accrues_funding_and_charges_cost_once():
    rates = make([RICH] * 100)
    eps = simulate(ts_for(rates), rates, COST, incumbent_entry_ok)
    assert eps, "rich constant series must produce an episode"
    ep = eps[0]
    assert ep["held"] == 42  # F1_MAX_HOLD_SETTLEMENTS
    assert ep["exit_reason"] == "max_hold"
    assert ep["net"] == pytest.approx(42 * RICH - COST)


def test_consec_negative_exit():
    rates = make([RICH] * 40 + [-RICH, -RICH] + [RICH] * 40)
    eps = simulate(ts_for(rates), rates, COST, incumbent_entry_ok)
    ep = eps[0]
    assert ep["exit_reason"] == "consec_negative"
    # entry at i=21 (first index with trailing history): holds 21..41 rich, 40,41 neg
    assert ep["net"] == pytest.approx(19 * RICH - 2 * RICH - COST)


def test_decay_exit_fires_before_incumbent_exit():
    # long rich plateau (trailing-90 median = RICH), then small-but-positive
    # funding below that median: no consec-negative, regime mean stays > 0,
    # but the decay exit fires while the incumbent holds to max-hold.
    rates = make([BASE] * 270 + [RICH] * 100 + [RICH / 5] * 40)
    t = ts_for(rates)
    inc = simulate(t, rates, COST, incumbent_entry_ok)
    var = simulate(t, rates, COST, make_variant_entry_fn(V1_CFG), decay_cfg=V1_CFG)
    assert inc and var
    v = next(e for e in var if e["exit_reason"] == "funding_decay")
    i_ep = [e for e in inc if e["entry_i"] >= 270][0]
    assert v["held"] < i_ep["held"]


def test_data_end_episode_flagged_unresolved():
    rates = make([RICH] * 30)  # entry at 21, data ends at 29 -> 9 settlements
    eps = simulate(ts_for(rates), rates, COST, incumbent_entry_ok)
    ep = eps[0]
    assert ep["exit_reason"] == "data_end"
    assert ep["resolved"] is False


def test_no_overlapping_episodes():
    # On a constant-rich series every settlement passes the entry gate, so the
    # next entry lands EXACTLY on the resume index. Pins the inherited
    # f1_replay_historical `i = j + 1` convention: after a max-hold exit the
    # scan resumes one print past the last held settlement, i.e.
    # entry_i == prev_entry_i + held + 1 (one post-max-hold print is skipped).
    rates = make([RICH] * 200)
    eps = simulate(ts_for(rates), rates, COST, incumbent_entry_ok)
    assert len(eps) >= 2
    for a, b in zip(eps, eps[1:]):
        assert b["entry_i"] == a["entry_i"] + a["held"] + 1


# ── primary endpoint ─────────────────────────────────────────────────────────
def test_efficiency_is_net_per_settlement_deployed():
    eps = [{"net": 0.010, "held": 10}, {"net": -0.002, "held": 10}]
    assert efficiency(eps) == pytest.approx(0.008 / 20)


def test_efficiency_empty_is_nan():
    assert np.isnan(efficiency([]))
