"""Small cross-platform process lock helper for long-running scripts."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TextIO

from loguru import logger


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
    except OSError as e:
        # Opening the lock FILE failed (permissions, disk full, AV handle) — an
        # infra fault, NOT another process holding the lock. Log LOUDLY so it is
        # not silently conflated with the normal "already running" case below
        # (which returns None quietly). Fail-closed is deliberate here: with no
        # handle we cannot hold a lock, and a silent second writer is worse.
        logger.warning(
            f"[process_lock] cannot open lock file for '{name}': {e} "
            f"— unable to acquire lock (treating as unavailable)")
        return None

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Genuine "lock held by another live process" — the normal singleton
        # path. Quiet (debug): duplicates exiting is the expected outcome.
        handle.close()
        logger.debug(f"[process_lock] '{name}' already held by another process")
        return None
    except Exception as e:
        # The platform lock primitive itself is unavailable/erroring (msvcrt or
        # fcntl missing, or a non-OSError surface). DELIBERATE fail-open: keep
        # market-data collection alive rather than failing closed on a lock
        # mechanism quirk — but LOG LOUDLY (a silent unlocked handle here is
        # undiagnosable and can mask duplicate writers).
        logger.warning(
            f"[process_lock] lock primitive unavailable for '{name}': {e} "
            f"— proceeding WITHOUT an enforced lock (fail-open)")
        return handle

    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired_at={time.time():.3f}\n")
        handle.flush()
    except OSError:
        pass
    return handle
