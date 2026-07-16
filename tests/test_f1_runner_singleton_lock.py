"""A4 (2026-07-16): the F1 carry runner must be a process singleton.

The 15-min scheduler demonstrably fires while a previous pass is still
running (observed LastTaskResult 267009 = SCHED_S_TASK_RUNNING on
2026-07-16). Two concurrent passes race the ONE shared state file
(load -> mutate -> os.replace is atomic per-write but the cycle is a
lost-update race), silently corrupting carry_positions.json — the promotion
evidence for F1, the only strategy that could ever authorize real capital.

Fix: main() acquires the repo's standard advisory singleton lock
(utils/process_lock.py, same idiom as the harvesters and shadow resolver)
and SKIPS the fire when another pass holds it. The next scheduled fire
picks up normally.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_f1_carry_paper.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("f1_runner_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skips_fire_when_lock_held(monkeypatch, capsys):
    mod = _load_module()
    # Another pass holds the lock -> acquire returns None -> main must skip
    # BEFORE touching the warehouse/state machinery.
    monkeypatch.setattr(
        "utils.process_lock.acquire_process_lock", lambda *a, **k: None)
    mod.main()
    out = capsys.readouterr().out.lower()
    assert "skip" in out and "running" in out


def test_lock_acquired_before_any_runner_construction():
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("def main()"):]
    i_lock = body.find("acquire_process_lock(")
    i_runner = body.find("CarryRunner(")
    assert 0 < i_lock < i_runner, (
        "main() must acquire the singleton lock before constructing any "
        "CarryRunner — otherwise concurrent passes race the shared state file")
