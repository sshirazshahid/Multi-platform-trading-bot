"""WS4 — the mutator builds short-lookback-weighted, sanitizer-valid candidates and
never promotes anything itself (promotion_loop owns status). Replay is injectable so
the test needs no OHLCV cache.
"""

from __future__ import annotations

from core.adaptive_config import TOP_LEVEL_RANGES
from core.decision.promotion_loop import Candidate
from research import mutator


def test_grid_is_short_lookback_weighted_and_bounded():
    grid = mutator.mutate_machine_configs()
    tstops = [g["config"]["scalp_time_stop_bars"] for g in grid]
    assert min(tstops) == 6  # R6: short horizon present and first
    lo, hi = TOP_LEVEL_RANGES["scalp_time_stop_bars"]
    assert all(lo <= t <= hi for t in tstops)
    for g in grid:
        ms = g["config"]["min_score"]
        ms_lo, ms_hi = TOP_LEVEL_RANGES["min_score"]
        assert ms_lo <= ms <= ms_hi
        # unconfirmed floor never below min_score (sanitizer invariant)
        assert g["config"]["unconfirmed_min_score"] >= ms


def _evidence(rets, *, eligible=True, independent=True):
    reasons = [] if eligible else ["eligible_symbols 2 < 8"]
    return mutator.ReplayEvidence(
        selection_trades=tuple([0.001] * 40),
        validation_trades=tuple(rets),
        independent_validation=independent,
        metadata={
            "eligible": eligible,
            "market_type": "futures",
            "protocol": "chronological_holdout_v1",
            "reasons": reasons,
        },
    )


def test_build_candidates_uses_only_preregistered_variant_validation_slice():
    grid_size = len(mutator.mutate_machine_configs())
    selected = mutator.mutate_machine_configs()[2]["id"]
    rets = [0.01] * 100
    cands = mutator.build_candidates(
        replay_fn=lambda entry: _evidence(rets),
        min_trades=30,
        selected_variant_id=selected,
    )
    assert len(cands) == 1
    candidate = cands[0]
    assert isinstance(candidate, Candidate)
    assert candidate.variant_id == selected
    assert candidate.n_configs_tested == grid_size  # R2 honest trial count
    assert list(candidate.replay_trades) == rets
    assert candidate.evidence["candidate_returns_role"] == "untouched_validation_only"
    assert not hasattr(candidate, "status")  # mutator never assigns status


def test_build_candidates_blocks_post_hoc_grid_selection():
    assert mutator.build_candidates(replay_fn=lambda entry: _evidence([0.01] * 100)) == []
    report = mutator.last_build_report()
    assert report["status"] == "BLOCKED"
    assert "preregistered" in report["reasons"][0]


def test_build_candidates_rejects_legacy_same_sample_list():
    selected = mutator.mutate_machine_configs()[0]["id"]
    cands = mutator.build_candidates(
        replay_fn=lambda entry: [0.01] * 100,
        selected_variant_id=selected,
    )
    assert cands == []
    assert "reused-sample" in mutator.last_build_report()["reasons"][0]


def test_build_candidates_drops_thin_evidence():
    selected = mutator.mutate_machine_configs()[0]["id"]
    cands = mutator.build_candidates(
        replay_fn=lambda entry: _evidence([0.01] * 5),
        min_trades=30,
        selected_variant_id=selected,
    )
    assert cands == []  # < min_trades -> dropped, too little evidence
    assert "validation_trades 5 < 30" in mutator.last_build_report()["reasons"]


def test_build_candidates_reports_insufficient_futures_breadth():
    selected = mutator.mutate_machine_configs()[0]["id"]
    cands = mutator.build_candidates(
        replay_fn=lambda entry: _evidence([0.01] * 100, eligible=False),
        selected_variant_id=selected,
    )
    assert cands == []
    report = mutator.last_build_report()
    assert "eligible_symbols 2 < 8" in report["reasons"]
    assert report["replay"]["market_type"] == "futures"


def test_build_candidates_failsoft_on_replay_error():
    def boom(_entry):
        raise RuntimeError("no ohlcv cache")

    selected = mutator.mutate_machine_configs()[0]["id"]
    assert (
        mutator.build_candidates(replay_fn=boom, selected_variant_id=selected) == []
    )  # no candidate -> nothing promotes
    assert "no ohlcv cache" in " ".join(mutator.last_build_report()["reasons"])
