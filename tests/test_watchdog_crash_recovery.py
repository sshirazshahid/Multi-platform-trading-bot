"""Watchdog crash-recovery classification (audit CRITICAL fix, 2026-06-06).

bot_engine.run() signals a fatal main-loop crash by calling sys.exit(1). The in-process watchdog
in main.run_with_watchdog() must treat a NON-ZERO SystemExit as a CRASH (→ restart), not as a clean
intentional exit. Previously `except SystemExit: break` swallowed the crash → process exited 0 → no
auto-restart (the 24/7-availability single point of failure).
"""
from __future__ import annotations

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
