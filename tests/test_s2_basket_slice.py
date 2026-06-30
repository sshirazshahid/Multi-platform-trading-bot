"""Phase 2 — S2 cross-sectional long-short basket vertical slice.

Proves a survivorship-bias-free, point-in-time universe (delisted names with
terminal price paths INCLUDED in historical folds), a decide-t / fill-t+1
rebalance convention, an after-cost accept() verdict recorded to the
EvidenceRegistry, and the live basket marked INFEASIBLE-AT-$420.

A correct NO_GO verdict is a PASS here; the deliverable is pipeline correctness
under capital-preservation discipline, NOT alpha.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.s2_basket_slice import (
    point_in_time_universe,
    simulate_basket,
)


def _synthetic_panel():
    """5-symbol panel where DEAD delists at bar 50 (NaN thereafter)."""
    n = 80
    syms = ["A", "B", "C", "D", "DEAD"]
    rng = np.random.default_rng(7)
    closes = {}
    opens = {}
    for k, s in enumerate(syms):
        base = 100.0 + 10 * k
        drift = 0.5 * (k - 2)  # spread of momentum across the cross-section
        path = base + drift * np.arange(n) + rng.normal(0, 1.0, n)
        c = pd.Series(path)
        o = c.shift(1).fillna(c.iloc[0])  # opens distinct from closes
        if s == "DEAD":
            c.iloc[50:] = np.nan  # delisted: terminal price path ends at bar 49
            o.iloc[51:] = np.nan
        closes[s] = c
        opens[s] = o
    return pd.DataFrame(closes), pd.DataFrame(opens)


# ── point-in-time universe includes delisted names while their path exists ────
def test_point_in_time_universe_includes_delisted_before_terminal():
    closes, _ = _synthetic_panel()
    uni_live = point_in_time_universe(closes, 40, lookback=7)
    assert "DEAD" in uni_live  # still listed at bar 40 -> must be selectable
    uni_after = point_in_time_universe(closes, 60, lookback=7)
    assert "DEAD" not in uni_after  # delisted -> dropped, no look-ahead resurrection


def test_delisted_symbol_appears_in_a_historical_fold():
    closes, opens = _synthetic_panel()
    periods = simulate_basket(closes, opens, lookback=7, rebalance_every=1,
                              top_q=0.5, bottom_q=0.5, venue="binance")
    # Universe membership in an EARLY (historical) rebalance must include DEAD,
    # proving the backtest is constructed point-in-time with delisted names.
    early = [p for p in periods if p["decision_idx"] < 49]
    seen = set()
    for p in early:
        seen.update(leg["symbol"] for leg in p["legs"])
    assert "DEAD" in seen


# ── decide-t / fill-t+1 rebalance convention ─────────────────────────────────
def test_rebalance_fill_convention_decide_t_fill_t_plus_1():
    closes, opens = _synthetic_panel()
    periods = simulate_basket(closes, opens, lookback=7, rebalance_every=5,
                              top_q=0.5, bottom_q=0.5, venue="binance")
    assert periods
    p = periods[0]
    t = p["decision_idx"]
    for leg in p["legs"]:
        assert leg["entry_idx"] == t + 1  # rank at closed bar t, fill next bar
        assert leg["entry_px"] == opens.iloc[t + 1][leg["symbol"]]


def test_basket_periods_are_resolved_and_deterministic():
    closes, opens = _synthetic_panel()
    a = simulate_basket(closes, opens, lookback=7, rebalance_every=3, venue="binance")
    b = simulate_basket(closes, opens, lookback=7, rebalance_every=3, venue="binance")
    assert a and all(p["label_status"] == "RESOLVED" for p in a)
    assert all("net_pnl" in p and p["net_pnl"] is not None for p in a)
    assert np.allclose([p["net_pnl"] for p in a], [p["net_pnl"] for p in b])


# ── end-to-end: accept() verdict recorded + INFEASIBLE-AT-$420 ───────────────
def test_s2_slice_end_to_end_records_verdict_and_infeasible(tmp_path):
    from scripts.s2_basket_slice import run_s2_slice

    reg = tmp_path / "active_strategies.json"
    report = run_s2_slice(registry_path=reg)

    # accept() ran and produced a verdict with all eight sub-gates
    verdict = report["verdict"]
    assert isinstance(verdict["accept"], bool)
    assert len(verdict["sub_gates"]) == 8

    # EvidenceRegistry row for S2 with the survivorship-free universe method
    ev = report["evidence"]
    assert ev["strategy_id"] == "S2"
    assert ev["oos_metrics"]["accept"] == verdict["accept"]
    assert "delist" in str(ev["universe_construction_method"]).lower()
    assert reg.exists()

    # live basket is marked INFEASIBLE at $420
    assert report["live_feasibility"] == "INFEASIBLE-AT-$420"
