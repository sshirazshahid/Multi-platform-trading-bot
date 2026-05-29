"""Phase 6.2: promotion-gate tests.

Verdicts: PROMOTE / HOLD / ESCALATE_TO_OPERATOR / REJECT.

Gate (all four required for PROMOTE):
  1. Shadow Sharpe LB(95%) > Live Sharpe UB
  2. Shadow rolling 30-day WR >= 0.65
  3. Shadow PBO < 0.5
  4. Shadow Deflated Sharpe p < 0.05

Escape clause (ESCALATE) when shadow has been gate-eligible >= 90 days,
the LB-vs-UB gate is false, but the shadow point-Sharpe still beats live
and WR floor is met.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "promotion_gate_test_copy", ROOT / "core" / "promotion_gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["promotion_gate_test_copy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pg():
    return _load()


# ── Synthetic-return helpers ─────────────────────────────────────────────

def _strong_returns(rng: np.random.Generator, n: int = 400) -> np.ndarray:
    """Returns with high mean, low vol — strong, persistent edge.

    Sharpe ~2.0 — high enough that the Deflated-Sharpe selection-bias
    penalty still leaves DSR p < 0.05 with n_trials=1 and n_obs=400.
    """
    return rng.normal(loc=0.02, scale=0.01, size=n)


def _weak_returns(rng: np.random.Generator, n: int = 400) -> np.ndarray:
    """Returns with mean near zero — no edge."""
    return rng.normal(loc=0.0001, scale=0.01, size=n)


def _losing_returns(rng: np.random.Generator, n: int = 400) -> np.ndarray:
    """Returns with negative mean — clear bad."""
    return rng.normal(loc=-0.01, scale=0.01, size=n)


# ── Tests ───────────────────────────────────────────────────────────────

def test_all_four_pass_promotes(pg):
    """All four gate conditions pass → PROMOTE."""
    rng = np.random.default_rng(7)
    shadow = _strong_returns(rng, n=400)
    live = _losing_returns(rng, n=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.70,
        days_eligible=120,
        n_trials_shadow=1,
    )
    gate = pg.PromotionGate()
    verdict, diag = gate.evaluate(inputs)
    assert verdict == pg.GateVerdict.PROMOTE, (verdict, diag)
    assert diag["all_four_pass"] is True
    assert diag["wr_floor_pass"] is True
    assert diag["pbo_pass"] is True
    assert diag["dsr_pass"] is True
    assert diag["sharpe_lb_beats_live_ub"] is True


def test_wr_below_floor_blocks_promotion(pg):
    """All else passes but WR=0.62 < 0.65 → not PROMOTE."""
    rng = np.random.default_rng(7)
    shadow = _strong_returns(rng, n=400)
    live = _losing_returns(rng, n=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.62,
        days_eligible=120,
        n_trials_shadow=1,
    )
    verdict, diag = pg.PromotionGate().evaluate(inputs)
    assert verdict != pg.GateVerdict.PROMOTE
    assert diag["wr_floor_pass"] is False


def test_dsr_above_threshold_blocks_promotion(pg):
    """DSR p large (no skill detected) → not PROMOTE."""
    rng = np.random.default_rng(11)
    shadow = _weak_returns(rng, n=400)
    live = _losing_returns(rng, n=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.70,
        days_eligible=120,
        # Inflate trial count so DSR becomes large p? Actually we need p < 0.05
        # to PASS. Weak returns naturally give DSR ~0.5, which is NOT < 0.05.
        n_trials_shadow=1,
    )
    verdict, diag = pg.PromotionGate().evaluate(inputs)
    assert verdict != pg.GateVerdict.PROMOTE
    assert diag["dsr_pass"] is False


def test_escape_clause_triggers_escalate(pg):
    """LB-vs-UB straddles, but >=90 days + point > point + WR>=0.65 → ESCALATE."""
    rng = np.random.default_rng(3)
    # Both shadow and live are mildly positive — overlapping CIs.
    shadow = rng.normal(loc=0.0010, scale=0.012, size=400)
    live = rng.normal(loc=0.0006, scale=0.012, size=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.66,
        days_eligible=95,
        n_trials_shadow=1,
    )
    verdict, diag = pg.PromotionGate().evaluate(inputs)
    # Confirm we set up the right scenario: LB-vs-UB false, point-shadow > point-live.
    assert diag["sharpe_lb_beats_live_ub"] is False
    assert diag["shadow_sharpe_point"] > diag["live_sharpe_point"]
    assert diag["wr_floor_pass"] is True
    assert diag["escape_eligible"] is True
    assert verdict == pg.GateVerdict.ESCALATE, (verdict, diag)


def test_escape_clause_not_triggered_when_too_few_days(pg):
    """Same scenario as escape, but days_eligible < 90 → HOLD (not ESCALATE)."""
    rng = np.random.default_rng(3)
    shadow = rng.normal(loc=0.0010, scale=0.012, size=400)
    live = rng.normal(loc=0.0006, scale=0.012, size=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.66,
        days_eligible=30,
        n_trials_shadow=1,
    )
    verdict, diag = pg.PromotionGate().evaluate(inputs)
    assert verdict != pg.GateVerdict.ESCALATE
    assert diag["escape_eligible"] is False
    assert verdict == pg.GateVerdict.HOLD


def test_clear_reject_when_shadow_worse_than_live(pg):
    """Shadow strictly worse than live at every reasonable percentile → REJECT."""
    rng = np.random.default_rng(13)
    shadow = _losing_returns(rng, n=400)
    live = _strong_returns(rng, n=400)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.40,
        days_eligible=120,
        n_trials_shadow=1,
    )
    verdict, diag = pg.PromotionGate().evaluate(inputs)
    assert verdict == pg.GateVerdict.REJECT
    assert diag["sharpe_lb_beats_live_ub"] is False
    assert diag["shadow_sharpe_point"] < diag["live_sharpe_point"]


def test_apply_split_writes_json(pg, tmp_path):
    p = tmp_path / "ab_split.json"
    pg.apply_split(0.10, path=p)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["percent_to_shadow"] == pytest.approx(0.10)


def test_apply_split_clamps(pg, tmp_path):
    p_hi = tmp_path / "hi.json"
    pg.apply_split(1.5, path=p_hi)
    assert json.loads(p_hi.read_text())["percent_to_shadow"] == pytest.approx(1.0)

    p_lo = tmp_path / "lo.json"
    pg.apply_split(-0.1, path=p_lo)
    assert json.loads(p_lo.read_text())["percent_to_shadow"] == pytest.approx(0.0)


def test_diagnostics_dict_has_all_documented_keys(pg):
    """Callers depend on the diagnostics shape — keep it stable."""
    rng = np.random.default_rng(1)
    shadow = _weak_returns(rng, n=200)
    live = _weak_returns(rng, n=200)
    inputs = pg.GateInputs(
        shadow_returns=shadow,
        live_returns=live,
        shadow_wr_30d=0.50,
        days_eligible=10,
        n_trials_shadow=1,
    )
    _verdict, diag = pg.PromotionGate().evaluate(inputs)
    expected = {
        "shadow_sharpe_lb",
        "shadow_sharpe_ub",
        "shadow_sharpe_point",
        "live_sharpe_lb",
        "live_sharpe_ub",
        "live_sharpe_point",
        "shadow_wr_30d",
        "days_eligible",
        "pbo",
        "deflated_sharpe_p",
        "sharpe_lb_beats_live_ub",
        "wr_floor_pass",
        "pbo_pass",
        "dsr_pass",
        "all_four_pass",
        "escape_eligible",
        "reasons",
    }
    missing = expected - set(diag.keys())
    assert not missing, f"diagnostics missing keys: {missing}"
    assert isinstance(diag["reasons"], list)
