"""Phase 6 — Spot S2 relative-strength rotation slice (research/PAPER).

Proves deterministic ranking, regime gating (DEFENSIVE -> USDT), top-N
selection, cost-aware rebalance skip, and an end-to-end accept() verdict
recorded to the EvidenceRegistry with a StrategySpec id=SPOT_S2_ROTATION
at promotion_status=research. A NO_GO verdict is a PASS — the deliverable
is pipeline honesty, not alpha.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from scripts.spot_rotation_slice import (
    ADMISSION_RETURN_WINDOW,
    MIN_ADMITTED,
    S2_MAX_SPREAD_BPS,
    S2_MIN_DOLLAR_VOLUME_USD,
    S2_TREND_SMA_PERIOD,
    admit_universe,
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
        volumes[s] = pd.Series(np.full(n, 1_000_000.0 + 100_000 * k))
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


# ── W3: absolute-return admission gate ───────────────────────────────────────
def _panel_from(series_map: dict, vol: float = 1_000_000.0):
    """(closes, opens, volumes) panel from explicit per-symbol close paths."""
    closes = pd.DataFrame({s: pd.Series(v, dtype=float) for s, v in series_map.items()})
    opens = pd.DataFrame({s: closes[s].shift(1).fillna(closes[s].iloc[0])
                          for s in series_map})
    volumes = pd.DataFrame({s: pd.Series(np.full(len(closes), vol)) for s in series_map})
    return closes, opens, volumes


def _legacy_simulate(closes, opens, volumes, regimes, *, top_n=3,
                     rebalance_every=7, venue="binance", spread_bps=None):
    """Faithful replica of the pre-admission simulate_rotation (W3 A/B pin)."""
    from core.cost_model import round_trip_cost
    from scripts.s2_basket_slice import point_in_time_universe
    from scripts.spot_rotation_slice import LOOKBACK

    n = len(closes)
    per_side = round_trip_cost(venue, market_type="spot") / 2.0
    start = LOOKBACK + 1
    rebal = list(range(start, n - 1, max(1, int(rebalance_every))))
    held: dict = {}
    periods: list = []
    for ri, t in enumerate(rebal):
        uni = point_in_time_universe(closes, t, lookback=LOOKBACK)
        regime = regimes[t]
        scores = rank_scores(closes, volumes, t, uni, spread_bps) if uni else {}
        targets = select_targets(scores, regime, top_n=top_n)
        coin_targets = {c: w for c, w in targets.items() if c != "USDT"}
        decision = should_rebalance(held, targets, portfolio_value=1.0, venue=venue)
        cost = 0.0
        if decision["action"] == "REBALANCE":
            cost = decision["est_cost"]
            held = coin_targets
        weights = dict(held)
        ei = t + 1
        t_next = rebal[ri + 1] if ri + 1 < len(rebal) else (n - 1)
        xi = min(t_next + 1, n - 1)
        gross = 0.0
        legs = []
        for s, w in sorted(weights.items()):
            entry_px = float(opens.iloc[ei][s])
            exit_px = float(opens.iloc[xi][s])
            if math.isnan(exit_px):
                path = closes[s].iloc[ei: xi + 1].to_numpy(float)
                path = path[~np.isnan(path)]
                if path.size == 0 or math.isnan(entry_px):
                    continue
                exit_px = float(path[-1])
            if math.isnan(entry_px):
                continue
            r = exit_px / entry_px - 1.0
            gross += w * r
            legs.append({"symbol": s, "weight": float(w), "entry_idx": int(ei),
                         "entry_px": entry_px, "exit_idx": int(xi),
                         "exit_px": exit_px, "gross_pnl": float(r)})
        periods.append({
            "decision_idx": int(t), "entry_idx": int(ei), "exit_idx": int(xi),
            "regime": regime, "action": decision["action"],
            "weights": {k: float(v) for k, v in weights.items()},
            "n_legs": len(legs), "legs": legs,
            "gross_pnl": float(gross), "cost": float(cost),
            "net_pnl": float(gross - cost),
            "fees_slippage": float(per_side * 2.0),
            "label_status": "RESOLVED",
        })
    return periods


def test_admission_constants():
    assert ADMISSION_RETURN_WINDOW == 28
    assert MIN_ADMITTED == 2
    assert S2_MIN_DOLLAR_VOLUME_USD == 50_000_000.0
    assert S2_MAX_SPREAD_BPS == 20.0
    assert S2_TREND_SMA_PERIOD == 50


def test_admit_universe_positive_only_sorted_nan_excluded():
    n = 120
    up = 100 + 0.2 * np.arange(n)
    down = 200 - 0.5 * np.arange(n)
    gap = up.copy()
    gap[72] = np.nan  # NaN at t-28 endpoint for t=100
    closes = pd.DataFrame({"ZUP": pd.Series(up), "ADN": pd.Series(down),
                           "BGAP": pd.Series(gap), "CUP": pd.Series(up.copy())})
    admitted = admit_universe(closes, 100, list(closes.columns))
    assert admitted == ["CUP", "ZUP"]  # sorted; negative + NaN excluded
    # NaN at t itself also excludes
    gap2 = up.copy()
    gap2[100] = np.nan
    closes2 = pd.DataFrame({"ZUP": pd.Series(up), "BGAP": pd.Series(gap2)})
    assert admit_universe(closes2, 100, list(closes2.columns)) == ["ZUP"]


def test_admit_universe_excludes_low_median_dollar_volume():
    n = 120
    up = pd.Series(100 + 0.2 * np.arange(n), dtype=float)
    closes = pd.DataFrame({"LIQ": up, "THIN": up.copy()})
    volumes = pd.DataFrame({
        "LIQ": pd.Series(np.full(n, 1_000_000.0)),
        "THIN": pd.Series(np.full(n, 1_000.0)),
    })
    assert admit_universe(closes, 100, ["LIQ", "THIN"], volumes=volumes) == ["LIQ"]


def test_admit_universe_excludes_wide_spread():
    n = 120
    up = pd.Series(100 + 0.2 * np.arange(n), dtype=float)
    closes = pd.DataFrame({"OK": up, "WIDE": up.copy()})
    volumes = pd.DataFrame({s: pd.Series(np.full(n, 1_000_000.0)) for s in closes.columns})
    admitted = admit_universe(
        closes, 100, ["OK", "WIDE"], volumes=volumes, spread_bps={"WIDE": 25.0}
    )
    assert admitted == ["OK"]


def test_admit_universe_excludes_close_below_sma50():
    n = 120
    trend = np.array([100.0] * 70 + [130.0] * 30 + [90.0] * 20)
    good = 100 + 0.2 * np.arange(n)
    closes = pd.DataFrame({"GOOD": pd.Series(good), "FADE": pd.Series(trend)})
    volumes = pd.DataFrame({s: pd.Series(np.full(n, 1_000_000.0)) for s in closes.columns})
    admitted = admit_universe(closes, 100, ["GOOD", "FADE"], volumes=volumes)
    assert admitted == ["GOOD"]


def test_negative_30d_leader_never_held(monkeypatch):
    """A z-score leader with a NEGATIVE raw 30d return is never admitted/held."""
    import scripts.spot_rotation_slice as srs

    n = 120
    series = {"LLL": 200 - 0.5 * np.arange(n)}  # negative 30d return everywhere
    for i, s in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE")):
        series[s] = 100 + 10 * i + 0.2 * np.arange(n)  # positive 30d return
    closes, opens, volumes = _panel_from(series)
    regimes = ["NORMAL"] * n

    real_rank = rank_scores

    def biased_rank(c, v, t, universe, spread_bps=None):
        scores = real_rank(c, v, t, universe, spread_bps)
        if "LLL" in universe:  # force LLL to the top of any ranking it enters
            scores = {"LLL": 99.0, **{k: w for k, w in scores.items() if k != "LLL"}}
        return scores

    monkeypatch.setattr(srs, "rank_scores", biased_rank)

    legacy = srs.simulate_rotation(closes, opens, volumes, regimes,
                                   admission_window=None)
    assert any("LLL" in p["weights"] for p in legacy)  # ranker would hold it

    gated = srs.simulate_rotation(closes, opens, volumes, regimes)
    assert gated
    for p in gated:
        assert "LLL" not in p["weights"]
        assert p["n_admitted"] == 5


def test_exactly_one_admitted_goes_fully_usdt():
    n = 120
    series = {"AAA": 100 + 0.2 * np.arange(n)}  # the only positive 30d return
    for i, s in enumerate(("BBB", "CCC", "DDD", "EEE", "FFF")):
        series[s] = 200 - (0.3 + 0.05 * i) * np.arange(n)
    closes, opens, volumes = _panel_from(series)
    periods = simulate_rotation(closes, opens, volumes, ["NORMAL"] * n)
    assert periods
    for p in periods:
        assert p["n_admitted"] == 1
        assert p["weights"] == {}  # < MIN_ADMITTED -> 100% USDT
        assert p["net_pnl"] == 0.0


def test_all_negative_universe_goes_fully_usdt():
    n = 120
    series = {s: 200 - (0.3 + 0.05 * i) * np.arange(n)
              for i, s in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"))}
    closes, opens, volumes = _panel_from(series)
    periods = simulate_rotation(closes, opens, volumes, ["NORMAL"] * n)
    assert periods
    for p in periods:
        assert p["n_admitted"] == 0
        assert p["weights"] == {}
        assert p["net_pnl"] == 0.0


def test_admission_determinism():
    closes, opens, volumes = _synthetic_panel()
    regimes = ["NORMAL"] * len(closes)
    a = simulate_rotation(closes, opens, volumes, regimes)
    b = simulate_rotation(closes, opens, volumes, regimes)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert all("n_admitted" in p for p in a)


def test_admission_none_matches_legacy_byte_identical():
    closes, opens, volumes = _synthetic_panel()
    regimes = ["NORMAL"] * len(closes)
    a = simulate_rotation(closes, opens, volumes, regimes, admission_window=None)
    b = _legacy_simulate(closes, opens, volumes, regimes)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert all("n_admitted" not in p for p in a)  # exact legacy shape


def test_spec_documents_admission_gate():
    from scripts.spot_rotation_slice import build_spot_s2_spec

    spec = build_spot_s2_spec()
    assert "admission" in spec.entry_rules
    assert "28d" in spec.entry_rules["admission"]
    assert "$50M" in spec.entry_rules["admission"]
