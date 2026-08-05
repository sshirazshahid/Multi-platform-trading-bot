"""Bounded runtime recovery with no trading or self-modification authority.

The supervisor may restart a small, static set of market-data harvesters.  The
policy reserves additional recovery classes for future in-process adapters, but
unknown actions and unregistered implementations are denied.  It never runs
audits, training, promotion, strategy mutation, the trading engine, or an order
path.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.feed_health import read_forward_feed_status, unhealthy_forward_feeds
from core.self_healing_policy import (
    FEED_SCRIPTS,
    PolicyViolation,
    RecoveryAction,
    SelfHealingPolicy,
    WriteScope,
)

ROOT = Path(__file__).resolve().parents[1]
SELF_HEAL_STATE_PATH = Path("data/self_healing_state.json")
SELF_HEAL_REPORT_DIR = Path("reports/self_healing")

_DEFAULT_POLICY = SelfHealingPolicy(ROOT)
_ACTIVE_CONFIG_KEYS = frozenset(
    {
        "dry_run",
        "enabled",
        "min_interval_sec",
        "repair_cooldown_sec",
        "repair_enabled",
        "report_dir",
        "state_path",
    }
)
_FORBIDDEN_FLAG_CAPABILITIES = {
    "adapt_enabled": "promote_or_mutate_strategy",
    "restart_main": "restart_trading_engine",
    "retrain_enabled": "train_model",
}
_INERT_LEGACY_KEYS = frozenset(
    {
        "adapt_cooldown_sec",
        "adaptive_ttl_hours",
        "async_heavy",
        "min_avg_ret",
        "min_positive_folds",
        "min_profit_factor",
        "min_trades",
        "replay_max_bars",
        "replay_max_files",
        "replay_timeframe",
        "retrain_cooldown_sec",
    }
)


class SelfHealingStateError(RuntimeError):
    """The persisted cooldown state cannot be trusted."""


@dataclass(frozen=True)
class SelfHealingConfig:
    enabled: bool = True
    repair_enabled: bool = True
    dry_run: bool = False
    min_interval_sec: int = 15 * 60
    repair_cooldown_sec: int = 10 * 60
    state_path: Path = SELF_HEAL_STATE_PATH
    report_dir: Path = SELF_HEAL_REPORT_DIR
    policy_blocks: tuple[str, ...] = ()


@dataclass
class SelfHealingState:
    last_run_at: float = 0.0
    last_repair_at: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessSnapshot:
    ok: bool
    processes: tuple[dict[str, Any], ...] = ()
    error: str | None = None


def _strict_bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a bool")


def _non_negative_int(value: Any, *, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a non-negative integer") from None
    if parsed < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return parsed


def config_from_mapping(raw: dict[str, Any] | None = None) -> SelfHealingConfig:
    """Parse active settings and neutralize all legacy privileged settings."""
    values = dict(raw or {})
    policy_blocks: set[str] = set()

    for key, capability in _FORBIDDEN_FLAG_CAPABILITIES.items():
        if key in values and _strict_bool(values[key], key=key):
            policy_blocks.add(capability)

    if "adaptive_config_path" in values:
        policy_blocks.add("write_strategy_parameters")
    if "python_executable" in values:
        requested = Path(str(values["python_executable"])).resolve(strict=False)
        current = Path(sys.executable).resolve(strict=False)
        if requested != current:
            policy_blocks.add("override_recovery_executable")

    known = (
        _ACTIVE_CONFIG_KEYS
        | frozenset(_FORBIDDEN_FLAG_CAPABILITIES)
        | _INERT_LEGACY_KEYS
        | {"adaptive_config_path", "python_executable"}
    )
    unknown = sorted(values.keys() - known)
    if unknown:
        raise ValueError(f"unknown self-healing config keys: {', '.join(unknown)}")

    return SelfHealingConfig(
        enabled=_strict_bool(values.get("enabled", True), key="enabled"),
        repair_enabled=_strict_bool(values.get("repair_enabled", True), key="repair_enabled"),
        dry_run=_strict_bool(values.get("dry_run", False), key="dry_run"),
        min_interval_sec=_non_negative_int(
            values.get("min_interval_sec", 15 * 60), key="min_interval_sec"
        ),
        repair_cooldown_sec=_non_negative_int(
            values.get("repair_cooldown_sec", 10 * 60), key="repair_cooldown_sec"
        ),
        state_path=Path(values.get("state_path", SELF_HEAL_STATE_PATH)),
        report_dir=Path(values.get("report_dir", SELF_HEAL_REPORT_DIR)),
        policy_blocks=tuple(sorted(policy_blocks)),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise SelfHealingStateError(f"invalid {field_name}")
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        raise SelfHealingStateError(f"invalid {field_name}") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise SelfHealingStateError(f"invalid {field_name}")
    return parsed


def _load_state(path: Path) -> SelfHealingState:
    try:
        try:
            path.stat()
        except FileNotFoundError:
            return SelfHealingState()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfHealingStateError(f"state_unreadable:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise SelfHealingStateError("state_not_an_object")
    raw_repairs = payload.get("last_repair_at") or {}
    if not isinstance(raw_repairs, dict):
        raise SelfHealingStateError("invalid last_repair_at")
    allowed_keys = {f"feed:{name}" for name in FEED_SCRIPTS}
    repairs: dict[str, float] = {}
    for key, value in raw_repairs.items():
        normalized = str(key)
        if normalized in allowed_keys:
            repairs[normalized] = _timestamp(value, field_name=f"last_repair_at.{normalized}")
    return SelfHealingState(
        last_run_at=_timestamp(payload.get("last_run_at"), field_name="last_run_at"),
        last_repair_at=repairs,
    )


_WRITE_LOCK = threading.Lock()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            for _ in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    time.sleep(0.02)
            raise OSError(f"atomic replace failed for {path}")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass


def _save_state(path: Path, state: SelfHealingState, policy: SelfHealingPolicy) -> None:
    bounded = policy.require_write(WriteScope.SELF_HEALING_STATE, path)
    _atomic_write_text(bounded, json.dumps(asdict(state), indent=2, sort_keys=True))


def _feed_environment() -> dict[str, str]:
    """Minimal environment for keyless feed scripts; trading secrets stay out."""
    keep = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _start_process(script: str | Path, cfg: SelfHealingConfig) -> dict[str, Any]:
    """Start one exact allowlisted feed script; all other scripts are denied."""
    feed = _DEFAULT_POLICY.feed_for_script(script)
    if feed is None:
        return {
            "script": str(script),
            "started": False,
            "blocked": "feed_script_not_allowlisted",
            "dry_run": cfg.dry_run,
        }
    decision = _DEFAULT_POLICY.feed_decision(feed)
    if not decision.allowed or decision.path is None:
        return {
            "script": str(script),
            "started": False,
            "blocked": decision.reason,
            "dry_run": cfg.dry_run,
        }
    if cfg.dry_run:
        return {
            "feed": feed,
            "script": str(decision.path),
            "started": True,
            "dry_run": True,
        }
    try:
        subprocess.Popen(
            [sys.executable, "-I", str(decision.path)],
            cwd=_DEFAULT_POLICY.workspace_root,
            env=_feed_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {
            "feed": feed,
            "script": str(decision.path),
            "started": True,
            "dry_run": False,
        }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {
            "feed": feed,
            "script": str(decision.path),
            "started": False,
            "error": f"{type(exc).__name__}:{exc}",
            "dry_run": False,
        }


def _pid(proc: dict[str, Any]) -> int | None:
    try:
        parsed = int(proc.get("ProcessId"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _pids_for_feed(processes: list[dict[str, Any]], feed: str) -> list[int]:
    if not _DEFAULT_POLICY.feed_decision(feed).allowed:
        return []
    out: list[int] = []
    for proc in processes:
        pid = _pid(proc)
        command_line = str(proc.get("CommandLine") or "")
        if pid is not None and _DEFAULT_POLICY.command_line_runs_feed(
            command_line,
            feed,
            working_directory=proc.get("WorkingDirectory"),
        ):
            out.append(pid)
    return out


def _root_pids_for_feed(processes: list[dict[str, Any]], feed: str) -> list[int]:
    allowed_pids = set(_pids_for_feed(processes, feed))
    matching = [
        proc for proc in processes if (pid := _pid(proc)) is not None and pid in allowed_pids
    ]
    ids = {pid for proc in matching if (pid := _pid(proc)) is not None}
    roots: list[int] = []
    for proc in matching:
        pid = _pid(proc)
        try:
            parent = int(proc.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            parent = 0
        if pid is not None and parent not in ids:
            roots.append(pid)
    return roots


def _pids_for_script(processes: list[dict[str, Any]], script: str) -> list[int]:
    """Compatibility helper with an allowlisted-script boundary."""
    feed = _DEFAULT_POLICY.feed_for_script(script)
    return _pids_for_feed(processes, feed) if feed is not None else []


def _command_line(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    import shlex

    return shlex.join(argv)


def _kill_validated_feed_root(record: dict[str, Any], feed: str) -> bool:
    """Revalidate a snapshotted process, then terminate its process tree."""
    try:
        import psutil
    except ImportError:
        return False

    pid = _pid(record)
    try:
        expected_created = float(record.get("CreateTime"))
    except (TypeError, ValueError):
        return False
    if pid is None or not math.isfinite(expected_created):
        return False
    try:
        process = psutil.Process(pid)
        if abs(float(process.create_time()) - expected_created) > 0.001:
            return False
        argv = [str(part) for part in process.cmdline()]
        working_directory = process.cwd()
        if not _DEFAULT_POLICY.command_line_runs_feed(
            _command_line(argv),
            feed,
            working_directory=working_directory,
        ):
            return False
        children = process.children(recursive=True)
        process.kill()
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
        _, alive = psutil.wait_procs([process, *children], timeout=5)
        return not alive
    except (OSError, ValueError, psutil.Error):
        return False


def _terminate_feed_processes(
    processes: list[dict[str, Any]],
    feed: str,
    cfg: SelfHealingConfig,
) -> list[int]:
    """Terminate only PIDs proven to run the exact allowlisted feed script."""
    killed: list[int] = []
    roots = set(_root_pids_for_feed(processes, feed))
    records = [proc for proc in processes if _pid(proc) in roots]
    for record in records:
        pid = _pid(record)
        if pid is None:
            continue
        if cfg.dry_run:
            continue
        if _kill_validated_feed_root(record, feed):
            killed.append(pid)
    return killed


def _wait_for_lock_release(
    lock_name: str,
    cfg: SelfHealingConfig,
    *,
    root: Path = ROOT,
    attempts: int = 15,
    delay_sec: float = 0.2,
) -> bool:
    if cfg.dry_run:
        return True
    from utils.process_lock import acquire_process_lock

    for _ in range(max(1, attempts)):
        handle = acquire_process_lock(lock_name, root=root)
        if handle is not None:
            handle.close()
            return True
        time.sleep(delay_sec)
    return False


def _process_snapshot() -> ProcessSnapshot:
    """Read exact feed identities without invoking a shell or external command."""
    try:
        import psutil
    except ImportError:
        return ProcessSnapshot(False, error="process_identity_dependency_unavailable")

    feed_basenames = {path.name.lower() for path in FEED_SCRIPTS.values()}
    identified: list[dict[str, Any]] = []
    try:
        iterator = psutil.process_iter(
            attrs=["pid", "ppid", "name", "exe", "cmdline", "create_time"],
            ad_value=None,
        )
        for process in iterator:
            info = process.info
            name = str(info.get("name") or "").lower()
            if not (name in {"py", "py.exe", "python", "python.exe"} or name.startswith("python")):
                continue
            raw_argv = info.get("cmdline")
            if not isinstance(raw_argv, list) or not raw_argv:
                return ProcessSnapshot(
                    False,
                    error=f"process_identity_unavailable:{info.get('pid')}:cmdline",
                )
            argv = [str(part) for part in raw_argv]
            token_names = {
                Path(part.strip().strip('"').strip("'").replace("\\", os.sep)).name.lower()
                for part in argv
            }
            if not token_names.intersection(feed_basenames):
                continue
            try:
                working_directory = process.cwd()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (OSError, psutil.AccessDenied) as exc:
                return ProcessSnapshot(
                    False,
                    error=(f"process_identity_unavailable:{info.get('pid')}:{type(exc).__name__}"),
                )
            command_line = _command_line(argv)
            if not any(
                _DEFAULT_POLICY.command_line_runs_feed(
                    command_line,
                    feed,
                    working_directory=working_directory,
                )
                for feed in FEED_SCRIPTS
            ):
                continue
            created = info.get("create_time")
            if created is None:
                return ProcessSnapshot(
                    False,
                    error=f"process_identity_unavailable:{info.get('pid')}:create_time",
                )
            identified.append(
                {
                    "ProcessId": info.get("pid"),
                    "ParentProcessId": info.get("ppid"),
                    "ExecutablePath": info.get("exe"),
                    "CommandLine": command_line,
                    "WorkingDirectory": working_directory,
                    "CreateTime": created,
                }
            )
    except (OSError, ValueError, psutil.Error) as exc:
        return ProcessSnapshot(False, error=f"snapshot_error:{type(exc).__name__}")
    return ProcessSnapshot(True, tuple(identified))


def _coerce_snapshot(value: ProcessSnapshot | list[dict[str, Any]]) -> ProcessSnapshot:
    if isinstance(value, ProcessSnapshot):
        return value
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return ProcessSnapshot(True, tuple(value))
    return ProcessSnapshot(False, error="snapshot_invalid_shape")


def _logical_counts(processes: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for feed in FEED_SCRIPTS:
        group = [
            proc
            for proc in processes
            if _DEFAULT_POLICY.command_line_runs_feed(
                str(proc.get("CommandLine") or ""),
                feed,
                working_directory=proc.get("WorkingDirectory"),
            )
        ]
        if not group:
            out[feed] = 0
            continue
        ids = {pid for proc in group if (pid := _pid(proc)) is not None}
        roots = []
        for proc in group:
            try:
                parent = int(proc.get("ParentProcessId") or 0)
            except (TypeError, ValueError):
                parent = 0
            if parent not in ids:
                roots.append(proc)
        out[feed] = len(roots) if roots else 1
    return out


class SelfHealingSupervisor:
    def __init__(
        self,
        config: SelfHealingConfig | dict[str, Any] | None = None,
        *,
        policy: SelfHealingPolicy | None = None,
    ):
        self.config = (
            config if isinstance(config, SelfHealingConfig) else config_from_mapping(config)
        )
        self.policy = policy or _DEFAULT_POLICY
        if self.policy.workspace_root != _DEFAULT_POLICY.workspace_root:
            raise PolicyViolation("supervisor workspace root is immutable")
        self.state_path = self.policy.require_write(
            WriteScope.SELF_HEALING_STATE, self.config.state_path
        )
        self.report_dir = self.policy.require_write(
            WriteScope.SELF_HEALING_REPORT, self.config.report_dir
        )
        self._state_error: str | None = None
        try:
            self.state = _load_state(self.state_path)
        except SelfHealingStateError as exc:
            self.state = SelfHealingState()
            self._state_error = str(exc)

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        cfg = self.config
        report: dict[str, Any] = {
            "generated_at": _utc_now().isoformat(),
            "enabled": cfg.enabled,
            "dry_run": cfg.dry_run,
            "policy": self.policy.describe(),
            "policy_blocks": list(cfg.policy_blocks),
            "actions": [],
        }
        if not cfg.enabled:
            report["verdict"] = "DISABLED"
            return report
        if self._state_error:
            report["verdict"] = "BLOCKED_STATE"
            report["state_error"] = self._state_error
            self._write_report(report)
            return report

        now = time.time()
        if not force and now - self.state.last_run_at < cfg.min_interval_sec:
            report["verdict"] = "SKIPPED_COOLDOWN"
            report["next_run_after_sec"] = int(
                cfg.min_interval_sec - (now - self.state.last_run_at)
            )
            return report

        if not cfg.dry_run:
            self.state.last_run_at = now
            try:
                _save_state(self.state_path, self.state, self.policy)
            except (OSError, PolicyViolation) as exc:
                report["actions"].append(
                    {
                        "type": "persist_state",
                        "ok": False,
                        "reason": f"{type(exc).__name__}:{exc}",
                    }
                )
                report["verdict"] = "ACTION_FAILED"
                self._write_report(report)
                return report
        if cfg.repair_enabled:
            report["actions"].extend(self._repair_runtime())

        action_failed = any(action.get("ok") is False for action in report["actions"])
        if action_failed:
            report["verdict"] = "ACTION_FAILED"
        elif cfg.policy_blocks:
            report["verdict"] = "POLICY_BLOCKED"
        else:
            report["verdict"] = "OK"
        self._write_report(report)
        return report

    def execute_recovery(self, action: RecoveryAction | str, *, target: str) -> dict[str, Any]:
        """Execute one registered recovery capability; deny all generic actions."""
        decision = self.policy.action_decision(action)
        if not decision.allowed:
            return {
                "type": "policy_block",
                "target": str(target),
                "ok": False,
                "reason": decision.reason,
            }
        try:
            normalized = (
                action if isinstance(action, RecoveryAction) else RecoveryAction(str(action))
            )
        except ValueError:
            return {
                "type": "policy_block",
                "target": str(target),
                "ok": False,
                "reason": "action_not_allowlisted",
            }
        if normalized is RecoveryAction.RECONCILE_POSITIONS:
            if not self.config.enabled or not self.config.repair_enabled:
                return {
                    "type": "policy_block",
                    "target": str(target),
                    "ok": False,
                    "reason": "recovery_disabled",
                }
            return self._reconcile_warehouse_orphans(target=str(target))
        if normalized is not RecoveryAction.RESTART_FEED:
            return {
                "type": "policy_block",
                "target": str(target),
                "ok": False,
                "reason": "recovery_adapter_not_registered",
            }
        if not self.config.enabled or not self.config.repair_enabled:
            return {
                "type": "policy_block",
                "target": str(target),
                "ok": False,
                "reason": "recovery_disabled",
            }
        feed = self.policy.feed_decision(target)
        if not feed.allowed or feed.path is None:
            return {
                "type": "policy_block",
                "target": str(target),
                "ok": False,
                "reason": feed.reason,
            }
        result = _start_process(str(feed.path), self.config)
        return {
            "type": RecoveryAction.RESTART_FEED.value,
            "target": str(target),
            "ok": bool(result.get("started")),
            "result": result,
        }

    def _reconcile_warehouse_orphans(self, *, target: str) -> dict[str, Any]:
        """Bounded adapter: close learning-book orphans only (no exchange orders)."""
        from core.warehouse_reconcile import reconcile_warehouse_orphans

        wh = self.policy.workspace_root / "data" / "warehouse.sqlite"
        pos = self.policy.workspace_root / "data" / "positions.json"
        dry = bool(self.config.dry_run)
        try:
            result = reconcile_warehouse_orphans(
                wh,
                positions_path=pos,
                dry_run=dry,
            )
        except Exception as exc:
            return {
                "type": RecoveryAction.RECONCILE_POSITIONS.value,
                "target": target,
                "ok": False,
                "reason": f"reconcile_failed:{type(exc).__name__}",
            }
        return {
            "type": RecoveryAction.RECONCILE_POSITIONS.value,
            "target": target,
            "ok": bool(result.get("ok")),
            "result": result,
            "dry_run": dry,
        }

    def _repair_runtime(self) -> list[dict[str, Any]]:
        cfg = self.config
        snapshot = _coerce_snapshot(_process_snapshot())
        if not snapshot.ok:
            return [
                {
                    "type": "repair_feeds",
                    "ok": False,
                    "reason": snapshot.error or "process_snapshot_unavailable",
                }
            ]

        processes = list(snapshot.processes)
        counts = _logical_counts(processes)
        try:
            records = read_forward_feed_status(self.policy.workspace_root)
            reported_bad = unhealthy_forward_feeds(records)
        except Exception as exc:
            return [
                {
                    "type": "repair_feeds",
                    "ok": False,
                    "reason": f"feed_health_unavailable:{type(exc).__name__}",
                }
            ]

        actions: list[dict[str, Any]] = []
        bad_feeds: set[str] = set()
        for name in reported_bad:
            decision = self.policy.feed_decision(name)
            if decision.allowed:
                bad_feeds.add(str(name))
            else:
                actions.append(
                    {
                        "type": "policy_block",
                        "target": str(name),
                        "ok": False,
                        "reason": decision.reason,
                    }
                )
        for name in FEED_SCRIPTS:
            if counts.get(name, 0) < 1:
                bad_feeds.add(name)

        now = time.time()
        for name in sorted(bad_feeds):
            key = f"feed:{name}"
            if now - self.state.last_repair_at.get(key, 0.0) < cfg.repair_cooldown_sec:
                actions.append(
                    {
                        "type": RecoveryAction.RESTART_FEED.value,
                        "target": name,
                        "ok": True,
                        "skipped": "cooldown",
                    }
                )
                continue

            if not cfg.dry_run:
                self.state.last_repair_at[key] = now
                try:
                    _save_state(self.state_path, self.state, self.policy)
                except (OSError, PolicyViolation) as exc:
                    actions.append(
                        {
                            "type": "persist_repair_budget",
                            "target": name,
                            "ok": False,
                            "reason": f"{type(exc).__name__}:{exc}",
                        }
                    )
                    continue

            killed: list[int] = []
            lock_released = True
            wedged = counts.get(name, 0) >= 1
            if wedged:
                holder_pids = _root_pids_for_feed(processes, name)
                if not holder_pids:
                    actions.append(
                        {
                            "type": RecoveryAction.RESTART_FEED.value,
                            "target": name,
                            "ok": False,
                            "reason": "feed_process_identity_unavailable",
                        }
                    )
                    continue
                killed = _terminate_feed_processes(processes, name, cfg)
                if not cfg.dry_run and set(killed) != set(holder_pids):
                    actions.append(
                        {
                            "type": RecoveryAction.RESTART_FEED.value,
                            "target": name,
                            "ok": False,
                            "reason": "feed_termination_unconfirmed",
                            "holder_pids": holder_pids,
                            "killed_pids": killed,
                        }
                    )
                    continue
                script = self.policy.feed_decision(name).path
                if script is None:
                    actions.append(
                        {
                            "type": "policy_block",
                            "target": name,
                            "ok": False,
                            "reason": "feed_script_unavailable",
                        }
                    )
                    continue
                lock_released = _wait_for_lock_release(
                    script.stem,
                    cfg,
                    root=self.policy.workspace_root,
                )

            action = self.execute_recovery(RecoveryAction.RESTART_FEED, target=name)
            if wedged:
                action["wedged_holder"] = True
                action["killed_pids"] = killed
                action["lock_released"] = lock_released
                action["ok"] = bool(action.get("ok")) and lock_released
            actions.append(action)
        return actions

    def _write_report(self, report: dict[str, Any]) -> None:
        report_dir = self.policy.require_write(WriteScope.SELF_HEALING_REPORT, self.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
        text = json.dumps(report, indent=2, default=str, sort_keys=True)
        path = self.policy.require_write(
            WriteScope.SELF_HEALING_REPORT,
            report_dir / f"self_healing_{stamp}.json",
        )
        latest = self.policy.require_write(
            WriteScope.SELF_HEALING_REPORT,
            report_dir / "self_healing_latest.json",
        )
        _atomic_write_text(path, text)
        _atomic_write_text(latest, text)


__all__ = [
    "ProcessSnapshot",
    "SelfHealingConfig",
    "SelfHealingState",
    "SelfHealingSupervisor",
    "config_from_mapping",
]
