from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validate_candidate_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_candidate_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_evidence():
    return {
        "candidate_id": "carry-v1",
        "market_type": "futures",
        "universe": {
            "point_in_time_master": True,
            "includes_delisted": True,
            "rank_features": [
                "return_pct",
                "atr_z",
                "quote_volume_usd",
                "spread_bps",
                "depth_usd",
            ],
        },
        "data": {
            "event_time_observed": True,
            "received_time_recorded": True,
            "mark_index_funding": True,
            "side_specific_book": True,
            "closed_bars_only": True,
        },
        "replay": {
            "causal": True,
            "next_event_fills": True,
            "chronological_portfolio": True,
            "funding_basis_liquidation": True,
            "partials_rejects_latency": True,
            "purged_walk_forward": True,
            "untouched_holdout": True,
            "venue_holdout": True,
            "parameter_plateau": True,
            "cost_stress_multipliers": [1, 1.5, 2],
        },
        "metrics": {
            "independent_trades": 500,
            "profit_factor": 1.25,
            "bootstrap_expectancy_lower": 0.001,
            "positive_folds": 3,
            "total_folds": 4,
            "pbo": 0.20,
            "dsr": 0.96,
            "dominant_symbol_pnl_fraction": 0.20,
            "stressed_expectancy_positive": True,
        },
        "shadow": {
            "matured": True,
            "days": 45,
            "independent_resolved": 150,
            "mismatch_count": 0,
        },
    }


def test_complete_evidence_is_manual_review_only():
    result = MODULE.evaluate_evidence(valid_evidence())

    assert result["decision"] == "ELIGIBLE_FOR_MANUAL_CONTROLLED_LIVE_REVIEW"
    assert result["failures"] == []
    assert "never authorizes live trading" in result["warnings"][0]


def test_good_research_with_immature_shadow_is_shadow_only():
    evidence = valid_evidence()
    evidence["shadow"]["matured"] = False
    evidence["shadow"]["days"] = 5

    result = MODULE.evaluate_evidence(evidence)

    assert result["decision"] == "ELIGIBLE_FOR_SHADOW"
    assert result["failures"] == []
    assert any("shadow.days" in warning for warning in result["warnings"])


def test_spot_or_leaky_evidence_fails_closed():
    evidence = valid_evidence()
    evidence["market_type"] = "spot"
    evidence["replay"]["next_event_fills"] = False
    evidence["metrics"]["profit_factor"] = 1.19

    result = MODULE.evaluate_evidence(evidence)

    assert result["decision"] == "RESEARCH_ONLY"
    assert any("genuine futures" in failure for failure in result["failures"])
    assert any("next_event_fills" in failure for failure in result["failures"])
    assert any("profit_factor" in failure for failure in result["failures"])


def test_missing_and_nonfinite_metrics_never_pass():
    evidence = copy.deepcopy(valid_evidence())
    del evidence["metrics"]["pbo"]
    evidence["metrics"]["dsr"] = float("nan")

    result = MODULE.evaluate_evidence(evidence)

    assert result["decision"] == "RESEARCH_ONLY"
    assert any("metrics.pbo" in failure for failure in result["failures"])
    assert any("metrics.dsr" in failure for failure in result["failures"])


def test_numeric_strings_fractional_counts_and_impossible_folds_fail_closed():
    evidence = valid_evidence()
    evidence["metrics"]["independent_trades"] = "500"
    evidence["shadow"]["independent_resolved"] = 100.5
    evidence["metrics"]["positive_folds"] = 5
    evidence["metrics"]["total_folds"] = 4

    result = MODULE.evaluate_evidence(evidence)

    assert result["decision"] == "RESEARCH_ONLY"
    assert any("independent_trades" in failure for failure in result["failures"])
    assert any("independent_resolved" in failure for failure in result["failures"])
    assert any("positive_folds must be <=" in failure for failure in result["failures"])


def test_probability_metrics_enforce_lower_and_upper_bounds():
    evidence = valid_evidence()
    evidence["metrics"]["pbo"] = -0.01
    evidence["metrics"]["dsr"] = 1.01
    evidence["metrics"]["dominant_symbol_pnl_fraction"] = -0.01

    result = MODULE.evaluate_evidence(evidence)

    assert result["decision"] == "RESEARCH_ONLY"
    assert any("metrics.pbo" in failure for failure in result["failures"])
    assert any("metrics.dsr" in failure for failure in result["failures"])
    assert any("dominant_symbol_pnl_fraction" in failure for failure in result["failures"])


def test_wrong_collection_types_and_non_object_input_fail_closed():
    evidence = valid_evidence()
    evidence["universe"]["rank_features"] = {"return_pct": True}
    evidence["replay"]["cost_stress_multipliers"] = "1.0,1.5,2.0"

    result = MODULE.evaluate_evidence(evidence)
    non_object = MODULE.evaluate_evidence([evidence])

    assert result["decision"] == "RESEARCH_ONLY"
    assert any("array of strings" in failure for failure in result["failures"])
    assert any("array of finite numbers" in failure for failure in result["failures"])
    assert non_object["decision"] == "RESEARCH_ONLY"
    assert non_object["failures"] == ["candidate evidence must be a JSON object"]


def test_evaluation_is_deterministic_and_does_not_mutate_input():
    evidence = valid_evidence()
    original = copy.deepcopy(evidence)

    first = MODULE.evaluate_evidence(evidence)
    second = MODULE.evaluate_evidence(evidence)

    assert evidence == original
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_cli_is_stdout_only_and_does_not_modify_evidence(tmp_path):
    input_path = tmp_path / "candidate.json"
    input_path.write_text(json.dumps(valid_evidence()), encoding="utf-8")
    original = input_path.read_bytes()
    original_names = sorted(path.name for path in tmp_path.iterdir())

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(input_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["decision"] == (
        "ELIGIBLE_FOR_MANUAL_CONTROLLED_LIVE_REVIEW"
    )
    assert completed.stderr == ""
    assert input_path.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == original_names


def test_cli_malformed_or_nonstandard_json_fails_closed_without_traceback(tmp_path):
    for name, payload in (("malformed.json", "{"), ("nan.json", '{"metric": NaN}')):
        input_path = tmp_path / name
        input_path.write_text(payload, encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_path)],
            check=False,
            capture_output=True,
            text=True,
        )

        result = json.loads(completed.stdout)
        assert completed.returncode == 2
        assert completed.stderr == ""
        assert result["decision"] == "RESEARCH_ONLY"
        assert result["failures"] == ["input must be valid standards-compliant JSON"]
