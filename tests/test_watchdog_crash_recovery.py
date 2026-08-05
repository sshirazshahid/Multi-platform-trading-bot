"""Watchdog crash-recovery classification (audit CRITICAL fix, 2026-06-06).

bot_engine.run() signals a fatal main-loop crash by calling sys.exit(1). The in-process watchdog
in main.run_with_watchdog() must treat a NON-ZERO SystemExit as a CRASH (→ restart), not as a clean
intentional exit. Previously `except SystemExit: break` swallowed the crash → process exited 0 → no
auto-restart (the 24/7-availability single point of failure).
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from main import _classify_exit


def test_clean_exits_do_not_restart():
    assert _classify_exit(SystemExit(0)) == "clean"
    assert _classify_exit(SystemExit(None)) == "clean"
    assert _classify_exit(SystemExit()) == "clean"
    assert _classify_exit(KeyboardInterrupt()) == "clean"


def test_fatal_crash_exits_trigger_restart():
    # The exact signal bot_engine.run() raises on a fatal main-loop error:
    assert _classify_exit(SystemExit(1)) == "crash"
    assert _classify_exit(SystemExit(2)) == "crash"
    assert _classify_exit(SystemExit("boom")) == "crash"  # non-int code => crash
    assert _classify_exit(ValueError("x")) == "crash"
    assert _classify_exit(RuntimeError()) == "crash"


def test_watchdog_crash_budget_exhaustion_is_nonzero(monkeypatch):
    import utils
    from core import bot_engine

    class AlwaysCrashes:
        runs = 0

        def run(self):
            type(self).runs += 1
            raise RuntimeError("deterministic crash")

    monkeypatch.setattr(bot_engine, "BotEngine", AlwaysCrashes)
    monkeypatch.setattr(main.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        utils,
        "TelegramNotifier",
        lambda: SimpleNamespace(error=lambda _message: True),
    )

    with pytest.raises(SystemExit) as exc:
        main.run_with_watchdog()

    assert exc.value.code == main.WATCHDOG_EXHAUSTED_EXIT_CODE
    assert AlwaysCrashes.runs == 11


def test_engine_does_not_swallow_nonzero_system_exit():
    source = bot_engine_source_for_grep()
    run_start = source.index("    def run(self):")
    shutdown_start = source.index("    def _shutdown(self):", run_start)
    run_block = source[run_start:shutdown_start]

    assert "except (KeyboardInterrupt, SystemExit)" not in run_block
    assert "except SystemExit as exc:" in run_block
    assert "if exc.code not in (None, 0):" in run_block


def test_controlled_live_refuses_start_when_singleton_lock_cannot_open(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "DRY_RUN", False)

    def _deny_lock(*args, **kwargs):
        raise OSError("lock storage unavailable")

    monkeypatch.setattr(builtins, "open", _deny_lock)
    with pytest.raises(SystemExit) as exc:
        main._acquire_single_instance_lock()
    assert exc.value.code == 1


def test_paper_mode_can_continue_when_singleton_lock_cannot_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "DRY_RUN", True)

    def _deny_lock(*args, **kwargs):
        raise OSError("lock storage unavailable")

    monkeypatch.setattr(builtins, "open", _deny_lock)
    assert main._acquire_single_instance_lock() is None
