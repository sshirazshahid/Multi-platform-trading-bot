"""Fail-closed capability and path policy for autonomous recovery.

The policy is a ceiling, not a generic executor.  A recovery class being listed
here does not make arbitrary commands or callbacks executable; the supervisor
must still register a concrete, bounded implementation for that class.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class PolicyViolation(ValueError):
    """Raised when a recovery request crosses an action or path boundary."""


class RecoveryAction(str, Enum):
    RESTART_FEED = "restart_feed"
    REBUILD_DERIVED_CACHE = "rebuild_derived_cache"
    RECONCILE_POSITIONS = "reconcile_positions"
    QUARANTINE_INSTRUMENT = "quarantine_instrument"
    ROLLBACK_RELEASE = "rollback_release"


class WriteScope(str, Enum):
    SELF_HEALING_STATE = "self_healing_state"
    SELF_HEALING_REPORT = "self_healing_report"
    DERIVED_CACHE = "derived_cache"
    INSTRUMENT_QUARANTINE = "instrument_quarantine"


ALLOWED_RECOVERY_ACTIONS = frozenset(action.value for action in RecoveryAction)

# This is deliberately independent of feed-health metadata.  Adding a feed to
# another module cannot silently grant this supervisor process-launch authority.
FEED_SCRIPTS = MappingProxyType(
    {
        "l2": Path("scripts/harvest_l2.py"),
        "liquidations": Path("scripts/harvest_liquidations.py"),
        "skew": Path("scripts/harvest_skew.py"),
        "tv": Path("scripts/harvest_tv.py"),
    }
)

DERIVED_CACHE_ROOTS = MappingProxyType(
    {
        "funding": Path("data/funding_cache"),
        "ohlcv": Path("data/ohlcv_cache"),
        "tv": Path("data/tv_cache"),
    }
)

_CACHE_DATA_SUFFIXES = frozenset(
    {".arrow", ".csv", ".db", ".feather", ".json", ".jsonl", ".parquet", ".sqlite"}
)

# These paths are named explicitly for auditability.  The allow-by-scope rules
# below already deny them, including when reached through ``..`` or a symlink.
MANUAL_CONTROL_PATHS = frozenset(
    {
        Path(".env"),
        Path("config.py"),  # legacy monolith path
        Path("config"),  # package root (De-Emotion D1)
        Path("config/__init__.py"),
        Path("data/KILL_SWITCH"),
        Path("data/ab_split.json"),
        Path("data/active_strategies.json"),
        Path("data/adaptive_machine_config.json"),
        Path("data/auto_mutations.json"),
        Path("data/carry_positions.json"),
        Path("data/review_required.json"),
        Path("data/risk_incident_latch.json"),
        Path("data/risk_state.json"),
        Path("data/self_heal_state.json"),
        Path("data/selfmod_disabled"),
        Path("data/selfmod_kill"),
        Path("data/selfmod_state.json"),
    }
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    path: Path | None = None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _clean_script_token(value: str) -> str:
    token = str(value).strip().strip('"').strip("'")
    return token.replace("\\", os.sep).replace("/", os.sep)


def _resolve_write_path(path: Path) -> Path:
    """Resolve parent links without racing an atomic replacement of the file."""
    if path.is_symlink():
        return path.resolve(strict=False)
    return path.parent.resolve(strict=False) / path.name


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


@dataclass(frozen=True)
class SelfHealingPolicy:
    """Trusted policy roots plus deterministic authorization helpers.

    ``runtime_root`` exists only to isolate tests and deployments that keep
    runtime state on a separate volume.  It is constructor-injected code, not a
    setting accepted by :func:`config_from_mapping`.
    """

    workspace_root: Path
    runtime_root: Path | None = None

    def __post_init__(self) -> None:
        workspace = Path(self.workspace_root).resolve(strict=True)
        runtime = Path(self.runtime_root or workspace).resolve(strict=True)
        if not workspace.is_dir() or not runtime.is_dir():
            raise PolicyViolation("policy roots must be existing directories")
        object.__setattr__(self, "workspace_root", workspace)
        object.__setattr__(self, "runtime_root", runtime)

    def action_decision(self, action: RecoveryAction | str) -> PolicyDecision:
        try:
            normalized = (
                action if isinstance(action, RecoveryAction) else RecoveryAction(str(action))
            )
        except ValueError:
            return PolicyDecision(False, "action_not_allowlisted")
        return PolicyDecision(True, f"allowlisted:{normalized.value}")

    def feed_decision(self, feed: str) -> PolicyDecision:
        name = str(feed or "").strip().lower()
        relative = FEED_SCRIPTS.get(name)
        if relative is None:
            return PolicyDecision(False, "feed_not_allowlisted")
        try:
            script = (self.workspace_root / relative).resolve(strict=True)
        except (OSError, RuntimeError):
            return PolicyDecision(False, "feed_script_unavailable")
        if not _is_within(script, self.workspace_root) or not script.is_file():
            return PolicyDecision(False, "feed_script_outside_workspace")
        expected = self.workspace_root.joinpath(*relative.parts)
        if script != expected:
            return PolicyDecision(False, "feed_script_path_mismatch")
        return PolicyDecision(True, "feed_allowlisted", script)

    def feed_for_script(self, script: str | Path) -> str | None:
        raw = _clean_script_token(str(script))
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        for feed in FEED_SCRIPTS:
            decision = self.feed_decision(feed)
            if decision.allowed and resolved == decision.path:
                return feed
        return None

    def command_line_runs_feed(
        self,
        command_line: str,
        feed: str,
        *,
        working_directory: str | Path | None = None,
    ) -> bool:
        """Return True only when Python's executable script is the exact feed."""
        decision = self.feed_decision(feed)
        if not decision.allowed or decision.path is None:
            return False
        try:
            import shlex

            tokens = shlex.split(str(command_line or ""), posix=os.name != "nt")
        except ValueError:
            return False
        if len(tokens) < 2:
            return False
        executable = Path(_clean_script_token(tokens[0])).name.lower()
        if not (
            executable in {"py", "py.exe", "python", "python.exe"}
            or executable.startswith("python3")
        ):
            return False

        index = 1
        while index < len(tokens) and str(tokens[index]).startswith("-"):
            option = str(tokens[index])
            if option in {"-W", "-X"}:
                index += 2
            else:
                index += 1
        if index >= len(tokens):
            return False
        raw = _clean_script_token(tokens[index])
        if not raw.lower().endswith(".py"):
            return False
        candidate = Path(raw)
        if not candidate.is_absolute():
            if working_directory is None:
                return False
            try:
                process_cwd = Path(working_directory).resolve(strict=True)
            except (OSError, RuntimeError):
                return False
            if process_cwd != self.workspace_root:
                return False
            candidate = process_cwd / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        return resolved == decision.path

    def require_write(
        self,
        scope: WriteScope | str,
        path: str | Path,
        *,
        target: str | None = None,
    ) -> Path:
        decision = self.write_decision(scope, path, target=target)
        if not decision.allowed or decision.path is None:
            raise PolicyViolation(decision.reason)
        return decision.path

    def write_decision(
        self,
        scope: WriteScope | str,
        path: str | Path,
        *,
        target: str | None = None,
    ) -> PolicyDecision:
        try:
            normalized_scope = scope if isinstance(scope, WriteScope) else WriteScope(str(scope))
        except ValueError:
            return PolicyDecision(False, "write_scope_not_allowlisted")

        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.runtime_root / candidate
        lexical = _lexical_absolute(candidate)
        try:
            resolved = _resolve_write_path(candidate)
        except (OSError, RuntimeError):
            return PolicyDecision(False, "write_path_unresolvable")
        if resolved != lexical:
            return PolicyDecision(False, "write_path_symlinked", resolved)
        try:
            metadata = lexical.lstat()
        except FileNotFoundError:
            metadata = None
        except OSError:
            return PolicyDecision(False, "write_path_unreadable", resolved)
        if (
            metadata is not None
            and stat.S_ISREG(metadata.st_mode)
            and getattr(metadata, "st_nlink", 1) > 1
        ):
            return PolicyDecision(False, "write_path_hardlinked", resolved)
        if not _is_within(resolved, self.runtime_root):
            return PolicyDecision(False, "write_path_outside_runtime_root", resolved)

        if normalized_scope is WriteScope.SELF_HEALING_STATE:
            expected = _lexical_absolute(self.runtime_root / "data/self_healing_state.json")
            allowed = _is_within(expected, self.runtime_root) and resolved == expected
            return PolicyDecision(
                allowed, "state_path_allowed" if allowed else "state_path_denied", resolved
            )

        if normalized_scope is WriteScope.SELF_HEALING_REPORT:
            report_root = _lexical_absolute(self.runtime_root / "reports/self_healing")
            if not _is_within(report_root, self.runtime_root):
                return PolicyDecision(False, "report_root_outside_runtime_root", resolved)
            is_root = resolved == report_root
            is_direct_json = resolved.parent == report_root and resolved.suffix.lower() == ".json"
            allowed = is_root or is_direct_json
            return PolicyDecision(
                allowed, "report_path_allowed" if allowed else "report_path_denied", resolved
            )

        if normalized_scope is WriteScope.DERIVED_CACHE:
            relative = DERIVED_CACHE_ROOTS.get(str(target or "").strip().lower())
            if relative is None:
                return PolicyDecision(False, "cache_target_not_allowlisted", resolved)
            cache_root = _lexical_absolute(self.runtime_root / relative)
            try:
                resolved_cache_root = _resolve_write_path(cache_root)
            except (OSError, RuntimeError):
                return PolicyDecision(False, "cache_root_unresolvable", resolved)
            if resolved_cache_root != cache_root:
                return PolicyDecision(False, "cache_root_symlinked", resolved)
            if not _is_within(cache_root, self.runtime_root) or not _is_within(
                resolved, cache_root
            ):
                return PolicyDecision(False, "cache_path_denied", resolved)
            if resolved != cache_root and resolved.suffix.lower() not in _CACHE_DATA_SUFFIXES:
                return PolicyDecision(False, "cache_file_type_denied", resolved)
            return PolicyDecision(True, "cache_path_allowed", resolved)

        if normalized_scope is WriteScope.INSTRUMENT_QUARANTINE:
            expected = _lexical_absolute(self.runtime_root / "data/instrument_quarantine.json")
            allowed = _is_within(expected, self.runtime_root) and resolved == expected
            reason = "quarantine_path_allowed" if allowed else "quarantine_path_denied"
            return PolicyDecision(allowed, reason, resolved)

        return PolicyDecision(False, "write_scope_unimplemented", resolved)

    def describe(self) -> dict[str, Any]:
        return {
            "actions": sorted(ALLOWED_RECOVERY_ACTIONS),
            "registered_actions": [RecoveryAction.RESTART_FEED.value],
            "feed_targets": sorted(FEED_SCRIPTS),
            "cache_targets": sorted(DERIVED_CACHE_ROOTS),
        }


__all__ = [
    "ALLOWED_RECOVERY_ACTIONS",
    "DERIVED_CACHE_ROOTS",
    "FEED_SCRIPTS",
    "MANUAL_CONTROL_PATHS",
    "PolicyDecision",
    "PolicyViolation",
    "RecoveryAction",
    "SelfHealingPolicy",
    "WriteScope",
]
