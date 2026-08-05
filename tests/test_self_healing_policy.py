from __future__ import annotations

from pathlib import Path

import pytest

import core.self_healing_supervisor as sh
from core.self_healing_policy import (
    ALLOWED_RECOVERY_ACTIONS,
    MANUAL_CONTROL_PATHS,
    PolicyViolation,
    RecoveryAction,
    SelfHealingPolicy,
    WriteScope,
)


@pytest.fixture
def policy(tmp_path):
    return SelfHealingPolicy(sh.ROOT, runtime_root=tmp_path)


def test_recovery_action_allowlist_is_exact(policy):
    assert ALLOWED_RECOVERY_ACTIONS == {
        "restart_feed",
        "rebuild_derived_cache",
        "reconcile_positions",
        "quarantine_instrument",
        "rollback_release",
    }
    for forbidden in (
        "run_command",
        "edit_code",
        "change_risk",
        "mutate_allowlist",
        "train_model",
        "promote_model",
        "authorize_order",
        "place_order",
    ):
        assert policy.action_decision(forbidden).allowed is False


def test_only_registered_feed_recovery_can_spawn(policy, monkeypatch):
    calls = []
    monkeypatch.setenv("BINANCE_API_KEY", "must-not-reach-feed")
    monkeypatch.setenv("CONTROLLED_LIVE", "true")

    class FakeProcess:
        pass

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(sh.subprocess, "Popen", fake_popen)
    cfg = sh.config_from_mapping({"dry_run": False})

    allowed = sh._start_process("scripts/harvest_l2.py", cfg)
    assert allowed["started"] is True
    assert calls[0][0] == [sh.sys.executable, "-I", str(sh.ROOT / "scripts/harvest_l2.py")]
    assert calls[0][1]["cwd"] == sh.ROOT
    assert "BINANCE_API_KEY" not in calls[0][1]["env"]
    assert "CONTROLLED_LIVE" not in calls[0][1]["env"]

    for forbidden in (
        "main.py",
        "scripts/retrain_if_stale.py",
        "scripts/machine_strategy_replay.py",
        "strategies/dca_strategy.py",
        "../outside.py",
    ):
        result = sh._start_process(forbidden, cfg)
        assert result["started"] is False
        assert result["blocked"] == "feed_script_not_allowlisted"
    assert len(calls) == 1


def test_unregistered_allowed_classes_still_fail_closed(policy):
    sup = sh.SelfHealingSupervisor(
        {"dry_run": True, "repair_enabled": False},
        policy=policy,
    )
    for action in (
        RecoveryAction.REBUILD_DERIVED_CACHE,
        RecoveryAction.QUARANTINE_INSTRUMENT,
        RecoveryAction.ROLLBACK_RELEASE,
    ):
        result = sup.execute_recovery(action, target="BTC-USDT")
        assert result == {
            "type": "policy_block",
            "target": "BTC-USDT",
            "ok": False,
            "reason": "recovery_adapter_not_registered",
        }

    for action in ("place_order", "train_model", "edit_code", "mutate_allowlist"):
        result = sup.execute_recovery(action, target="BTC-USDT")
        assert result["ok"] is False


def test_reconcile_positions_adapter_dry_run(policy, monkeypatch):
    """RECONCILE_POSITIONS is registered: warehouse orphans only, dry_run safe."""
    calls: list = []

    def fake_reconcile(warehouse_path, **kwargs):
        calls.append({"path": warehouse_path, **kwargs})
        return {
            "ok": True,
            "reason": "reconciled",
            "closed": [{"id": 1, "dry_run": True}],
            "orphan_count": 1,
            "skipped": 0,
        }

    monkeypatch.setattr(
        "core.warehouse_reconcile.reconcile_warehouse_orphans",
        fake_reconcile,
    )
    sup = sh.SelfHealingSupervisor(
        {"dry_run": True, "repair_enabled": True, "enabled": True},
        policy=policy,
    )
    result = sup.execute_recovery(RecoveryAction.RECONCILE_POSITIONS, target="warehouse")
    assert result["ok"] is True
    assert result["type"] == "reconcile_positions"
    assert result["dry_run"] is True
    assert result["result"]["closed"][0]["id"] == 1
    assert calls and calls[0]["dry_run"] is True


def test_disabled_recovery_cannot_be_called_directly(policy, monkeypatch):
    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("disabled recovery spawned a process")

    monkeypatch.setattr(sh, "_start_process", forbidden_spawn)
    sup = sh.SelfHealingSupervisor(
        {"dry_run": False, "repair_enabled": False},
        policy=policy,
    )

    result = sup.execute_recovery(RecoveryAction.RESTART_FEED, target="l2")

    assert result["ok"] is False
    assert result["reason"] == "recovery_disabled"


