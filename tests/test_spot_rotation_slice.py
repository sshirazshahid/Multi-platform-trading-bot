"""Phase 6 — Spot S2 relative-strength rotation slice (research/PAPER).

Proves deterministic ranking, regime gating (DEFENSIVE -> USDT), top-N
selection, cost-aware rebalance skip, and an end-to-end accept() verdict
recorded to the EvidenceRegistry with a StrategySpec id=SPOT_S2_ROTATION
at promotion_status=research. A NO_GO verdict is a PASS — the deliverable
is pipeline honesty, not alpha.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.spot_rotation_slice import (
    rank_scores,
    select_targets,
    should_rebalance,
    simulate_rotation,
)


def _synthetic_panel(n=120, syms=("A", "B", "C", "D", "E", "F")):
    rng = np.random.default_rng(11)
    closes, opens, volumes = {}, {}, {}
    for k, s in enumerate(syms):
        base = 100.0 + 10 * k
        drift = 0.4 * (k - 2)  # spread of relative strength
        path = base + drift * np.arange(n) + rng.normal(0, 1.0, n)
        c = pd.Series(np.abs(path) + 1.0)
        opens[s] = c.shift(1).fillna(c.iloc[0])
        closes[s] = c
        volumes[s] = pd.Series(np.full(n, 1000.0 + 100 * k))
    return pd.DataFrame(closes), pd.DataFrame(opens), pd.DataFrame(volumes)


# ── rank determinism ─────────────────────────────────────────────────────────
def test_rank_scores_deterministic():
    closes, _, volumes = _synthetic_panel()
    uni = list(closes.columns)
    a = rank_scores(closes, volumes, 100, uni)
    b = rank_scores(closes, volumes, 100, uni)
    assert list(a.keys()) == list(b.keys())  # identical ordering
    assert list(a.values()) == list(b.values())
    # ordered strongest-first
    vals = list(a.values())
    assert vals == sorted(vals, reverse=True)


def test_rank_scores_tie_break_by_symbol():
    n = 120
    c = pd.Series(np.linspace(100, 120, n))
    closes = pd.DataFrame({"ZZZ": c, "AAA": c.copy()})
    volumes = pd.DataFrame({"ZZZ": pd.Series(np.full(n, 1.0)),
                            "AAA": pd.Series(np.full(n, 1.0))})
    scores = rank_scores(closes, volumes, 100, ["ZZZ", "AAA"])
    assert list(scores.keys()) == ["AAA", "ZZZ"]  # equal score -> alphabetical


# ── regime gating + top-N selection ──────────────────────────────────────────
def test_defensive_regime_goes_fully_usdt():
    targets = select_targets({"A": 2.0, "B": 1.0, "C": 0.5}, "DEFENSIVE", top_n=3)
    assert targets == {"USDT": 1.0}


def test_reduced_regime_goes_partly_usdt():
    targets = select_targets({"A": 2.0, "B": 1.0, "C": 0.5}, "REDUCED", top_n=2)
    assert targets["USDT"] == 0.5
    assert abs(sum(targets.values()) - 1.0) < 1e-9
    assert all(w >= 0 for w in targets.values())  # long-only


def test_normal_regime_selects_top_n():
    scores = {"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.5, "E": 0.1}
    targets = select_targets(scores, "NORMAL", top_n=3)
    held = [c for c in targets if c != "USDT" and targets[c] > 0]
    assert sorted(held) == ["A", "B", "C"]  # strongest 3
    assert abs(sum(targets.values()) - 1.0) < 1e-9
    # top_n clamped to 2..4
    assert len([c for c in select_targets(scores, "NORMAL", top_n=10) if c != "USDT"]) == 4
    assert len([c for c in select_targets(scores, "NORMAL", top_n=1) if c != "USDT"]) == 2


# ── cost-aware rebalance skip ────────────────────────────────────────────────
def test_should_rebalance_skips_tiny_drift():
    cur = {"A": 0.334, "B": 0.333, "C": 0.333}
    tgt = {"A": 0.333, "B": 0.334, "C": 0.333}
    d = should_rebalance(cur, tgt, portfolio_value=1000.0, venue="binance")
    assert d["action"] == "SKIP"


def test_should_rebalance_acts_on_large_drift():
    cur = {"A": 1.0}
    tgt = {"B": 0.5, "C": 0.5}
    d = should_rebalance(cur, tgt, portfolio_value=1000.0, venue="binance")
    assert d["action"] == "REBALANCE"
    assert d["est_benefit"] > d["est_cost"] > 0


def test_simulate_rotation_defensive_periods_hold_no_coins():
    closes, opens, volumes = _synthetic_panel()
    regimes = ["DEFENSIVE"] * len(closes)
    periods = simulate_rotation(closes, opens, volumes, regimes,
                                rebalance_every=7, venue="binance")
    assert periods
    for p in periods:
        assert p["regime"] == "DEFENSIVE"
        assert p["weights"] == {}  # fully USDT
        assert p["net_pnl"] == 0.0


def test_simulate_rotation_deterministic_and_resolved():
    closes, opens, volumes = _synthetic_panel()
    regimes = ["NORMAL"] * len(closes)
    a = simulate_rotation(closes, opens, volumes, regimes,
                          rebalance_every=5, venue="binance")
    b = simulate_rotation(closes, opens, volumes, regimes,
                          rebalance_every=5, venue="binance")
    assert a and all(p["label_status"] == "RESOLVED" for p in a)
    assert np.allclose([p["net_pnl"] for p in a], [p["net_pnl"] for p in b])
    # long-only: weights non-negative, sum <= 1
    for p in a:
        assert all(w >= 0 for w in p["weights"].values())
        assert sum(p["weights"].values()) <= 1.0 + 1e-9


# ── end-to-end: verdict + registry + spec ────────────────────────────────────
def test_spot_rotation_slice_end_to_end(tmp_path):
    from scripts.spot_rotation_slice import run_spot_rotation_slice

    reg = tmp_path / "active_strategies.json"
    spec_dir = tmp_path / "specs"
    report = run_spot_rotation_slice(registry_path=reg, spec_dir=spec_dir)

    verdict = report["verdict"]
    assert isinstance(verdict["accept"], bool)
    assert len(verdict["sub_gates"]) == 8

    ev = report["evidence"]
    assert ev["strategy_id"] == "SPOT_S2_ROTATION"
    assert ev["promotion_status"] == "research"
    assert ev["oos_metrics"]["accept"] == verdict["accept"]
    assert reg.exists()

    # StrategySpec registered at research status (never enters the live scorer)
    from core.strategy_spec import approved_symbols, load_spec
    spec = load_spec("SPOT_S2_ROTATION", directory=spec_dir)
    assert spec.market_type == "spot"
    assert spec.promotion_status == "research"
    assert approved_symbols([spec]) == set()
