"""Read-only Mission Control state loaders.

Pure file readers — no BotEngine, order_manager, or exchange clients.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

HEARTBEAT_PATH = Path("data/heartbeat.json")
RISK_STATE_PATH = Path("data/risk_state.json")
INCIDENT_LATCH_PATH = Path("data/risk_incident_latch.json")
POSITIONS_PATH = Path("data/positions.json")
WALLET_PATH = Path("data/virtual_wallet.json")
FUNNEL_PATH = Path("data/promotion_funnel.json")
GOAL_PATH = Path("data/goal_progress.json")
KILL_SWITCH_PATH = Path("data/KILL_SWITCH")
AUDIT_PATH = Path("data/mission_control_audit.jsonl")
LIVE_PID_PATH = Path("data/mission_control_live_pid.json")
CHECKLIST_PATH = Path("docs/CONTROLLED_LIVE_CHECKLIST.md")

# Non-secret keys safe to surface from .env
_SAFE_ENV_KEYS = frozenset(
    {
        "OPERATING_MODE",
        "CONTROLLED_LIVE_ENABLED",
        "DRY_RUN",
        "PAPER_TRADING_PROFILE",
        "ENTRY_POLICY",
        "TRADING_MODE",
        "LOG_LEVEL",
    }
)


def _resolve(root: Path, rel: Path | str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else root / p


def read_json(path: Path, default: Any = None) -> Any:
    """Load JSON from ``path``; return ``default`` on missing/invalid."""
    if default is None:
        default = {}
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return default


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a flat dict (no expansion, no secrets filter)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def safe_env_flags(root: Path | None = None) -> dict[str, str]:
    """Return non-secret operating flags from ``.env``."""
    root = root or ROOT
    raw = parse_env_file(root / ".env")
    return {k: raw.get(k, "") for k in _SAFE_ENV_KEYS}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def heartbeat_age_seconds(heartbeat: dict[str, Any]) -> float | None:
    """Seconds since heartbeat timestamp; None if unknown."""
    for key in ("ts", "timestamp", "updated_at", "written_at", "time"):
        dt = _parse_iso(heartbeat.get(key) if isinstance(heartbeat, dict) else None)
        if dt is not None:
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    return None


def supervisor_liveness(root: Path | None = None) -> dict[str, Any]:
    """Best-effort guess whether launcher_supervisor / main.py is alive."""
    root = root or ROOT
    found: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore
    except ImportError:
        return {"available": False, "processes": [], "alive": None}
    markers = ("launcher_supervisor.py", "main.py")
    root_s = str(root).lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmd = proc.info.get("cmdline") or []
            joined = " ".join(str(c) for c in cmd)
            if not any(m in joined for m in markers):
                continue
            if root_s and root_s not in joined.lower() and "Trading_Bot" not in joined:
                # Still accept if cwd matches
                try:
                    cwd = proc.cwd()
                except (psutil.Error, OSError):
                    cwd = ""
                if str(root) not in str(cwd):
                    continue
            found.append(
                {
                    "pid": proc.info.get("pid"),
                    "cmdline": joined[:240],
                }
            )
        except (psutil.Error, OSError):
            continue
    return {"available": True, "processes": found, "alive": bool(found)}


def load_status(root: Path | None = None) -> dict[str, Any]:
    """Aggregate operational status for the Mission Control header."""
    root = root or ROOT
    hb = read_json(_resolve(root, HEARTBEAT_PATH), {})
    risk = read_json(_resolve(root, RISK_STATE_PATH), {})
    latch_path = _resolve(root, INCIDENT_LATCH_PATH)
    latch = read_json(latch_path, {}) if latch_path.exists() else None
    kill = _resolve(root, KILL_SWITCH_PATH).exists()
    env = safe_env_flags(root)
    live_pid = read_json(_resolve(root, LIVE_PID_PATH), {})
    return {
        "heartbeat": hb if isinstance(hb, dict) else {},
        "heartbeat_age_seconds": heartbeat_age_seconds(hb if isinstance(hb, dict) else {}),
        "risk_state": risk if isinstance(risk, dict) else {},
        "incident_latch_present": latch_path.exists(),
        "incident_latch": latch if isinstance(latch, dict) else latch,
        "kill_switch": kill,
        "env": env,
        "mode": (env.get("OPERATING_MODE") or hb.get("operating_mode") or "").upper(),
        "paper_profile": env.get("PAPER_TRADING_PROFILE")
        or hb.get("paper_trading_profile")
        or "",
        "is_halted": bool(
            (isinstance(risk, dict) and risk.get("is_halted"))
            or latch_path.exists()
        ),
        "supervisor": supervisor_liveness(root),
        "live_pid": live_pid if isinstance(live_pid, dict) else {},
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }


def load_positions(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    positions = read_json(_resolve(root, POSITIONS_PATH), {})
    wallet = read_json(_resolve(root, WALLET_PATH), {})
    open_list: list[Any] = []
    closed_count = 0
    if isinstance(positions, dict):
        raw_open = positions.get("open") or positions.get("open_positions") or []
        if isinstance(raw_open, dict):
            open_list = list(raw_open.values())
        elif isinstance(raw_open, list):
            open_list = raw_open
        raw_closed = positions.get("closed") or positions.get("closed_positions") or []
        if isinstance(raw_closed, list):
            closed_count = len(raw_closed)
        elif isinstance(raw_closed, dict):
            closed_count = len(raw_closed)
    return {
        "open": open_list,
        "open_count": len(open_list),
        "closed_count": closed_count,
        "wallet": wallet if isinstance(wallet, dict) else {},
    }


def load_risk(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    latch_path = _resolve(root, INCIDENT_LATCH_PATH)
    return {
        "risk_state": read_json(_resolve(root, RISK_STATE_PATH), {}),
        "incident_latch_present": latch_path.exists(),
        "incident_latch": read_json(latch_path, {}) if latch_path.exists() else None,
        "kill_switch": _resolve(root, KILL_SWITCH_PATH).exists(),
    }


def load_funnel(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    data = read_json(_resolve(root, FUNNEL_PATH), {})
    return data if isinstance(data, dict) else {"raw": data}


def load_goals(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    data = read_json(_resolve(root, GOAL_PATH), {})
    return data if isinstance(data, dict) else {"raw": data}


def load_gates(root: Path | None = None) -> dict[str, Any]:
    """CONTROLLED_LIVE gate status — never mutates the checklist."""
    root = root or ROOT
    checklist = _resolve(root, CHECKLIST_PATH)
    env = safe_env_flags(root)
    signed = False
    message = "checklist not evaluated"
    unchecked_count = 0
    try:
        from core.live_gate import is_checklist_signed

        signed, message = is_checklist_signed(checklist)
    except Exception as exc:  # noqa: BLE001 — surface import/IO failures
        message = f"gate check failed: {exc}"
    if checklist.exists():
        try:
            text = checklist.read_text(encoding="utf-8", errors="replace")
            unchecked_count = sum(
                1
                for raw in text.splitlines()
                if re.match(r"^\s*-\s*\[\s\]\s+", raw, flags=re.IGNORECASE)
            )
        except OSError:
            pass
    return {
        "checklist_path": str(checklist.relative_to(root) if checklist.is_relative_to(root) else checklist),
        "checklist_exists": checklist.exists(),
        "checklist_signed": signed,
        "message": message,
        "unchecked_count": unchecked_count,
        "operating_mode": env.get("OPERATING_MODE", ""),
        "controlled_live_enabled": env.get("CONTROLLED_LIVE_ENABLED", "").lower()
        in {"1", "true", "yes"},
        "dry_run": env.get("DRY_RUN", "").lower() in {"1", "true", "yes"},
        "note": "Mission Control never writes checklist boxes or Signed-By lines.",
    }


def list_scheduled_tasks() -> list[dict[str, Any]]:
    """List TradingBot* Windows scheduled tasks. Empty on non-Windows."""
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-ScheduledTask | Where-Object { $_.TaskName -like 'TradingBot*' } | "
                    "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName "
                    "-TaskPath $_.TaskPath -ErrorAction SilentlyContinue; "
                    "[PSCustomObject]@{ Name=$_.TaskName; State=[string]$_.State; "
                    "LastResult=$i.LastTaskResult; LastRun=[string]$i.LastRunTime; "
                    "NextRun=[string]$i.NextRunTime } } | ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{"error": str(exc)}]
    if proc.returncode != 0:
        return [{"error": (proc.stderr or proc.stdout or "schtasks query failed")[:400]}]
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [{"error": "failed to parse task list"}]
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


def load_audit(root: Path | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    root = root or ROOT
    path = _resolve(root, AUDIT_PATH)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = {"raw": line}
        if isinstance(row, dict):
            out.append(row)
    out.reverse()
    return out


def validate_bind_host(host: str, *, allow_lan: bool = False, token_set: bool = False) -> None:
    """Refuse non-loopback binds unless explicitly allowed with a token."""
    h = (host or "").strip().lower()
    loopback = {"127.0.0.1", "localhost", "::1"}
    if h in loopback:
        return
    if allow_lan and token_set:
        return
    raise ValueError(
        f"refusing bind host {host!r}: Mission Control binds loopback only "
        "(set MISSION_CONTROL_ALLOW_LAN=1 with MISSION_CONTROL_TOKEN to override)"
    )


def token_from_environ(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get("MISSION_CONTROL_TOKEN") or "").strip()
