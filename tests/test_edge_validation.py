from __future__ import annotations

import numpy as np

from core.edge_validation import VALIDATION_SCHEMA, validate_paper_edge


def _evidence(**updates):
    rng = np.random.default_rng(17)
    returns = rng.normal(0.01, 0.004, size=240)
    # Preserve a realistic loss tail while retaining a deliberately strong
    # synthetic series for a deterministic positive-control test.
    returns[::17] = -0.004
    labels = [f"g{i % 4}" for i in range(len(returns))]
    holdout_hash = "a" * 64
    kwargs = {
        "forward_returns": returns,
        "strategy_class": "directional",
        "confirmation_days": 90,
        "fee_slippage_costs": np.full(len(returns), 0.0005),
        "pbo_value": 0.10,
        "n_trials": 4,
        "family_p_values": None,
        "candidate_family_index": 0,
        "group_labels": {
            "symbol": labels,
            "venue": labels[1:] + labels[:1],
            "regime": labels[2:] + labels[:2],
        },
        "independent_event_ids": [f"event-{i}" for i in range(len(returns))],
        "preregistration": {
            "hypothesis": "frozen hypothesis",
            "rules": {"entry": "frozen"},
            "universe": ["BTC", "ETH"],
            "parameter_trials": 4,
            "costs": {"source": "venue tier"},
            "split_dates": ["2025-01-01", "2026-01-01"],
            "holdout_hash": holdout_hash,
        },
        "holdout_hash": holdout_hash,
        "parameter_plateau_pass": True,
        "lookahead_analysis_pass": True,
        "recursive_indicator_analysis_pass": True,
        "n_resamples": 500,
        "seed": 7,
    }
    kwargs.update(updates)
    return kwargs


def test_complete_exact_evidence_can_pass_all_gates():
    kwargs = _evidence()
    first = validate_paper_edge(**kwargs)
    dsr_p = first["gates"]["deflated_sharpe"]["p"]
    kwargs["family_p_values"] = [dsr_p, 0.01, 0.02, 0.03]

    result = validate_paper_edge(**kwargs)

    assert result["validation_schema"] == VALIDATION_SCHEMA
    assert result["pass"] is True
    assert all(gate["pass"] for gate in result["gates"].values())


def test_missing_one_group_dimension_fails_closed():
    kwargs = _evidence(group_labels={"symbol": ["x"] * 240})
    first = validate_paper_edge(**kwargs)
    dsr_p = first["gates"]["deflated_sharpe"]["p"]
    kwargs["family_p_values"] = [dsr_p, 0.01, 0.02, 0.03]

    result = validate_paper_edge(**kwargs)

    concentration = result["gates"]["concentration_and_leave_one_out"]
    assert result["pass"] is False
    assert concentration["pass"] is False
    assert concentration["dimensions"]["venue"]["reason"] == (
        "labels_missing_or_misaligned"
    )


def test_fdr_input_must_contain_the_computed_candidate_p_value():
    result = validate_paper_edge(
        **_evidence(family_p_values=[0.001, 0.01, 0.02, 0.03])
    )

    assert result["gates"]["family_fdr"]["pass"] is False
    assert result["pass"] is False
