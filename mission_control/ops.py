"""Mission Control mutating operations (soft ops + fail-closed CONTROLLED_LIVE).

Never forges checklist signatures. Never places orders. Never imports
order_manager / exchange clients.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mission_control.state import (
    AUDIT_PATH,
    CHECKLIST_PATH,
    INCIDENT_LATCH_PATH,
    KILL_SWITCH_PATH,
    LIVE_PID_PATH,
    ROOT,
    parse_env_file,
    read_json,
)

LIVE_CONFIRM_PHRASE = "ENABLE CONTROLLED LIVE"
SAFE_MODES = frozenset({"PAPER", "OBSERVATION"})
ALLOWED_TASK_PREFIX = "TradingBot"
KNOWN_TASK_NAMES = frozenset(
    {
        "TradingBot-24x7",
        "TradingBot-ShadowResolver",
        "TradingBot_PromotionFunnel",
        "TradingBot_UnlockCalendar",
        "TradingBot-F1CarryPaper",
        "TradingBot-FundingCarryHarvest",
        "TradingBot-FundingOIFetch",
        "TradingBot-DerivsHarvester",
        "TradingBot_MicrostructureHarvest",
        "TradingBot_DeribitChainSnap_AM",
        "TradingBot_DeribitChainSnap_PM",
        "TradingBot_AnnouncementHarvest",
        "TradingBot-MarketIntel",
        "TradingBot-GoalProgress",
        "TradingBot-IntelSynthesis",
        "TradingBot_DualModelLoop",
        "TradingBot-RegimeShortBias",
        "TradingBot-LiquidationHarvester",
        "TradingBot-WhaleEventHarvest",
        "TradingBot-ResearchLoopTick",
        "TradingBot Weekly Retrain",
        "TradingBot-WeeklyResearch",
        "TradingBot-ETFFlowScreen",
    }
)

DESTRUCTIVE_CONFIRMS = {
    "restart": "RESTART BOT",
    "mode": "CHANGE MODE",
    "clear-incident": "CLEAR INCIDENT",
    "controlled-live": LIVE_CONFIRM_PHRASE,
}


class OpsError(Exception):
    """Operator-facing failure (maps to HTTP 400/403/409)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(
    root: Path,
    *,
    action: str,
    operator_note: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    path = root / AUDIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _now(),
        "action": action,
        "operator_note": (operator_note or "").strip(),
        "ok": ok,
        "detail": detail or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def require_operator_note(note: str) -> str:
    cleaned = (note or "").strip()
    if not cleaned:
        raise OpsError("operator_note is required", status_code=400)
    return cleaned


def require_confirm(action_key: str, phrase: str | None) -> None:
    expected = DESTRUCTIVE_CONFIRMS.get(action_key)
    if expected is None:
        return
    if (phrase or "").strip() != expected:
        raise OpsError(
            f"confirm_phrase must be exactly {expected!r}",
            status_code=400,
        )


def _set_env(root: Path, key: str, value: str) -> None:
    """Write ``key=value`` into ``root/.env`` without printing secrets."""
    env_path = root / ".env"
    content = ""
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    import re

    pattern = re.compile(rf"^{re.escape(key)}=.*$", flags=re.M)
    line = f"{key}={value}"
    if pattern.search(content):
        content = pattern.sub(line, content)
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        content += line + "\n"
    env_path.write_text(content, encoding="utf-8")


def _venv_python(root: Path) -> Path:
    if sys.platform == "win32":
        candidate = root / "venv" / "Scripts" / "python.exe"
    else:
        candidate = root / "venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


def _run_python_script(
    root: Path,
    script_rel: str,
    *,
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    py = _venv_python(root)
    cmd = [str(py), str(root / script_rel), *(extra_args or [])]
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1500:],
    }


