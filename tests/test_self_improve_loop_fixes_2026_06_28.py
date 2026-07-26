"""Regression tests for the 2026-06-28 self-improvement-loop audit fixes.

(2) Model retraining no longer belongs to self-healing; legacy requests must be
    blocked rather than respawned on any cadence.
(4) The promotion gate's Deflated Sharpe omitted sr_var (defaulted to 1.0), which
    inflated E[max SR] under the null and made the gate reject even strong signals.
"""
from __future__ import annotations

import core.self_healing_supervisor as sh
from core.self_healing_policy import SelfHealingPolicy
from core.stat_tests import deflated_sharpe


def test_legacy_retrain_request_never_spawns(tmp_path, monkeypatch):
    def forbidden_call(*args, **kwargs):
        raise AssertionError("self-healing attempted model training")

    monkeypatch.setattr(sh.subprocess, "run", forbidden_call)
    monkeypatch.setattr(sh.subprocess, "Popen", forbidden_call)
    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping({
            "dry_run": False,
            "repair_enabled": False,
            "retrain_enabled": True,
            "adapt_enabled": False,
            "min_interval_sec": 0,
        }),
        policy=SelfHealingPolicy(sh.ROOT, runtime_root=tmp_path),
    )

    first = sup.tick(force=True)
    second = sup.tick(force=True)

    assert first["verdict"] == second["verdict"] == "POLICY_BLOCKED"
    assert first["policy_blocks"] == ["train_model"]


def test_deflated_sharpe_sr_var_stops_over_rejection():
    args = dict(sr_observed=0.15, n_trials=10, n_obs=200, skew=0.0, kurt=3.0)
    placeholder = deflated_sharpe(**args, sr_var=1.0)        # the old buggy default
    corrected = deflated_sharpe(**args, sr_var=1.0 / 200)    # the fix (~1/n_obs)
    # The fix must RAISE Pr[true SR > 0] for a genuine signal, not lower it.
    assert corrected > placeholder
    assert placeholder < 0.5    # old: over-rejects a real per-obs Sharpe of 0.15
    assert corrected > 0.5      # fix: flips it to more-likely-than-not skill


def test_deflated_sharpe_still_rejects_noise():
    # A ~zero Sharpe must NOT pass even with the corrected sr_var (no false-GO).
    p = deflated_sharpe(sr_observed=0.0, n_trials=10, n_obs=200,
                        skew=0.0, kurt=3.0, sr_var=1.0 / 200)
    assert p < 0.5
