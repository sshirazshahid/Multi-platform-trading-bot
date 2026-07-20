"""Entries-only kill switch (Codex port, 2026-07-12).

``data/KILL_SWITCH`` present (any content, even empty) => the bot refuses NEW
entries — bot_engine._execute_open (mcp/band lane) and the deep-breakout PAPER
lane. Everything else KEEPS running: position monitoring, SL/TP management,
maker-resolver, reconciliation, shadow probes. Removing the file resumes
entries — no restart needed.

Reference: Codex/bot/engine.py:263-265 — but Codex returns from run_cycle
entirely, which would also freeze position management. That skip-whole-cycle
semantics is deliberately NOT ported: a halted bot with open positions still
needs its SL/TP and monitoring rails.

Logging: once per STATE CHANGE (halt / resume), never per cycle — the check
runs on every candidate entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

# Repo-wide contract: data paths are relative to the repo root cwd
# (see core/warehouse.py WAREHOUSE_PATH and the resolver's cwd anchor).
KILL_SWITCH_PATH = Path("data/KILL_SWITCH")


class KillSwitch:
    """File-flag switch with once-per-state-change logging.

    A ``path`` of None follows the module-level ``KILL_SWITCH_PATH`` at each
    check (tests monkeypatch that); an explicit path is fixed at construction.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else None
        self._last: Optional[bool] = None  # None = state not yet observed

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else KILL_SWITCH_PATH

    def entries_halted(self) -> bool:
        """True while the switch file exists. Logs only on state change."""
        try:
            present = self.path.exists()
        except OSError as exc:
            # Entry authorization cannot be proven when the control filesystem
            # is unavailable. Fail closed while monitoring/exits keep running.
            present = True
            if self._last is not True:
                logger.error(f"[KillSwitch] control-path check failed: {exc}")
        if present != self._last:
            if present:
                logger.warning(
                    "[KillSwitch] entries halted — data/KILL_SWITCH present "
                    "(monitoring/SL-TP/closes keep running; delete the file to resume)")
            elif self._last is not None:
                logger.info("[KillSwitch] entries resumed — data/KILL_SWITCH removed")
            self._last = present
        return present


# Shared default instance: bot_engine and the deep-breakout lane consult the
# same object, so a halt/resume is announced once per process, not per caller.
_default = KillSwitch()


def entries_halted() -> bool:
    """Module-level convenience over the shared default switch."""
    return _default.entries_halted()
