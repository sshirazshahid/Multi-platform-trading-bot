"""Rev 5 Phase 5 — Spot S1 defensive allocation + rebalance (risk control, PAPER-only)."""
from __future__ import annotations

import json

import pytest

from core.decision.promotion_loop import init_evidence_registry, load_registry
from core.spot_regime import (
    ALLOCATION_TARGETS,
    REGIME_DEFENSIVE,
    REGIME_NORMAL,
    REGIME_REDUCED,
    build_spot_s1_spec,
    closed_daily_closes,
    daily_ema200_regime,
    rebalance_decision,
)

DAY = 86400


def _closes(last: float, n: int = 250, base: float = 100.0) -> list:
    """n-1 flat closes at ``base`` then one final close at ``last`` (EMA200 ~ base)."""
    return [base] * (n - 1) + [last]


# ── regime transitions ─────────────────────────────────────────────────
def test_regime_normal_both_above():
    assert daily_ema200_regime(_closes(110.0), _closes(120.0)) == REGIME_NORMAL


def test_regime_reduced_exactly_one_below():
    assert daily_ema200_regime(_closes(110.0), _closes(80.0)) == REGIME_REDUCED
    assert daily_ema200_regime(_closes(80.0), _closes(110.0)) == REGIME_REDUCED


def test_regime_defensive_both_below():
    assert daily_ema200_regime(_closes(80.0), _closes(70.0)) == REGIME_DEFENSIVE


def test_regime_requires_enough_history():
    with pytest.raises(ValueError):
        daily_ema200_regime([100.0] * 50, [100.0] * 50)


def test_closed_daily_closes_drops_in_progress_candle():
    t0 = 1_700_000_000 - (1_700_000_000 % DAY)
    candles = [[(t0 + i * DAY) * 1000, 0, 0, 0, 100.0 + i, 0] for i in range(3)]
    # "now" is mid-way through the 3rd candle's day -> it is still open
    closes = closed_daily_closes(candles, now_ts=t0 + 2 * DAY + 3600)
    assert closes == [100.0, 101.0]
    # after the 3rd day completes, it is included
    closes = closed_daily_closes(candles, now_ts=t0 + 3 * DAY)
    assert closes == [100.0, 101.0, 102.0]


# ── allocation targets: no leverage, no shorting by construction ───────
def test_targets_long_only_no_leverage_all_regimes():
    for regime, targets in ALLOCATION_TARGETS.items():
        assert all(w >= 0.0 for w in targets.values()), regime
        assert sum(targets.values()) <= 1.0 + 1e-9, regime
        assert set(targets) == {"BTC", "ETH", "SOL", "BNB", "USDT"}
    # DEFENSIVE = max USDT
    assert ALLOCATION_TARGETS[REGIME_DEFENSIVE]["USDT"] == max(
        t["USDT"] for t in ALLOCATION_TARGETS.values()
    )


# ── rebalance decision ─────────────────────────────────────────────────
def _current_near(targets: dict, off_coin: str = "", off: float = 0.0) -> dict:
    cur = dict(targets)
    if off_coin:
        cur[off_coin] = cur[off_coin] + off
        cur["USDT"] = cur["USDT"] - off
    return cur


def test_drift_below_threshold_no_action():
    targets = ALLOCATION_TARGETS[REGIME_NORMAL]
    cur = _current_near(targets, "BTC", 0.03)  # 3% drift < 5%
    d = rebalance_decision(cur, REGIME_NORMAL, portfolio_value=1000.0)
    assert d["action"] == "HOLD"
    assert "drift" in d["reason"]
    assert d["trades"] == []


def test_drift_above_threshold_and_benefit_above_cost_rebalances():
    targets = ALLOCATION_TARGETS[REGIME_NORMAL]
    cur = _current_near(targets, "BTC", 0.10)
    d = rebalance_decision(cur, REGIME_NORMAL, portfolio_value=1000.0)
    assert d["action"] == "REBALANCE"
    assert d["est_benefit"] > d["est_cost"] > 0
    sells = [t for t in d["trades"] if t["side"] == "SELL"]
    assert any(t["coin"] == "BTC" for t in sells)


def test_benefit_at_or_below_cost_skips():
    targets = ALLOCATION_TARGETS[REGIME_NORMAL]
    cur = _current_near(targets, "BTC", 0.10)
    d = rebalance_decision(
        cur, REGIME_NORMAL, portfolio_value=1000.0, benefit_rate=0.0001
    )
    assert d["action"] == "HOLD"
    assert "benefit" in d["reason"]


def test_shorting_and_leverage_impossible():
    targets = ALLOCATION_TARGETS[REGIME_NORMAL]
    with pytest.raises(ValueError):  # negative (short) current weight
        rebalance_decision({**targets, "BTC": -0.1}, REGIME_NORMAL, portfolio_value=1000.0)
    with pytest.raises(ValueError):  # weights > 1 (leverage)
        rebalance_decision({**targets, "BTC": 0.9}, REGIME_NORMAL, portfolio_value=1000.0)
    # sells never exceed what is held: SELL notional <= current weight * pv
    cur = _current_near(targets, "ETH", 0.15)
    d = rebalance_decision(cur, REGIME_NORMAL, portfolio_value=1000.0)
    for t in d["trades"]:
        if t["side"] == "SELL":
            assert t["notional"] <= cur[t["coin"]] * 1000.0 + 1e-6


