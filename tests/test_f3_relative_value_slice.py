"""Phase D-F3 — market-neutral relative-value L/S F3 StrategySpec tests.

Extends the shipped S2 basket into a multi-feature ranked, dollar-neutral,
beta-and-cluster-capped relative-value long/short book. Research/PAPER only; a
correct NO_GO is success and live is INFEASIBLE-at-$420.

Covers: point-in-time delisted-safe universe (reused from S2), the multi-feature
composite rank, dollar-neutral construction, per-symbol + correlated-cluster
weight caps, the BTC/ETH beta cap, the F3 StrategySpec, and the end-to-end
register -> accept() -> evidence slice with the pipeline gate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.strategy_spec import StrategySpec
from scripts import f3_relative_value_slice as f3


def _synthetic_panel():
    """6-symbol panel; DEAD delists at bar 60 (NaN thereafter). Distinct
    momentum + volume trends so the cross-sectional rank is non-degenerate."""
    n = 90
    syms = ["A", "B", "C", "D", "E", "DEAD"]
    rng = np.random.default_rng(11)
    closes, opens, vols = {}, {}, {}
    for k, s in enumerate(syms):
        base = 100.0 + 10 * k
        drift = 0.6 * (k - 2)
        path = base + drift * np.arange(n) + rng.normal(0, 0.8, n)
        c = pd.Series(path)
        o = c.shift(1).fillna(c.iloc[0])
        v = pd.Series(1000.0 + 5 * k * np.arange(n) + rng.normal(0, 10, n)).abs()
        if s == "DEAD":
            c.iloc[60:] = np.nan
            o.iloc[61:] = np.nan
            v.iloc[60:] = np.nan
        closes[s], opens[s], vols[s] = c, o, v
    return pd.DataFrame(closes), pd.DataFrame(opens), pd.DataFrame(vols)


# ── point-in-time universe retains delisted names (reused S2 primitive) ──
def test_f3_universe_retains_delisted_before_terminal():
    closes, _, _ = _synthetic_panel()
    assert "DEAD" in f3.point_in_time_universe(closes, 40, lookback=14)
    assert "DEAD" not in f3.point_in_time_universe(closes, 75, lookback=14)


# ── multi-feature composite rank ─────────────────────────────────────────
def test_composite_rank_multi_feature_covers_universe_and_orders():
    closes, _, vols = _synthetic_panel()
    t = 70
    uni = f3.point_in_time_universe(closes, t, lookback=14)
    scores, breakdown = f3.composite_rank(closes, vols, t, uni)
    assert set(scores) == set(uni)
    # strongest positive-drift name (E, highest k among live) should rank top;
    # weakest (A) should rank bottom.
    assert scores["E"] == max(scores.values())
    assert scores["A"] == min(scores.values())
    # multiple features actually contribute (rs + ema_slope + vol_trend present)
    for feat in ("rs3", "rs7", "ema_slope", "vol_trend", "rvol_penalty"):
        assert feat in breakdown


def test_composite_rank_oi_gated_out_by_default():
    closes, _, vols = _synthetic_panel()
    t = 70
    uni = f3.point_in_time_universe(closes, t, lookback=14)
    base, _ = f3.composite_rank(closes, vols, t, uni)
    # supplying OI must NOT change the default score (weight 0 => forward-gated)
    oi = {s: (1.0 if s == "A" else -1.0) for s in uni}
    with_oi, _ = f3.composite_rank(closes, vols, t, uni, oi=oi)
    assert with_oi == base


# ── dollar-neutral construction ──────────────────────────────────────────
def test_ls_weights_are_dollar_neutral():
    scores = {"A": -2.0, "B": -1.0, "C": 0.0, "D": 1.0, "E": 2.0}
    w = f3.ls_weights(scores, top_q=0.4, bottom_q=0.4)
    signed = w["weights"]
    longs = sum(v for v in signed.values() if v > 0)
    shorts = sum(v for v in signed.values() if v < 0)
    assert longs == pytest.approx(0.5, abs=1e-9)
    assert shorts == pytest.approx(-0.5, abs=1e-9)
    assert sum(signed.values()) == pytest.approx(0.0, abs=1e-9)


# ── per-symbol + correlated-cluster caps ─────────────────────────────────
def test_per_symbol_and_cluster_caps_enforced():
    # concentrated book: one name and one cluster dominate.
    weights = {"BTC": 0.45, "ETH": 0.30, "SOL": 0.25, "DOGE": -1.0}
    clusters = {s: f3.cluster_of(s) for s in weights}
    capped = f3.apply_caps(weights, clusters, max_symbol_w=0.35, max_cluster_w=0.60)
    assert all(abs(v) <= 0.35 + 1e-9 for v in capped.values())
    # ETH+SOL+BNB share the major_l1 cluster -> its gross must be capped.
    cl_gross = {}
    for s, v in capped.items():
        cl_gross[clusters[s]] = cl_gross.get(clusters[s], 0.0) + abs(v)
    assert all(g <= 0.60 + 1e-9 for g in cl_gross.values())


# ── BTC/ETH beta cap ─────────────────────────────────────────────────────
def test_beta_cap_neutralizes_excess_beta():
    # net beta before cap = 0.5*1.2 + (-0.5)*0.2 = 0.5 (net long-beta tilt)
    signed = {"X": 0.5, "Y": -0.5}
    betas = {"X": 1.2, "Y": 0.2}
    capped, net_beta = f3.apply_beta_cap(signed, betas, beta_cap=0.10)
    assert abs(net_beta) <= 0.10 + 1e-9


# ── F3 StrategySpec ──────────────────────────────────────────────────────
def test_build_f3_spec_is_strategyspec():
    spec = f3.build_f3_spec()
    assert isinstance(spec, StrategySpec)
    assert spec.id == "F3"
    assert spec.family == "relative_value"
    assert spec.risk_limits.get("dollar_neutral") is True
    assert spec.risk_limits.get("live_feasibility") == "INFEASIBLE-AT-$420"
    # multi-feature ranking is declared in the entry rules
    assert "features" in spec.entry_rules


# ── End-to-end slice: register -> accept() -> evidence (NO_GO ok) ─────────
def test_run_f3_slice_registers_and_records_verdict(tmp_path, monkeypatch):
    closes, opens, vols = _synthetic_panel()
    monkeypatch.setattr(f3, "load_panel_v", lambda *a, **k: (closes, opens, vols))

    reg = tmp_path / "active_strategies.json"
    rep = f3.run_f3_slice(registry_path=reg, rebalance_every=1)

    assert isinstance(rep["verdict"]["accept"], bool)
    assert rep["evidence"]["promotion_status"] in ("PROMOTED", "NO_EDGE")

    import json
    ledger = json.loads(reg.read_text(encoding="utf-8"))
    assert "F3" in ledger["strategies"]

    g = rep["f3_gate"]
    for key in ("rebalances_ok", "pf_ok", "folds_majority_positive_ok",
                "concentration_ok", "net_pnl_positive_2x_stress"):
        assert key in g
    assert rep["oos_metrics"]["live_feasibility"] == "INFEASIBLE-AT-$420"
