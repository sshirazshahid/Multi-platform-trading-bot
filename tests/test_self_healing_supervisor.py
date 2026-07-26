from __future__ import annotations

from core.self_healing_policy import SelfHealingPolicy


def _policy(sh, tmp_path):
    return SelfHealingPolicy(sh.ROOT, runtime_root=tmp_path)


def _healthy_records(sh):
    from core.feed_health import FORWARD_FEEDS

    return [
        {
            "name": spec.name,
            "exists": True,
            "connected": True,
            "fresh": True,
            "error": None,
        }
        for spec in FORWARD_FEEDS
    ]


def test_self_healing_repairs_missing_feed_process(tmp_path, monkeypatch):
    import core.self_healing_supervisor as sh

    started = []
    monkeypatch.setattr(sh, "read_forward_feed_status", lambda root: _healthy_records(sh))
    monkeypatch.setattr(sh, "_process_snapshot", lambda: sh.ProcessSnapshot(True))
    monkeypatch.setattr(
        sh,
        "_start_process",
        lambda script, cfg: (
            started.append(str(script)) or {"script": str(script), "started": True, "dry_run": True}
        ),
    )

    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping(
            {
                "dry_run": True,
                "repair_enabled": True,
                "retrain_enabled": False,
                "adapt_enabled": False,
                "min_interval_sec": 0,
            }
        ),
        policy=_policy(sh, tmp_path),
    )
    report = sup.tick(force=True)

    assert report["verdict"] == "OK"
    assert len(started) == 4
    assert all("harvest_" in script for script in started)
    assert {action["type"] for action in report["actions"]} == {"restart_feed"}


def test_legacy_training_promotion_and_main_restart_are_neutralized(tmp_path, monkeypatch):
    import core.self_healing_supervisor as sh

    def forbidden_call(*args, **kwargs):
        raise AssertionError("legacy privileged execution was reached")

    monkeypatch.setattr(sh.subprocess, "run", forbidden_call)
    monkeypatch.setattr(sh.subprocess, "Popen", forbidden_call)

    adaptive_path = tmp_path / "adaptive_machine_config.json"
    sup = sh.SelfHealingSupervisor(
        sh.config_from_mapping(
            {
                "repair_enabled": False,
                "restart_main": True,
                "retrain_enabled": True,
                "adapt_enabled": True,
                "adaptive_config_path": adaptive_path,
                "python_executable": "cmd.exe",
                "min_interval_sec": 0,
            }
        ),
        policy=_policy(sh, tmp_path),
    )
    report = sup.tick(force=True)

    assert report["verdict"] == "POLICY_BLOCKED"
    assert set(report["policy_blocks"]) == {
        "override_recovery_executable",
        "promote_or_mutate_strategy",
        "restart_trading_engine",
        "train_model",
        "write_strategy_parameters",
    }
    assert not adaptive_path.exists()
    assert (tmp_path / "data/self_healing_state.json").exists()
