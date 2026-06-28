"""WS4 / R1+R2 — the gate inputs are honest: the real number of tested configs is
threaded into the Deflated-Sharpe haircut (R2), and PBO/CSCV is computed over the
per-config OOS matrix when supplied (R1). Noise must not promote through the REAL gate.
"""
from __future__ import annotations

import numpy as np

from core.decision import promotion_loop as pl
from core.promotion_gate import GateVerdict, PromotionGate


class _CapturingGate:
    def __init__(self):
        self.seen = None

    def evaluate(self, inputs):
        self.seen = inputs
        return GateVerdict.HOLD, {}


def test_n_trials_threaded_not_hardcoded():   # R2
    gate = _CapturingGate()
    cand = pl.Candidate("v", {"min_score": 0.34}, replay_trades=[0.01] * 120, n_configs_tested=9)
    pl.evaluate_candidate(cand, live_returns=[0.0] * 120, gate=gate)
    assert gate.seen.n_trials_shadow == 9   # honest trial count, not the old hardcoded 5


def test_pbo_computed_when_matrix_supplied():   # R1
    rng = np.random.default_rng(1)
    matrix = rng.normal(0.0, 0.01, size=(64, 4))   # T >= 16 partitions, K >= 2 strategies
    cand = pl.Candidate("v", {}, replay_trades=[0.01] * 120, n_configs_tested=4,
                        oos_return_matrix=matrix)
    ev = pl.evaluate_candidate(cand, live_returns=[0.0] * 120, gate=_CapturingGate())
    assert ev["pbo"] is not None
    assert 0.0 <= ev["pbo"] <= 1.0


def test_pbo_skipped_when_no_matrix():
    cand = pl.Candidate("v", {}, replay_trades=[0.01] * 120, n_configs_tested=4)
    ev = pl.evaluate_candidate(cand, live_returns=[0.0] * 120, gate=_CapturingGate())
    assert ev["pbo"] is None and ev["pbo_pass"] is True   # neutral when no grid matrix


def test_noise_does_not_promote_through_real_gate():
    rng = np.random.default_rng(2)
    cand = pl.Candidate("v", {}, replay_trades=list(rng.normal(0.0, 0.02, 200)), n_configs_tested=6)
    ev = pl.evaluate_candidate(
        cand,
        live_returns=list(rng.normal(0.0, 0.02, 200)),
        gate=PromotionGate(bootstrap_resamples=300),
    )
    assert ev["promote"] is False   # no edge -> no promotion (the correct outcome)
