"""utils/atomic_io.py — atomic file writes (same-directory tmp + os.replace).

A torn state file (process killed mid-write) sends its loader down the
"starting fresh" path on the next boot, silently losing learned state.
Writing to a temp file in the same directory and then os.replace()-ing it
over the target is atomic on both POSIX and Windows, so the target is
always either the old or the new content — never a partial write.

Shared helper extracted per TD-2 (tasks/plan_provenance_bundle_2026-06-12.md);
pattern proven in core/risk_manager._save_state (risk_manager.py:212-215).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically via same-dir tmp file + os.replace."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, target)


def atomic_write_json(path: Path, obj, **dumps_kw) -> None:
    """JSON-dump `obj` to `path` atomically. `dumps_kw` is passed to json.dumps."""
    atomic_write_text(path, json.dumps(obj, **dumps_kw))