def set_kill_switch(root: Path, *, enabled: bool, operator_note: str) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    path = root / KILL_SWITCH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        path.write_text(f"halted_by_mission_control\n{_now()}\n{note}\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    detail = {"enabled": enabled, "path": str(KILL_SWITCH_PATH)}
    append_audit(root, action="kill_switch", operator_note=note, ok=True, detail=detail)
    return detail


def clear_incident(root: Path, *, operator_note: str) -> dict[str, Any]:
    """Archive + delete ``data/risk_incident_latch.json`` (same contract as RiskManager)."""
    note = require_operator_note(operator_note)
    latch = root / INCIDENT_LATCH_PATH
    history = root / "data" / "risk_incident_history.jsonl"
    if not latch.exists():
        raise OpsError("no incident latch present", status_code=409)
    try:
        payload = json.loads(latch.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpsError(f"cannot read incident latch: {exc}", status_code=500) from exc
    archive = {
        **(payload if isinstance(payload, dict) else {"payload": payload}),
        "cleared_at": _now(),
        "operator_note": note,
        "cleared_via": "mission_control",
    }
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(archive, sort_keys=True) + "\n")
    latch.unlink()
    detail = {"cleared": True, "archive_keys": sorted(archive.keys())}
    append_audit(root, action="clear_incident", operator_note=note, ok=True, detail=detail)
    return detail


def set_safe_mode(root: Path, *, mode: str, operator_note: str, confirm_phrase: str) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    require_confirm("mode", confirm_phrase)
    normalized = (mode or "").strip().upper()
    if normalized not in SAFE_MODES:
        raise OpsError("mode must be PAPER or OBSERVATION", status_code=400)
    _set_env(root, "OPERATING_MODE", normalized)
    _set_env(root, "CONTROLLED_LIVE_ENABLED", "false")
    _set_env(root, "DRY_RUN", "true")
    detail = {"mode": normalized}
    append_audit(root, action="set_mode", operator_note=note, ok=True, detail=detail)
    return detail


def set_paper_profile(root: Path, *, profile: str, operator_note: str) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    # Import normalize without loading trading stack side effects beyond entry_policy.
    from core.entry_policy import normalize_paper_profile

    try:
        normalized = normalize_paper_profile(profile)
    except ValueError as exc:
        raise OpsError(str(exc), status_code=400) from exc
    env = parse_env_file(root / ".env")
    mode = (env.get("OPERATING_MODE") or "PAPER").upper()
    if mode != "PAPER" and normalized != "STANDARD":
        raise OpsError(
            f"profile {normalized!r} requires PAPER operating mode (current={mode})",
            status_code=400,
        )
    _set_env(root, "PAPER_TRADING_PROFILE", normalized)
    detail = {"paper_profile": normalized}
    append_audit(root, action="set_paper_profile", operator_note=note, ok=True, detail=detail)
    return detail


def restart_bot(
    root: Path,
    *,
    operator_note: str,
    confirm_phrase: str,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    require_confirm("restart", confirm_phrase)
    if runner is not None:
        detail = runner(root)
    elif sys.platform == "win32":
        detail = _restart_via_schtask(root)
    else:
        detail = _restart_via_supervisor(root)
    append_audit(root, action="restart", operator_note=note, ok=True, detail=detail)
    return detail


def _restart_via_schtask(root: Path) -> dict[str, Any]:
    # End then Run the 24x7 supervisor task (kills inherited-env gotcha).
    end = subprocess.run(
        ["schtasks", "/End", "/TN", "TradingBot-24x7"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )
    run = subprocess.run(
        ["schtasks", "/Run", "/TN", "TradingBot-24x7"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )
    return {
        "method": "schtasks",
        "end_code": end.returncode,
        "run_code": run.returncode,
        "end_out": (end.stdout or end.stderr or "")[:500],
        "run_out": (run.stdout or run.stderr or "")[:500],
    }


def _restart_via_supervisor(root: Path) -> dict[str, Any]:
    py = _venv_python(root)
    script = root / "scripts" / "launcher_supervisor.py"
    proc = subprocess.Popen(
        [str(py), str(script), "run", "--restart"],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"method": "launcher_supervisor", "pid": proc.pid}


def run_script(
    root: Path,
    *,
    kind: str,
    operator_note: str,
) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    mapping = {
        "funnel": "scripts/promotion_funnel.py",
        "goal-report": "scripts/report_goal_progress.py",
    }
    script = mapping.get(kind)
    if not script:
        raise OpsError(f"unknown script kind: {kind}", status_code=400)
    try:
        result = _run_python_script(root, script, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise OpsError(f"script timed out: {exc}", status_code=504) from exc
    ok = result["returncode"] == 0
    append_audit(root, action=f"run_{kind}", operator_note=note, ok=ok, detail=result)
    if not ok:
        raise OpsError(
            f"{kind} exited {result['returncode']}: {result.get('stderr_tail') or result.get('stdout_tail')}",
            status_code=500,
        )
    return result


def task_allowed(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n in KNOWN_TASK_NAMES:
        return True
    return n.startswith(ALLOWED_TASK_PREFIX)


def run_scheduled_task(root: Path, *, name: str, operator_note: str) -> dict[str, Any]:
    note = require_operator_note(operator_note)
    if not task_allowed(name):
        raise OpsError(f"task not allowlisted: {name}", status_code=403)
    if sys.platform != "win32":
        raise OpsError("scheduled tasks are only available on Windows", status_code=501)
    proc = subprocess.run(
        ["schtasks", "/Run", "/TN", name],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )
    detail = {
        "name": name,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[:500],
        "stderr": (proc.stderr or "")[:500],
    }
    ok = proc.returncode == 0
    append_audit(root, action="run_task", operator_note=note, ok=ok, detail=detail)
    if not ok:
        raise OpsError(f"schtasks /Run failed for {name}", status_code=500)
    return detail


def _checklist_signed(root: Path) -> tuple[bool, str]:
    from core.live_gate import is_checklist_signed

    return is_checklist_signed(root / CHECKLIST_PATH)


def controlled_live(
    root: Path,
    *,
    action: str,
    operator_note: str,
    confirm_phrase: str | None = None,
    spawn: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Arm / disarm / start / stop CONTROLLED_LIVE — fail-closed."""
    note = require_operator_note(operator_note)
    act = (action or "").strip().lower()
    if act not in {"arm", "disarm", "start", "stop"}:
        raise OpsError("action must be arm|disarm|start|stop", status_code=400)

    if act in {"arm", "start"}:
        require_confirm("controlled-live", confirm_phrase)
        signed, message = _checklist_signed(root)
        if not signed:
            append_audit(
                root,
                action=f"controlled_live_{act}",
                operator_note=note,
                ok=False,
                detail={"reason": message},
            )
            raise OpsError(
                f"CONTROLLED_LIVE refused — checklist gate: {message}",
                status_code=403,
            )

    if act == "arm":
        _set_env(root, "OPERATING_MODE", "CONTROLLED_LIVE")
        _set_env(root, "CONTROLLED_LIVE_ENABLED", "true")
        _set_env(root, "DRY_RUN", "false")
        detail = {"armed": True, "mode": "CONTROLLED_LIVE"}
        append_audit(root, action="controlled_live_arm", operator_note=note, ok=True, detail=detail)
        return detail

    if act == "disarm":
        _set_env(root, "OPERATING_MODE", "PAPER")
        _set_env(root, "CONTROLLED_LIVE_ENABLED", "false")
        _set_env(root, "DRY_RUN", "true")
        detail = {"armed": False, "mode": "PAPER"}
        append_audit(root, action="controlled_live_disarm", operator_note=note, ok=True, detail=detail)
        return detail

    if act == "start":
        # Refuse safe supervisor path — start dedicated main.py process.
        env = parse_env_file(root / ".env")
        if (env.get("OPERATING_MODE") or "").upper() != "CONTROLLED_LIVE":
            raise OpsError("arm CONTROLLED_LIVE before start", status_code=409)
        if (env.get("CONTROLLED_LIVE_ENABLED") or "").lower() not in {"1", "true", "yes"}:
            raise OpsError("CONTROLLED_LIVE_ENABLED must be true before start", status_code=409)
        if spawn is not None:
            detail = spawn(root)
        else:
            detail = _spawn_live_main(root)
        append_audit(root, action="controlled_live_start", operator_note=note, ok=True, detail=detail)
        return detail

    # stop
    detail = _stop_live_main(root)
    append_audit(root, action="controlled_live_stop", operator_note=note, ok=True, detail=detail)
    return detail


def _spawn_live_main(root: Path) -> dict[str, Any]:
    py = _venv_python(root)
    main = root / "main.py"
    # Fresh env from OS + force live flags so inherited PAPER supervisor env cannot veto.
    child_env = dict(os.environ)
    child_env["OPERATING_MODE"] = "CONTROLLED_LIVE"
    child_env["CONTROLLED_LIVE_ENABLED"] = "true"
    child_env["DRY_RUN"] = "false"
    proc = subprocess.Popen(
        [str(py), str(main)],
        cwd=str(root),
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    payload = {
        "pid": proc.pid,
        "started_at": _now(),
        "cmd": [str(py), str(main)],
        "via": "mission_control",
    }
    path = root / LIVE_PID_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _stop_live_main(root: Path) -> dict[str, Any]:
    path = root / LIVE_PID_PATH
    meta = read_json(path, {})
    pid = meta.get("pid") if isinstance(meta, dict) else None
    stopped = False
    error = None
    if isinstance(pid, int) and pid > 0:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                stopped = True
            else:
                os.kill(pid, signal.SIGTERM)
                stopped = True
        except (OSError, ProcessLookupError) as exc:
            error = str(exc)
    if path.exists():
        path.unlink()
    return {"stopped": stopped, "pid": pid, "error": error}
