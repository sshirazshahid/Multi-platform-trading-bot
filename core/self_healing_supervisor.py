"""Bounded self-healing and strategy-adaptation supervisor.

The supervisor is intentionally conservative:

* it can restart missing/stale auxiliary feed harvesters;
* it can ask the existing retrain wrapper to repair stale model pointers;
* it can generate machine-strategy parameter candidates and backtest them;
* it can write a PAPER-only adaptive machine config consumed by MachineSignal.

It cannot bypass live gates, mutate Python strategy code, or raise leverage/size.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.adaptive_config import ADAPTIVE_MACHINE_CONFIG_PATH, sanitize_adaptive_machine_payload
from core.feed_health import FORWARD_FEEDS, read_forward_feed_status, unhealthy_forward_feeds

ROOT = Path(__file__).resolve().parents[1]
SELF_HEAL_STATE_PATH = Path("data/self_healing_state.json")
SELF_HEAL_REPORT_DIR = Path("reports/self_healing")


@dataclass(frozen=True)
class SelfHealingConfig:
    enabled: bool = True
    repair_enabled: bool = True
    restart_main: bool = False
    retrain_enabled: bool = True
    adapt_enabled: bool = True
    dry_run: bool = False
    min_interval_sec: int = 15 * 60
    repair_cooldown_sec: int = 10 * 60
    retrain_cooldown_sec: int = 12 * 60 * 60
    adapt_cooldown_sec: int = 4 * 60 * 60
    replay_timeframe: str = "15m"
    replay_max_files: int = 8
    replay_max_bars: int = 1500
    min_trades: int = 100
    min_profit_factor: float = 1.05
    min_avg_ret: float = 0.0
    min_positive_folds: int = 2
    adaptive_ttl_hours: int = 24
    python_executable: str = sys.executable
    state_path: Path = SELF_HEAL_STATE_PATH
    report_dir: Path = SELF_HEAL_REPORT_DIR
    adaptive_config_path: Path = ADAPTIVE_MACHINE_CONFIG_PATH


@dataclass
class SelfHealingState:
    last_run_at: float = 0.0
    last_repair_at: dict[str, float] = field(default_factory=dict)
    last_retrain_at: float = 0.0
    last_adapt_at: float = 0.0
    active_candidate_id: str | None = None


def config_from_mapping(raw: dict[str, Any] | None = None) -> SelfHealingConfig:
    raw = dict(raw or {})
    return SelfHealingConfig(
        enabled=bool(raw.get("enabled", True)),
        repair_enabled=bool(raw.get("repair_enabled", True)),
        restart_main=bool(raw.get("restart_main", False)),
        retrain_enabled=bool(raw.get("retrain_enabled", True)),
        adapt_enabled=bool(raw.get("adapt_enabled", True)),
        dry_run=bool(raw.get("dry_run", False)),
        min_interval_sec=int(raw.get("min_interval_sec", 15 * 60)),
        repair_cooldown_sec=int(raw.get("repair_cooldown_sec", 10 * 60)),
        retrain_cooldown_sec=int(raw.get("retrain_cooldown_sec", 12 * 60 * 60)),
        adapt_cooldown_sec=int(raw.get("adapt_cooldown_sec", 4 * 60 * 60)),
        replay_timeframe=str(raw.get("replay_timeframe", "15m")),
        replay_max_files=int(raw.get("replay_max_files", 8)),
        replay_max_bars=int(raw.get("replay_max_bars", 1500)),
        min_trades=int(raw.get("min_trades", 100)),
        min_profit_factor=float(raw.get("min_profit_factor", 1.05)),
        min_avg_ret=float(raw.get("min_avg_ret", 0.0)),
        min_positive_folds=int(raw.get("min_positive_folds", 2)),
        adaptive_ttl_hours=int(raw.get("adaptive_ttl_hours", 24)),
        python_executable=str(raw.get("python_executable") or sys.executable),
        state_path=Path(raw.get("state_path", SELF_HEAL_STATE_PATH)),
        report_dir=Path(raw.get("report_dir", SELF_HEAL_REPORT_DIR)),
        adaptive_config_path=Path(raw.get("adaptive_config_path", ADAPTIVE_MACHINE_CONFIG_PATH)),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(path: Path) -> SelfHealingState:
    if not path.exists():
        return SelfHealingState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return SelfHealingState()
    return SelfHealingState(
        last_run_at=float(payload.get("last_run_at") or 0.0),
        last_repair_at=dict(payload.get("last_repair_at") or {}),
        last_retrain_at=float(payload.get("last_retrain_at") or 0.0),
        last_adapt_at=float(payload.get("last_adapt_at") or 0.0),
        active_candidate_id=payload.get("active_candidate_id"),
    )


def _save_state(path: Path, state: SelfHealingState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, default=str), encoding="utf-8")


def _run_command(
    cmd: list[str],
    *,
    timeout_sec: int,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"cmd": cmd, "returncode": 0, "stdout": "", "dry_run": True}
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        return {
            "cmd": cmd,
            "returncode": int(proc.returncode),
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
            "dry_run": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "returncode": 124,
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": "timeout",
            "dry_run": False,
        }


def _start_process(script: str, cfg: SelfHealingConfig) -> dict[str, Any]:
    cmd = [cfg.python_executable, script]
    if cfg.dry_run:
        return {"script": script, "started": True, "dry_run": True}
    try:
        subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"script": script, "started": True, "dry_run": False}
    except Exception as exc:
        return {"script": script, "started": False, "error": str(exc), "dry_run": False}


def _powershell_process_snapshot() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    ps = (
        "$items = Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match 'python|py.exe' -and "
        "($_.CommandLine -match 'Trading_Bot' -or $_.CommandLine -match 'main.py' "
        "-or $_.CommandLine -match 'harvest_' -or $_.CommandLine -match 'run_confluence') } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine; "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return []
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return [payload]
    return payload if isinstance(payload, list) else []


def _logical_counts(processes: list[dict[str, Any]]) -> dict[str, int]:
    scripts = {
        "main": "main.py",
        "confluence_paper": "run_confluence_paper.py",
        "liquidations": "harvest_liquidations.py",
        "skew": "harvest_skew.py",
        "l2": "harvest_l2.py",
        "tv": "harvest_tv.py",
    }
    grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in scripts}
    for proc in processes:
        cl = str(proc.get("CommandLine") or "").lower().replace("/", "\\")
        for key, leaf in scripts.items():
            if leaf.lower() in cl:
                grouped[key].append(proc)
    out: dict[str, int] = {}
    for key, group in grouped.items():
        if not group:
            out[key] = 0
            continue
        ids = {int(p.get("ProcessId")) for p in group if p.get("ProcessId") is not None}
        roots = [p for p in group if int(p.get("ParentProcessId") or 0) not in ids]
        out[key] = len(roots) if roots else 1
    return out


def _summary_ok(summary: dict[str, Any], cfg: SelfHealingConfig) -> bool:
    return (
        int(summary.get("n") or 0) >= cfg.min_trades
        and float(summary.get("avg_ret") or 0.0) > cfg.min_avg_ret
        and float(summary.get("profit_factor") or 0.0) >= cfg.min_profit_factor
    )


def _positive_folds(folds: dict[str, dict[str, Any]]) -> int:
    count = 0
    for fold in (folds or {}).values():
        if int(fold.get("n") or 0) < 10:
            continue
        if float(fold.get("avg_ret") or 0.0) > 0.0 and float(fold.get("profit_factor") or 0.0) >= 1.0:
            count += 1
    return count


def _candidate_configs() -> list[dict[str, Any]]:
    return [
        {
            "id": "baseline_confirmed_rr22",
            "args": {
                "min_score": 0.34,
                "unconfirmed_min_score": 0.40,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
            },
            "config": {
                "min_score": 0.34,
                "unconfirmed_min_score": 0.40,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
            },
        },
        {
            "id": "strict_confirmed_rr22",
            "args": {
                "min_score": 0.40,
                "unconfirmed_min_score": 0.45,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
            },
            "config": {
                "min_score": 0.40,
                "unconfirmed_min_score": 0.45,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
            },
        },
        {
            "id": "ema_rsi_heavier",
            "args": {
                "min_score": 0.34,
                "unconfirmed_min_score": 0.45,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
            },
            "config": {
                "min_score": 0.34,
                "unconfirmed_min_score": 0.45,
                "rr": 2.2,
                "atr_stop_mult": 2.0,
                "weights": {
                    "valuation": 0.10,
                    "harmonic": 0.10,
                    "stochastic": 0.08,
                    "ict": 0.14,
                    "asian_range": 0.06,
                    "dow_swing": 0.08,
                    "bb_squeeze": 0.12,
                    "ema_rsi": 0.32,
                },
            },
        },
    ]


class SelfHealingSupervisor:
    def __init__(self, config: SelfHealingConfig | dict[str, Any] | None = None):
        self.config = config if isinstance(config, SelfHealingConfig) else config_from_mapping(config)
        self.state = _load_state(self.config.state_path)

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        cfg = self.config
        report: dict[str, Any] = {
            "generated_at": _utc_now().isoformat(),
            "enabled": cfg.enabled,
            "dry_run": cfg.dry_run,
            "actions": [],
        }
        if not cfg.enabled:
            report["verdict"] = "DISABLED"
            return report
        now = time.time()
        if not force and now - self.state.last_run_at < cfg.min_interval_sec:
            report["verdict"] = "SKIPPED_COOLDOWN"
            report["next_run_after_sec"] = int(cfg.min_interval_sec - (now - self.state.last_run_at))
            return report

        if not cfg.dry_run:
            self.state.last_run_at = now
        audit = _run_command([cfg.python_executable, "scripts/trading_system_audit.py"], timeout_sec=120, dry_run=cfg.dry_run)
        report["audit"] = audit
        if cfg.repair_enabled:
            report["actions"].extend(self._repair_runtime())
        if cfg.retrain_enabled:
            action = self._maybe_retrain()
            if action:
                report["actions"].append(action)
        if cfg.adapt_enabled:
            action = self._maybe_adapt_strategy()
            if action:
                report["actions"].append(action)

        report["verdict"] = "OK" if all(a.get("ok", True) for a in report["actions"]) else "ACTION_FAILED"
        if not cfg.dry_run:
            _save_state(cfg.state_path, self.state)
        self._write_report(report)
        return report

    def _repair_runtime(self) -> list[dict[str, Any]]:
        cfg = self.config
        actions: list[dict[str, Any]] = []
        now = time.time()
        processes = _powershell_process_snapshot()
        counts = _logical_counts(processes)
        script_by_feed = {spec.name: spec.script for spec in FORWARD_FEEDS}

        records = read_forward_feed_status(ROOT)
        bad_feeds = set(unhealthy_forward_feeds(records))
        for name in ("liquidations", "skew", "l2", "tv"):
            if counts.get(name, 0) < 1:
                bad_feeds.add(name)
        for name in sorted(bad_feeds):
            key = f"feed:{name}"
            if now - self.state.last_repair_at.get(key, 0.0) < cfg.repair_cooldown_sec:
                actions.append({"type": "repair_feed", "target": name, "ok": True, "skipped": "cooldown"})
                continue
            result = _start_process(script_by_feed[name], cfg)
            if not cfg.dry_run:
                self.state.last_repair_at[key] = now
            actions.append({"type": "repair_feed", "target": name, "ok": bool(result.get("started")), "result": result})

        if counts.get("confluence_paper", 0) < 1:
            key = "confluence_paper"
            if now - self.state.last_repair_at.get(key, 0.0) >= cfg.repair_cooldown_sec:
                result = _start_process("run_confluence_paper.py", cfg)
                if not cfg.dry_run:
                    self.state.last_repair_at[key] = now
                actions.append(
                    {
                        "type": "repair_auxiliary",
                        "target": "confluence_paper",
                        "ok": bool(result.get("started")),
                        "result": result,
                    }
                )
            else:
                actions.append(
                    {
                        "type": "repair_auxiliary",
                        "target": "confluence_paper",
                        "ok": True,
                        "skipped": "cooldown",
                    }
                )

        if cfg.restart_main and counts.get("main", 0) < 1:
            key = "main"
            if now - self.state.last_repair_at.get(key, 0.0) >= cfg.repair_cooldown_sec:
                result = _start_process("main.py", cfg)
                if not cfg.dry_run:
                    self.state.last_repair_at[key] = now
                actions.append({"type": "repair_main", "ok": bool(result.get("started")), "result": result})
        return actions

    def _maybe_retrain(self) -> dict[str, Any] | None:
        cfg = self.config
        now = time.time()
        if now - self.state.last_retrain_at < cfg.retrain_cooldown_sec:
            return None
        cmd = [
            cfg.python_executable,
            "scripts/retrain_if_stale.py",
            "--market",
            "both",
            "--quick",
            "--step-timeout-sec",
            "1800",
        ]
        if cfg.dry_run:
            cmd.append("--dry-run")
        # _run_command always spawns the script (dry_run=False) — when cfg.dry_run
        # the safe behaviour is delegated to the script's own --dry-run flag
        # (appended above) so its stdout still reports retrain=skipped vs needed.
        result = _run_command(cmd, timeout_sec=3600, dry_run=False)
        needs_retrain = "retrain=skipped" not in str(result.get("stdout") or "")
        # Stamp the cooldown whenever the check actually RAN the script — not only
        # when a retrain was needed. Previously a "retrain=skipped" result left
        # last_retrain_at unchanged (0.0), so the 12h cooldown never engaged and the
        # script re-spawned every supervisor tick (~15 min). Stamp unconditionally.
        self.state.last_retrain_at = now
        return {"type": "retrain_if_stale", "ok": result.get("returncode") == 0,
                "needs_retrain": needs_retrain, "result": result}

    def _run_replay_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config
        args = candidate["args"]
        out_path = cfg.report_dir / f"replay_{candidate['id']}_{_utc_now().strftime('%Y%m%d_%H%M%S')}.json"
        cmd = [
            cfg.python_executable,
            "scripts/machine_strategy_replay.py",
            "--timeframe",
            cfg.replay_timeframe,
            "--max-files",
            str(cfg.replay_max_files),
            "--max-bars",
            str(cfg.replay_max_bars),
            "--min-score-grid",
            str(args["min_score"]),
            "--rr-grid",
            str(args["rr"]),
            "--atr-stop-mult-grid",
            str(args["atr_stop_mult"]),
            "--unconfirmed-min-score",
            str(args["unconfirmed_min_score"]),
            "--output",
            str(out_path),
        ]
        result = _run_command(cmd, timeout_sec=180, dry_run=cfg.dry_run)
        payload: dict[str, Any] = {}
        if not cfg.dry_run and out_path.exists():
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        label = next(iter((payload.get("results") or {}).keys()), "")
        metrics = (payload.get("results") or {}).get(label, {})
        return {"candidate": candidate, "cmd_result": result, "path": str(out_path), "label": label, "metrics": metrics}

    def _maybe_adapt_strategy(self) -> dict[str, Any] | None:
        cfg = self.config
        now = time.time()
        if now - self.state.last_adapt_at < cfg.adapt_cooldown_sec:
            return None
        runs = [self._run_replay_candidate(c) for c in _candidate_configs()]
        best: dict[str, Any] | None = None
        for run in runs:
            summary = (run.get("metrics") or {}).get("summary") or {}
            folds = (run.get("metrics") or {}).get("folds") or {}
            if not _summary_ok(summary, cfg):
                continue
            if _positive_folds(folds) < cfg.min_positive_folds:
                continue
            if best is None:
                best = run
                continue
            best_summary = (best.get("metrics") or {}).get("summary") or {}
            if float(summary.get("avg_ret") or 0.0) > float(best_summary.get("avg_ret") or 0.0):
                best = run

        action = {"type": "adapt_strategy", "ok": True, "candidate_runs": runs, "promoted": False}
        if best is None:
            if not cfg.dry_run:
                self.state.last_adapt_at = now
            action["reason"] = "no_candidate_passed"
            return action

        candidate = best["candidate"]
        payload = {
            "enabled": True,
            "source": "self_healing_supervisor",
            "candidate_id": candidate["id"],
            "generated_at": _utc_now().isoformat(),
            "expires_at": (_utc_now() + timedelta(hours=cfg.adaptive_ttl_hours)).isoformat(),
            "apply_scope": "paper",
            "evidence": {
                "replay_path": best["path"],
                "label": best["label"],
                "summary": (best.get("metrics") or {}).get("summary") or {},
                "folds": (best.get("metrics") or {}).get("folds") or {},
            },
            "config": candidate["config"],
        }
        validated = sanitize_adaptive_machine_payload(payload)
        if not validated.applied:
            action["ok"] = False
            action["reason"] = validated.reason
            return action
        if not cfg.dry_run:
            cfg.adaptive_config_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.adaptive_config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if not cfg.dry_run:
            self.state.last_adapt_at = now
            self.state.active_candidate_id = candidate["id"]
        action.update(
            {
                "promoted": True,
                "candidate_id": candidate["id"],
                "adaptive_config_path": str(cfg.adaptive_config_path),
                "evidence": payload["evidence"],
                "dry_run": cfg.dry_run,
            }
        )
        return action

    def _write_report(self, report: dict[str, Any]) -> None:
        out = self.config.report_dir
        out.mkdir(parents=True, exist_ok=True)
        stamp = _utc_now().strftime("%Y%m%d_%H%M%S")
        path = out / f"self_healing_{stamp}.json"
        latest = out / "self_healing_latest.json"
        text = json.dumps(report, indent=2, default=str)
        path.write_text(text, encoding="utf-8")
        latest.write_text(text, encoding="utf-8")
