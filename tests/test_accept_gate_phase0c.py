"""Phase 0c — unified after-cost accept() gate tests.

One verdict so no strategy invents its own pass/fail. These tests pin the
three acceptance bullets:
  * a wide-CI flat-expectancy result FAILS even with PF >= 1.20
  * an n=80 scalp candidate FAILS the 500 floor
  * PBO over a FULL hyperparameter grid differs from folds-as-strategies
"""
from __future__ import annotations

import numpy as np

from core.decision.promotion_loop import accept
from core.stat_tests import pbo as cscv_pbo


def _confirm_kwargs():
    """A passing 30-60d RESOLVED-PAPER confirmation window (post-0b rows)."""
    return {
        "resolved_paper_returns": [0.1] * 40,
        "confirm_days": 45,
    }


def test_returns_single_verdict_with_subgates_and_honest_n():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.5, 1.0, size=120).tolist()
    grid = rng.normal(0.0, 1.0, size=(64, 6))
    v = accept(
        returns=rets,
        strategy_class="intraday",
        fold_returns=[rng.normal(0.4, 1.0, size=40) for _ in range(3)],
        oos_return_matrix=grid,
        n_trials=5,
        trial_budget=50,
        **_confirm_kwargs(),
    )
    assert "accept" in v
    assert isinstance(v["accept"], bool)
    sg = v["sub_gates"]
    for key in (
        "n_floor",
        "profit_factor",
        "expectancy_ci",
        "folds",
        "pbo",
        "dsr",
        "multiple_comparisons",
        "confirmation_window",
    ):
        assert key in sg, key
        assert "pass" in sg[key]
    # honest N used for DSR is surfaced and accounts for the pre-registered budget.
    assert v["honest_n_dsr"] == max(5, 50)
    assert sg["dsr"]["n_trials"] == v["honest_n_dsr"]


def test_wide_ci_flat_expectancy_fails_despite_pf_above_floor():
    # 12 large wins (+50) + 96 small losses (-5): PF = 600/480 = 1.25 (>= 1.20),
    # but the mean is dominated by a handful of rare big wins, so the bootstrap
    # expectancy LOWER bound dips below zero -> expectancy gate must FAIL.
    rets = [50.0] * 12 + [-5.0] * 96
    v = accept(
        returns=rets,
        strategy_class="intraday",
        oos_return_matrix=np.random.default_rng(1).normal(0, 1, size=(64, 6)),
        n_trials=1,
        trial_budget=1,
        **_confirm_kwargs(),
    )
    assert v["sub_gates"]["profit_factor"]["value"] >= 1.20
    assert v["sub_gates"]["profit_factor"]["pass"] is True
    assert v["sub_gates"]["expectancy_ci"]["lb"] <= 0.0
    assert v["sub_gates"]["expectancy_ci"]["pass"] is False
    assert v["accept"] is False


def test_n80_scalp_fails_500_floor():
    rng = np.random.default_rng(2)
    rets = rng.normal(0.5, 1.0, size=80).tolist()
    v = accept(
        returns=rets,
        strategy_class="scalp",
        oos_return_matrix=rng.normal(0, 1, size=(64, 6)),
        n_trials=1,
        trial_budget=1,
        **_confirm_kwargs(),
    )
    assert v["sub_gates"]["n_floor"]["floor"] == 500
    assert v["sub_gates"]["n_floor"]["n"] == 80
    assert v["sub_gates"]["n_floor"]["pass"] is False
    assert v["accept"] is False


def test_pbo_full_grid_differs_from_folds_as_strategies():
    rng = np.random.default_rng(7)
    T = 64
    # Full hyperparameter grid: 8 distinct configs as columns.
    full_grid = rng.normal(0.0, 1.0, size=(T, 8))
    # Folds-as-strategies: the SAME single config sliced into 4 fold-columns
    # (the promotion_gate.py ~280-296 caveat). Distinct matrix -> distinct PBO.
    base = rng.normal(0.05, 1.0, size=T)
    folds_as_strats = np.column_stack(
        [base + rng.normal(0, 0.01, size=T) for _ in range(4)]
    )
    pbo_full = cscv_pbo(full_grid, 16)
    pbo_folds = cscv_pbo(folds_as_strats, 16)
    assert pbo_full != pbo_folds

    v = accept(
        returns=rng.normal(0.5, 1.0, size=120).tolist(),
        strategy_class="intraday",
        oos_return_matrix=full_grid,
        n_trials=1,
        trial_budget=1,
        **_confirm_kwargs(),
    )
    # accept() reports PBO computed over the FULL grid, not folds-as-strategies.
    assert v["sub_gates"]["pbo"]["value"] == pbo_full
    assert v["sub_gates"]["pbo"]["value"] != pbo_folds
