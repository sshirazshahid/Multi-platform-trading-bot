import os
import subprocess
import sys
from pathlib import Path

from utils.process_lock import acquire_process_lock


def test_process_lock_allows_only_one_holder(tmp_path):
    first = acquire_process_lock("example", root=tmp_path)
    assert first is not None

    second = acquire_process_lock("example", root=tmp_path)
    assert second is None

    first.close()

    third = acquire_process_lock("example", root=tmp_path)
    assert third is not None
    third.close()


def test_process_lock_excludes_a_separate_process(tmp_path):
    """Cross-process exclusion: a real second process cannot take a held lock.

    The same-process test above passes even if the OS lock is a no-op; only a
    separate process exercises the actual advisory-lock enforcement.
    """
    root = Path(__file__).resolve().parents[1].as_posix()
    lock_root = Path(tmp_path).as_posix()
    child = (
        f"import sys; sys.path.insert(0, r'{root}');"
        "from utils.process_lock import acquire_process_lock as a;"
        f"h = a('xproc', root=r'{lock_root}');"
        "print('GOT' if h is not None else 'NONE')"
    )

    held = acquire_process_lock("xproc", root=tmp_path)
    assert held is not None
    try:
        out = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=30)
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        assert lines and lines[-1].strip() == "NONE", (out.stdout, out.stderr)
    finally:
        held.close()

    out2 = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, text=True, timeout=30)
    lines2 = [ln for ln in out2.stdout.splitlines() if ln.strip()]
    assert lines2 and lines2[-1].strip() == "GOT", (out2.stdout, out2.stderr)


def _raise_non_oserror(*args, **kwargs):
    raise ValueError("lock primitive unavailable")


def test_process_lock_fail_open_logs_loudly(tmp_path, monkeypatch):
    """A non-OSError from the platform lock primitive is a DELIBERATE fail-open,
    but it must be LOUD (pre-fix it returned an unlocked handle silently)."""
    from loguru import logger

    messages: list[str] = []
    sink = logger.add(messages.append, level="WARNING")
    try:
        if os.name == "nt":
            import msvcrt

            monkeypatch.setattr(msvcrt, "locking", _raise_non_oserror)
        else:
            import fcntl

            monkeypatch.setattr(fcntl, "flock", _raise_non_oserror)
        handle = acquire_process_lock("failopen", root=tmp_path)
        assert handle is not None  # fail-open keeps market-data collection alive
        handle.close()
    finally:
        logger.remove(sink)

    assert any("process_lock" in str(m).lower() for m in messages), messages