def test_write_scopes_allow_only_bounded_recovery_data(policy, tmp_path):
    assert (
        policy.require_write(WriteScope.SELF_HEALING_STATE, "data/self_healing_state.json")
        == tmp_path / "data/self_healing_state.json"
    )
    assert (
        policy.require_write(WriteScope.SELF_HEALING_REPORT, "reports/self_healing/report.json")
        == tmp_path / "reports/self_healing/report.json"
    )
    assert (
        policy.require_write(
            WriteScope.DERIVED_CACHE,
            "data/ohlcv_cache/BTC-USDT_1h.parquet",
            target="ohlcv",
        )
        == tmp_path / "data/ohlcv_cache/BTC-USDT_1h.parquet"
    )
    assert (
        policy.require_write(
            WriteScope.INSTRUMENT_QUARANTINE,
            "data/instrument_quarantine.json",
        )
        == tmp_path / "data/instrument_quarantine.json"
    )


@pytest.mark.parametrize(
    "scope,path,target",
    [
        (WriteScope.SELF_HEALING_STATE, "config.py", None),
        (WriteScope.SELF_HEALING_STATE, "config/modes.py", None),
        (WriteScope.SELF_HEALING_STATE, "core/risk_manager.py", None),
        (WriteScope.SELF_HEALING_REPORT, "strategies/dca_strategy.py", None),
        (WriteScope.DERIVED_CACHE, "data/ohlcv_cache/evil.py", "ohlcv"),
        (WriteScope.DERIVED_CACHE, "data/active_strategies.json", "ohlcv"),
        (WriteScope.DERIVED_CACHE, "data/ohlcv_cache/../../data/KILL_SWITCH", "ohlcv"),
        (WriteScope.DERIVED_CACHE, "models/promoted_model.json", "ohlcv"),
        (WriteScope.INSTRUMENT_QUARANTINE, "data/risk_incident_latch.json", None),
        (WriteScope.INSTRUMENT_QUARANTINE, "data/ab_split.json", None),
        (WriteScope.INSTRUMENT_QUARANTINE, ".env", None),
    ],
)
def test_protected_code_risk_latch_and_approval_paths_are_denied(policy, scope, path, target):
    with pytest.raises(PolicyViolation):
        policy.require_write(scope, path, target=target)


@pytest.mark.parametrize("path", sorted(MANUAL_CONTROL_PATHS, key=str))
def test_every_manual_control_path_is_outside_all_write_scopes(policy, path):
    assert policy.write_decision(WriteScope.SELF_HEALING_STATE, path).allowed is False
    assert policy.write_decision(WriteScope.SELF_HEALING_REPORT, path).allowed is False
    assert policy.write_decision(WriteScope.DERIVED_CACHE, path, target="ohlcv").allowed is False
    assert policy.write_decision(WriteScope.INSTRUMENT_QUARANTINE, path).allowed is False


@pytest.mark.parametrize(
    "mapping",
    [
        {"state_path": Path("../outside/state.json")},
        {"report_dir": Path("../outside/reports")},
        {"state_path": Path("data/risk_incident_latch.json")},
        {"report_dir": Path("strategies")},
    ],
)
def test_supervisor_rejects_configured_path_escape(policy, mapping):
    with pytest.raises(PolicyViolation):
        sh.SelfHealingSupervisor(mapping, policy=policy)


def test_unknown_config_key_fails_closed():
    with pytest.raises(ValueError, match="unknown self-healing config keys"):
        sh.config_from_mapping({"repair_enabledd": False})


def test_symlinked_cache_root_is_denied(policy, tmp_path):
    real_cache = tmp_path / "real-cache"
    real_cache.mkdir()
    link = tmp_path / "data/ohlcv_cache"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(real_cache, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(PolicyViolation, match="symlinked"):
        policy.require_write(WriteScope.DERIVED_CACHE, link, target="ohlcv")


def test_hardlinked_cache_file_is_denied(policy, tmp_path):
    protected = tmp_path / "protected.json"
    protected.write_text('{"risk": "unchanged"}', encoding="utf-8")
    alias = tmp_path / "data/ohlcv_cache/alias.json"
    alias.parent.mkdir(parents=True)
    try:
        alias.hardlink_to(protected)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    with pytest.raises(PolicyViolation, match="hardlinked"):
        policy.require_write(WriteScope.DERIVED_CACHE, alias, target="ohlcv")
    assert protected.read_text(encoding="utf-8") == '{"risk": "unchanged"}'


def test_pid_matching_requires_feed_to_be_the_executed_script():
    processes = [
        {
            "ProcessId": 1,
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 2,
            "CommandLine": r"python harmless.py scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 3,
            "CommandLine": r"python scripts\harvest_skew.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 4,
            "CommandLine": r"cmd /c scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {"ProcessId": 5, "CommandLine": r"python scripts\harvest_l2.py"},
        {
            "ProcessId": 6,
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT.parent),
        },
    ]
    assert sh._pids_for_script(processes, "scripts/harvest_l2.py") == [1]
    assert sh._pids_for_script(processes, "main.py") == []
