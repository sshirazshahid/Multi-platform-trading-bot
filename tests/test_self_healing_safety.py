"""Regressions for three SelfHealingSupervisor defects (audit 2026-07-07).

4a. Retrain (~60 min) / replay (4x3 min) subprocesses ran synchronously inside
    ``tick()`` — which the bot schedules on the shared ``schedule`` thread that
    also drives the portfolio cycle, position monitor and watchdog. A slow
    retrain stalled EVERYTHING scheduled. With ``async_heavy`` the heavy phase
    runs on a single-flight daemon thread and ``tick()`` returns immediately.

4b. Kill-then-respawn didn't wait for the wedged holder's singleton lock to
    release, so the respawn could exit instantly ('already running') — a no-op
    repair that still reported ok=True. It must poll for release and report
    honestly when the lock stays held.

4c. ``_terminate_pids`` recorded a pid as killed even when ``taskkill`` failed
    (returncode != 0). Only confirmed kills may be recorded.
"""
from __future__ import annotations

import threading
import time

import core.self_healing_supervisor as sh


def test_async_heavy_does_not_block_and_is_single_flight(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_retrain(self):
        started.set()
        release.wait(5)
        return {"type": "retrain_if_stale", "ok": True}

    monkeypatch.setattr(sh.SelfHealingSupervisor, "_maybe_retrain", slow_retrain)

    sup = sh.SelfHealingSupervisor(sh.config_from_mapping({
        "dry_run": True,
        "state_path": tmp_path / "state.json",
        "report_dir": tmp_path / "reports",
        "repair_enabled": False,
        "retrain_enabled": True,
        "adapt_enabled": False,
        "async_heavy": True,
        "min_interval_sec": 0,
    }))

    t0 = time.time()
    report = sup.tick(force=True)
    assert time.time() - t0 < 2.0, "tick blocked on the heavy subprocess"
    hb = [a for a in report["actions"] if a.get("type") == "heavy_async"]
    assert hb and hb[0]["dispatched"] is True
    assert started.wait(2.0), "heavy worker never started"

    # Single-flight: a second tick while the worker is busy does not re-dispatch.
    report2 = sup.tick(force=True)
    hb2 = [a for a in report2["actions"] if a.get("type") == "heavy_async"]
    assert hb2 and hb2[0]["dispatched"] is False

    release.set()
    if sup._heavy_thread is not None:
        sup._heavy_thread.join(5)


def test_wait_for_lock_release_distinguishes_held_from_free(tmp_path):
    from utils.process_lock import acquire_process_lock

    cfg = sh.config_from_mapping({"dry_run": False})

    held = acquire_process_lock("harvest_l2", root=tmp_path)
    assert held is not None
    # Still held by us -> the respawn would no-op, so report False (not released).
    assert sh._wait_for_lock_release(
        "harvest_l2", cfg, root=tmp_path, attempts=3, delay_sec=0.01) is False

    held.close()
    # Released -> True (respawn can now take the lock).
    assert sh._wait_for_lock_release(
        "harvest_l2", cfg, root=tmp_path, attempts=3, delay_sec=0.01) is True


def test_save_state_concurrent_writers_never_corrupt(tmp_path):
    """The real production concurrency is two writer threads (main tick +
    async_heavy worker) in one process. _save_state must serialize them so no
    write raises, the final file is valid JSON, and no stray .tmp leaks."""
    import json as _json

    path = tmp_path / "state.json"
    st = sh._load_state(path)  # fresh default state

    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(60):
                st.last_repair_at[f"feed{n}"] = float(i)
                sh._save_state(path, st)
        except Exception as exc:  # a failed write (e.g. Windows replace) surfaces
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not errors, f"concurrent writers corrupted the state file: {errors}"
    _json.loads(path.read_text(encoding="utf-8"))  # final file parses
    assert not list(tmp_path.glob("*.tmp")), "atomic write left a temp file behind"


def test_terminate_pids_records_only_confirmed_kills(monkeypatch):
    cfg = sh.config_from_mapping({"dry_run": False})

    class _Res:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(args, **kw):
        pid = args[2]  # ["taskkill", "/PID", "<pid>", ...]
        return _Res(0 if pid == "111" else 1)

    monkeypatch.setattr(sh.os, "name", "nt")
    monkeypatch.setattr(sh.subprocess, "run", fake_run)

    killed = sh._terminate_pids([111, 999], cfg)
    assert killed == [111], "a failed taskkill must not be recorded as a kill"
