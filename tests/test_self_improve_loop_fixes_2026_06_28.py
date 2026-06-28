"""Regression tests for the 2026-06-28 self-improvement-loop audit fixes.

(2) Self-healing retrain cooldown was inverted: last_retrain_at only updated when
    a retrain actually happened, so a "retrain=skipped" result left it at 0.0 and
    the 12h cooldown never engaged -> the script re-spawned every supervisor tick.
(4) The promotion gate's Deflated Sharpe omitted sr_var (defaulted to 1.0), which
    inflated E[max SR] under the null and made the gate reject even strong signals.
"""
from __future__ import annotations

import core.self_healing_supervisor as sh
from core.stat_tests import deflated_sharpe


def test_retrain_cooldown_stamps_even_when_skipped(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, timeout_sec=0, dry_run=False):
        calls.append(1)
        return {"returncode": 0, "stdout": "retrain=skipped"}

    monkeypatch.setattr(sh, "_run_command", fake_run)
    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping({
            "dry_run": False,
            "state_path": tmp_path / "state.json",
            "report_dir": tmp_path / "reports",
            "repair_enabled": False,
            "retrain_enabled": True,
            "adapt_enabled": False,
            "min_interval_sec": 0,
        })
    )
    assert sup.state.last_retrain_at == 0.0

    first = sup._maybe_retrain()
    assert first is not None
    assert first["needs_retrain"] is False          # script reported "skipped"
    assert sup.state.last_retrain_at > 0.0          # cooldown stamped anyway
    assert len(calls) == 1

    # Immediately again: cooldown is now engaged -> the script is NOT re-spawned.
    assert sup._maybe_retrain() is None
    assert len(calls) == 1


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
