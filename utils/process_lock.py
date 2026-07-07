"""Small cross-platform process lock helper for long-running scripts."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TextIO


def acquire_process_lock(name: str, *, root: Path | None = None) -> TextIO | None:
    """Return an open lock handle, or None when another process holds the lock.

    The caller must keep the returned handle alive for the lifetime of the
    process. Lock files live under data/runtime_locks and are advisory; they
    prevent duplicate local bot/feed processes from writing the same state files.
    """

    base = Path(root) if root is not None else Path.cwd()
    lock_dir = base / "data" / "runtime_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    lock_path = lock_dir / f"{safe_name}.lock"

    try:
        handle = lock_path.open("a+", encoding="utf-8")
        handle.seek(0)
    except OSError:
        return None

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    except Exception:
        # If the platform lock mechanism is unavailable, keep the process alive
        # rather than making market-data collection fail closed.
        return handle

    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired_at={time.time():.3f}\n")
        handle.flush()
    except OSError:
        pass
    return handle
