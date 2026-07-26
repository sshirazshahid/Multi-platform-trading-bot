"""Failure-mode regressions for bounded self-healing."""

from __future__ import annotations

import json
import sys
import threading
from types import SimpleNamespace

import core.self_healing_supervisor as sh
from core.self_healing_policy import SelfHealingPolicy


def _policy(tmp_path):
    return SelfHealingPolicy(sh.ROOT, runtime_root=tmp_path)


def _all_feed_processes():
    return [
        {
            "ProcessId": index,
            "ParentProcessId": 0,
            "CommandLine": f"python scripts/harvest_{name}.py",
            "WorkingDirectory": str(sh.ROOT),
        }
        for index, name in enumerate(("l2", "liquidations", "skew", "tv"), start=1)
    ]


def test_process_snapshot_failure_never_spawns_duplicate_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sh,
        "_process_snapshot",
        lambda: sh.ProcessSnapshot(False, error="snapshot_returncode:1"),
    )

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("spawned a feed without a trustworthy process snapshot")

    monkeypatch.setattr(sh, "_start_process", forbidden_spawn)
    sup = sh.SelfHealingSupervisor(
        {"dry_run": True, "min_interval_sec": 0},
        policy=_policy(tmp_path),
    )

    report = sup.tick(force=True)

    assert report["verdict"] == "ACTION_FAILED"
    assert report["actions"] == [
        {
            "type": "repair_feeds",
            "ok": False,
            "reason": "snapshot_returncode:1",
        }
    ]


def test_process_inventory_error_fails_closed(monkeypatch):
    class InventoryError(Exception):
        pass

    def failed_inventory(*args, **kwargs):
        raise InventoryError("unavailable")

    fake_psutil = SimpleNamespace(process_iter=failed_inventory, Error=InventoryError)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    snapshot = sh._process_snapshot()

    assert snapshot.ok is False
    assert snapshot.error == "snapshot_error:InventoryError"


def test_state_budget_failure_prevents_any_recovery_side_effect(tmp_path, monkeypatch):
    def failed_save(*args, **kwargs):
        raise OSError("read-only state")

    def forbidden_repair(*args, **kwargs):
        raise AssertionError("recovery ran before its budget was persisted")

    monkeypatch.setattr(sh, "_save_state", failed_save)
    monkeypatch.setattr(sh, "_process_snapshot", forbidden_repair)
    sup = sh.SelfHealingSupervisor(
        {"dry_run": False, "min_interval_sec": 0},
        policy=_policy(tmp_path),
    )

    report = sup.tick(force=True)

    assert report["verdict"] == "ACTION_FAILED"
    assert report["actions"][0]["type"] == "persist_state"


def test_unconfirmed_feed_termination_prevents_respawn(tmp_path, monkeypatch):
    processes = _all_feed_processes()
    monkeypatch.setattr(
        sh,
        "_process_snapshot",
        lambda: sh.ProcessSnapshot(True, tuple(processes)),
    )
    monkeypatch.setattr(
        sh,
        "read_forward_feed_status",
        lambda root: [
            {
                "name": name,
                "exists": True,
                "connected": True,
                "fresh": name != "l2",
                "error": None,
            }
            for name in ("l2", "liquidations", "skew", "tv")
        ],
    )
    monkeypatch.setattr(sh, "_terminate_feed_processes", lambda *args: [])

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("feed respawned after an unconfirmed termination")

    monkeypatch.setattr(sh, "_start_process", forbidden_spawn)
    sup = sh.SelfHealingSupervisor(
        {"dry_run": False, "min_interval_sec": 0, "repair_cooldown_sec": 0},
        policy=_policy(tmp_path),
    )

    report = sup.tick(force=True)

    assert report["verdict"] == "ACTION_FAILED"
    failed = [action for action in report["actions"] if action.get("target") == "l2"]
    assert failed[0]["reason"] == "feed_termination_unconfirmed"


