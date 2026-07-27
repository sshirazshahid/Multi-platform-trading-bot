"""FastAPI Mission Control application (localhost full-ops UI)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mission_control import ops
from mission_control import state

STATIC_DIR = Path(__file__).resolve().parent / "static"


class OpsNoteBody(BaseModel):
    operator_note: str = Field(..., min_length=1)
    confirm_phrase: str | None = None


class ModeBody(OpsNoteBody):
    mode: str


class ProfileBody(OpsNoteBody):
    profile: str


class KillSwitchBody(OpsNoteBody):
    enabled: bool


class ControlledLiveBody(OpsNoteBody):
    action: str
    confirm_phrase: str | None = None


def _root_from_app(request: Request) -> Path:
    return Path(getattr(request.app.state, "root", state.ROOT))


def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_mission_control_token: str | None = Header(default=None),
) -> None:
    expected = str(getattr(request.app.state, "token", "") or "")
    if not expected:
        raise HTTPException(status_code=503, detail="MISSION_CONTROL_TOKEN not configured")
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif x_mission_control_token:
        provided = x_mission_control_token.strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def create_app(
    *,
    root: Path | None = None,
    token: str | None = None,
    bind_host: str = "127.0.0.1",
    allow_lan: bool | None = None,
) -> FastAPI:
    root = Path(root) if root is not None else state.ROOT
    resolved_token = (token if token is not None else state.token_from_environ()).strip()
    lan = (
        allow_lan
        if allow_lan is not None
        else os.environ.get("MISSION_CONTROL_ALLOW_LAN", "").strip() in {"1", "true", "yes"}
    )
    state.validate_bind_host(bind_host, allow_lan=lan, token_set=bool(resolved_token))

    app = FastAPI(title="Mission Control", version="1.0.0", docs_url=None, redoc_url=None)
    app.state.root = root
    app.state.token = resolved_token
    app.state.bind_host = bind_host

    @app.exception_handler(ops.OpsError)
    async def _ops_error(_request: Request, exc: ops.OpsError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc)})

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "mission_control"}

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def api_status(request: Request) -> dict[str, Any]:
        return state.load_status(_root_from_app(request))

    @app.get("/api/positions", dependencies=[Depends(require_token)])
    async def api_positions(request: Request) -> dict[str, Any]:
        return state.load_positions(_root_from_app(request))

    @app.get("/api/risk", dependencies=[Depends(require_token)])
    async def api_risk(request: Request) -> dict[str, Any]:
        return state.load_risk(_root_from_app(request))

    @app.get("/api/funnel", dependencies=[Depends(require_token)])
    async def api_funnel(request: Request) -> dict[str, Any]:
        return state.load_funnel(_root_from_app(request))

    @app.get("/api/goals", dependencies=[Depends(require_token)])
    async def api_goals(request: Request) -> dict[str, Any]:
        return state.load_goals(_root_from_app(request))

    @app.get("/api/gates", dependencies=[Depends(require_token)])
    async def api_gates(request: Request) -> dict[str, Any]:
        return state.load_gates(_root_from_app(request))

    @app.get("/api/tasks", dependencies=[Depends(require_token)])
    async def api_tasks() -> dict[str, Any]:
        return {"tasks": state.list_scheduled_tasks(), "platform": os.name}

    @app.get("/api/audit", dependencies=[Depends(require_token)])
    async def api_audit(request: Request, limit: int = 100) -> dict[str, Any]:
        return {"entries": state.load_audit(_root_from_app(request), limit=min(limit, 500))}

    @app.post("/api/ops/restart", dependencies=[Depends(require_token)])
    async def api_restart(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.restart_bot(
            _root_from_app(request),
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase or "",
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/mode", dependencies=[Depends(require_token)])
    async def api_mode(body: ModeBody, request: Request) -> dict[str, Any]:
        detail = ops.set_safe_mode(
            _root_from_app(request),
            mode=body.mode,
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase or "",
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/paper-profile", dependencies=[Depends(require_token)])
    async def api_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
        detail = ops.set_paper_profile(
            _root_from_app(request),
            profile=body.profile,
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/kill-switch", dependencies=[Depends(require_token)])
    async def api_kill(body: KillSwitchBody, request: Request) -> dict[str, Any]:
        detail = ops.set_kill_switch(
            _root_from_app(request),
            enabled=body.enabled,
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/clear-incident", dependencies=[Depends(require_token)])
    async def api_clear(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        ops.require_confirm("clear-incident", body.confirm_phrase)
        detail = ops.clear_incident(
            _root_from_app(request),
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/run-funnel", dependencies=[Depends(require_token)])
    async def api_funnel_run(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_script(
            _root_from_app(request), kind="funnel", operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/run-goal-report", dependencies=[Depends(require_token)])
    async def api_goal_run(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_script(
            _root_from_app(request), kind="goal-report", operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/task/{name}/run", dependencies=[Depends(require_token)])
    async def api_task_run(name: str, body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_scheduled_task(
            _root_from_app(request), name=name, operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/controlled-live", dependencies=[Depends(require_token)])
    async def api_live(body: ControlledLiveBody, request: Request) -> dict[str, Any]:
        detail = ops.controlled_live(
            _root_from_app(request),
            action=body.action,
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase,
        )
        return {"ok": True, "detail": detail}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
