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
    assert min(tstops) == 6   # R6: short horizon present and first
    lo, hi = TOP_LEVEL_RANGES["scalp_time_stop_bars"]
    assert all(lo <= t <= hi for t in tstops)
    for g in grid:
        ms = g["config"]["min_score"]
        ms_lo, ms_hi = TOP_LEVEL_RANGES["min_score"]
        assert ms_lo <= ms <= ms_hi
        # unconfirmed floor never below min_score (sanitizer invariant)
        assert g["config"]["unconfirmed_min_score"] >= ms


def test_build_candidates_from_synthetic_replay():
    grid_size = len(mutator.mutate_machine_configs())
    rets = [0.01] * 100
    cands = mutator.build_candidates(replay_fn=lambda e: rets, min_trades=30)
    assert len(cands) == grid_size
    for c in cands:
        assert isinstance(c, Candidate)
        assert c.n_configs_tested == grid_size   # R2 honest trial count
        assert list(c.replay_trades) == rets
        assert not hasattr(c, "status")          # mutator never assigns status


def test_build_candidates_drops_thin_evidence():
    cands = mutator.build_candidates(replay_fn=lambda e: [0.01] * 5, min_trades=30)
    assert cands == []   # < min_trades -> dropped, too little evidence


def test_build_candidates_failsoft_on_replay_error():
    def boom(_entry):
        raise RuntimeError("no ohlcv cache")

    assert mutator.build_candidates(replay_fn=boom) == []   # no candidate -> nothing promotes