def test_wait_for_lock_release_distinguishes_held_from_free(tmp_path):
    from utils.process_lock import acquire_process_lock

    cfg = sh.config_from_mapping({"dry_run": False})

    held = acquire_process_lock("harvest_l2", root=tmp_path)
    assert held is not None
    assert (
        sh._wait_for_lock_release("harvest_l2", cfg, root=tmp_path, attempts=3, delay_sec=0.01)
        is False
    )

    held.close()
    assert (
        sh._wait_for_lock_release("harvest_l2", cfg, root=tmp_path, attempts=3, delay_sec=0.01)
        is True
    )


def test_save_state_concurrent_writers_never_corrupt(tmp_path):
    policy = _policy(tmp_path)
    path = tmp_path / "data/self_healing_state.json"
    state = sh.SelfHealingState()
    errors: list[Exception] = []

    def writer(number: int) -> None:
        try:
            for index in range(60):
                state.last_repair_at["feed:l2"] = float(number * 100 + index)
                sh._save_state(path, state, policy)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(number,)) for number in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not errors
    json.loads(path.read_text(encoding="utf-8"))
    assert not list(path.parent.glob("*.tmp"))


def test_terminate_feed_records_only_confirmed_allowlisted_kills(monkeypatch):
    cfg = sh.config_from_mapping({"dry_run": False})
    checked = []

    def fake_kill(record, feed):
        checked.append((record["ProcessId"], feed))
        return record["ProcessId"] == 111

    processes = [
        {
            "ProcessId": 111,
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 999,
            "CommandLine": r"python scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
        {
            "ProcessId": 555,
            "CommandLine": r"python main.py scripts\harvest_l2.py",
            "WorkingDirectory": str(sh.ROOT),
        },
    ]
    monkeypatch.setattr(sh, "_kill_validated_feed_root", fake_kill)

    killed = sh._terminate_feed_processes(processes, "l2", cfg)

    assert killed == [111]
    assert checked == [(111, "l2"), (999, "l2")]


def test_corrupt_state_blocks_recovery_and_is_not_overwritten(tmp_path, monkeypatch):
    state_path = tmp_path / "data/self_healing_state.json"
    state_path.parent.mkdir(parents=True)
    original = b"{not-json"
    state_path.write_bytes(original)

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("recovery ran with untrusted cooldown state")

    monkeypatch.setattr(sh, "_start_process", forbidden_spawn)
    sup = sh.SelfHealingSupervisor(
        {"dry_run": False, "min_interval_sec": 0},
        policy=_policy(tmp_path),
    )

    report = sup.tick(force=True)

    assert report["verdict"] == "BLOCKED_STATE"
    assert report["state_error"].startswith("state_unreadable")
    assert state_path.read_bytes() == original


def test_manual_incident_latches_and_allowlists_are_byte_preserved(tmp_path, monkeypatch):
    protected = {
        "data/KILL_SWITCH": b"operator halt\n",
        "data/active_strategies.json": b'{"approved": ["baseline"]}\n',
        "data/risk_incident_latch.json": b'{"reason": "manual review"}\n',
    }
    for relative, content in protected.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    monkeypatch.setattr(
        sh,
        "_process_snapshot",
        lambda: sh.ProcessSnapshot(True, tuple(_all_feed_processes())),
    )
    monkeypatch.setattr(
        sh,
        "read_forward_feed_status",
        lambda root: [
            {"name": name, "exists": True, "connected": True, "fresh": True, "error": None}
            for name in ("l2", "liquidations", "skew", "tv")
        ],
    )
    sup = sh.SelfHealingSupervisor(
        {"dry_run": False, "min_interval_sec": 0},
        policy=_policy(tmp_path),
    )

    report = sup.tick(force=True)

    assert report["verdict"] == "OK"
    for relative, content in protected.items():
        assert (tmp_path / relative).read_bytes() == content