def test_unknown_regime_rejected():
    with pytest.raises(ValueError):
        rebalance_decision(
            ALLOCATION_TARGETS[REGIME_NORMAL], "PANIC", portfolio_value=1000.0
        )


# ── StrategySpec registration ──────────────────────────────────────────
def test_spot_s1_spec_registers(tmp_path):
    reg_path = tmp_path / "active_strategies.json"
    init_evidence_registry(reg_path)
    spec = build_spot_s1_spec()
    assert spec.id == "SPOT_S1_DEFENSIVE"
    assert spec.family == "risk_control"
    assert spec.market_type == "spot"
    assert spec.risk_limits.get("max_leverage") == 1.0
    assert spec.risk_limits.get("shorting") is False
    spec.register(registry_path=reg_path)
    reg = load_registry(reg_path)
    assert "SPOT_S1_DEFENSIVE" in reg["strategies"]


# ── PAPER-only wiring through spot_manager ─────────────────────────────
def _mgr(monkeypatch, tmp_path):
    import core.spot_manager as sm

    monkeypatch.setattr(sm, "STATE_FILE", tmp_path / "spot_portfolio.json")
    monkeypatch.setattr(sm, "RECOMMENDATIONS_FILE", tmp_path / "spot_recs.jsonl")
    return sm.SpotPortfolioManager(exchanges={}), sm


def test_s1_cycle_disabled_by_default(monkeypatch, tmp_path):
    mgr, sm = _mgr(monkeypatch, tmp_path)
    out = mgr.run_s1_cycle(
        {"BTC": _closes(110.0), "ETH": _closes(120.0)},
        current_weights=ALLOCATION_TARGETS[REGIME_NORMAL],
        portfolio_value=1000.0,
    )
    assert out is None
    assert not sm.RECOMMENDATIONS_FILE.exists()


def test_s1_cycle_enabled_emits_recommendation_only(monkeypatch, tmp_path):
    import config

    mgr, sm = _mgr(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SPOT_S1", {"enabled": True}, raising=False)
    cur = _current_near(ALLOCATION_TARGETS[REGIME_NORMAL], "BTC", 0.10)
    out = mgr.run_s1_cycle(
        {"BTC": _closes(110.0), "ETH": _closes(120.0)},
        current_weights=cur,
        portfolio_value=1000.0,
    )
    assert out["action"] == "REBALANCE"
    assert out["regime"] == REGIME_NORMAL
    # advisory only: recommendation jsonl written, no exchange interaction possible
    lines = sm.RECOMMENDATIONS_FILE.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["strategy"] == "SPOT_S1_DEFENSIVE"
    assert rec["executed"] is False


# ── W4: hysteresis regime state persistence (round-trip) ───────────────
def test_s1_cycle_persists_and_reuses_regime_state(monkeypatch, tmp_path):
    import config

    mgr, sm = _mgr(monkeypatch, tmp_path)
    state_file = tmp_path / "s1_regime_state.json"
    monkeypatch.setattr(sm, "S1_REGIME_STATE_FILE", state_file)
    monkeypatch.setattr(config, "SPOT_S1", {"enabled": True}, raising=False)

    # call 1: decisively above the band -> NORMAL, states persisted
    out = mgr.run_s1_cycle(
        {"BTC": _closes(110.0), "ETH": _closes(120.0)},
        current_weights=ALLOCATION_TARGETS[REGIME_NORMAL],
        portfolio_value=1000.0,
    )
    assert out["regime"] == REGIME_NORMAL
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["regime"] == REGIME_NORMAL
    assert state["version"] == 2
    assert state["states"]["BTC"]["state"] == "above"
    assert state["states"]["ETH"]["state"] == "above"
    assert "ts" in state

    # call 2: flat closes sit ON the EMA (inside the zero-width band);
    # the persisted "above" states are retained -> still NORMAL
    flat = [100.0] * 250
    out2 = mgr.run_s1_cycle(
        {"BTC": flat, "ETH": flat},
        current_weights=ALLOCATION_TARGETS[REGIME_NORMAL],
        portfolio_value=1000.0,
    )
    assert out2["regime"] == REGIME_NORMAL


def test_s1_cycle_cold_start_missing_or_corrupt_state(monkeypatch, tmp_path):
    import config

    mgr, sm = _mgr(monkeypatch, tmp_path)
    state_file = tmp_path / "s1_regime_state.json"
    monkeypatch.setattr(sm, "S1_REGIME_STATE_FILE", state_file)
    monkeypatch.setattr(config, "SPOT_S1", {"enabled": True}, raising=False)

    flat = [100.0] * 250  # on the EMA: cold start falls back to stateless sign
    out = mgr.run_s1_cycle(
        {"BTC": flat, "ETH": flat},
        current_weights=ALLOCATION_TARGETS[REGIME_DEFENSIVE],
        portfolio_value=1000.0,
    )
    assert out["regime"] == REGIME_DEFENSIVE

    state_file.write_text("{not json", encoding="utf-8")  # corrupt -> stateless
    out2 = mgr.run_s1_cycle(
        {"BTC": flat, "ETH": flat},
        current_weights=ALLOCATION_TARGETS[REGIME_DEFENSIVE],
        portfolio_value=1000.0,
    )
    assert out2["regime"] == REGIME_DEFENSIVE
    # a valid state file was re-persisted after the corrupt read
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["states"]["BTC"]["state"] == "below"
    assert state["states"]["ETH"]["state"] == "below"
