"""Local health/readiness helpers for the dashboard package."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dashboard.state import (
    PROJECT_ROOT,
    _HEARTBEAT_FUTURE_TOLERANCE_SECONDS,
    _HEARTBEAT_STALE_SECONDS,
)

def _load_core_module(name: str):
    """Import a module from core/ directly, bypassing core/__init__.py
    (which pulls in BotEngine → schedule → rich, unnecessary for dashboard)."""
    p = PROJECT_ROOT / "core" / "{}.py".format(name)
    if not p.exists():
        return None
    spec = _ilu.spec_from_file_location("core.{}".format(name), str(p))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _read_local_status_file(path):
    """Read a local JSON object; malformed or missing data is unavailable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _operational_health_from_local_state(
    heartbeat,
    risk_state,
    *,
    now=None,
    stale_after_seconds=_HEARTBEAT_STALE_SECONDS,
):
    """Classify process health from local state without contacting exchanges."""
    if not isinstance(heartbeat, dict):
        return False, "heartbeat_missing_or_invalid"
    if heartbeat.get("is_halted") is True:
        return False, "heartbeat_reports_halted"
    if isinstance(risk_state, dict) and risk_state.get("is_halted") is True:
        return False, "risk_state_reports_halted"
    halted_exchanges = heartbeat.get("halted_exchanges")
    if isinstance(halted_exchanges, (list, tuple, set)) and halted_exchanges:
        return False, "exchange_halt_reported"

    try:
        stale_limit = float(stale_after_seconds)
        if stale_limit <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return False, "heartbeat_stale_threshold_invalid"

    raw_timestamp = heartbeat.get("timestamp")
    try:
        if isinstance(raw_timestamp, (int, float)):
            heartbeat_time = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc)
        else:
            heartbeat_time = datetime.fromisoformat(
                str(raw_timestamp).replace("Z", "+00:00")
            )
        if heartbeat_time.tzinfo is None:
            heartbeat_time = heartbeat_time.replace(tzinfo=timezone.utc)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        age_seconds = (current_time - heartbeat_time).total_seconds()
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return False, "heartbeat_timestamp_invalid"

    if age_seconds < -_HEARTBEAT_FUTURE_TOLERANCE_SECONDS:
        return False, "heartbeat_timestamp_in_future"
    if age_seconds > stale_limit:
        return False, "heartbeat_stale"
    return True, "heartbeat_fresh_and_not_halted"


def build_health_status_payload(
    *,
    heartbeat=None,
    risk_state=None,
    heartbeat_path="data/heartbeat.json",
    risk_state_path="data/risk_state.json",
    now=None,
    stale_after_seconds=_HEARTBEAT_STALE_SECONDS,
    config_module=None,
    approved_evidence=None,
    evidence_path=None,
    paper_prerequisites_met=True,
):
    """Build the dashboard's local-only operational/readiness status payload."""
    dashboard_root = PROJECT_ROOT
    heartbeat_file = Path(heartbeat_path)
    risk_state_file = Path(risk_state_path)
    if not heartbeat_file.is_absolute():
        heartbeat_file = dashboard_root / heartbeat_file
    if not risk_state_file.is_absolute():
        risk_state_file = dashboard_root / risk_state_file
    local_heartbeat = (
        _read_local_status_file(heartbeat_file) if heartbeat is None else heartbeat
    )
    local_risk_state = (
        _read_local_status_file(risk_state_file)
        if risk_state is None
        else risk_state
    )
    healthy, health_reason = _operational_health_from_local_state(
        local_heartbeat,
        local_risk_state,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )

    from core.readiness import readiness_payload

    payload = readiness_payload(
        system_healthy=healthy,
        system_health_reason=health_reason,
        config_module=config_module,
        repo_root=dashboard_root,
        evidence_path=evidence_path,
        approved_evidence=approved_evidence,
        now=now,
        paper_prerequisites_met=paper_prerequisites_met,
    )
    payload["operational_status"] = "healthy" if healthy else "unhealthy"
    payload["status_source"] = "local_files"
    return payload
