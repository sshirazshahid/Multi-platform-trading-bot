"""FastAPI Mission Control application — observability console + operator controls.

Safety contract:

* The GET surface stays strictly read-only: nothing under ``data/`` is
  written by any GET handler, and ``warehouse.sqlite`` is opened with
  ``mode=ro`` (plus ``PRAGMA query_only=ON``) so this server can never take a
  write lock on the database the running bot is appending to.
* The mutating surface is exactly the 9 POST routes under ``/api/ops/*``,
  wired to ``mission_control/ops.py``. Every one requires the bearer token
  AND a non-empty ``operator_note``; destructive actions additionally require
  their exact confirm phrase. Every action appends to
  ``data/mission_control_audit.jsonl`` (written by ops.py).
* CONTROLLED_LIVE arming stays fail-closed behind the
  ``docs/CONTROLLED_LIVE_CHECKLIST.md`` signature check (``core.live_gate``,
  imported lazily inside ops.py). No checklist signature is ever forged.
* No live trading rail is imported here — no ``order_manager``,
  ``bot_engine``, ``risk_manager`` or exchange client.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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


class StaticFilesNoStore(StaticFiles):
    """Static assets served without caching, so edits are picked up on reload."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


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
        else os.environ.get("MISSION_CONTROL_ALLOW_LAN", "").strip().lower()
        in {"1", "true", "yes"}
    )
    state.validate_bind_host(bind_host, allow_lan=lan, token_set=bool(resolved_token))

    app = FastAPI(title="Mission Control", version="2.1.0", docs_url=None, redoc_url=None)
    app.state.root = root
    app.state.token = resolved_token
    app.state.bind_host = bind_host

    @app.exception_handler(ops.OpsError)
    async def _ops_error(_request: Request, exc: ops.OpsError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc)})

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "deck.html",
            headers={"Cache-Control": "no-store"},
        )

    # The previous console is unchanged and remains the only operator surface:
    # every /api/ops/* control lives there. The deck served at "/" is read-only.
    @app.get("/classic")
    async def classic() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "mission_control", "read_only": False}

    # NOTE: the handlers below are intentionally `def`, not `async def`.
    # Starlette runs sync endpoints in a threadpool; declaring them async would
    # run their blocking work (psutil process scans, PowerShell subprocesses,
    # sqlite reads, file I/O) directly on the event loop and stall every other
    # request behind them.

    @app.get("/api/status", dependencies=[Depends(require_token)])
    def api_status(request: Request) -> dict[str, Any]:
        return state.load_status(_root_from_app(request))

    @app.get("/api/positions", dependencies=[Depends(require_token)])
    def api_positions(request: Request) -> dict[str, Any]:
        return state.load_positions(_root_from_app(request))

    @app.get("/api/risk", dependencies=[Depends(require_token)])
    def api_risk(request: Request) -> dict[str, Any]:
        return state.load_risk(_root_from_app(request))

    @app.get("/api/funnel", dependencies=[Depends(require_token)])
    def api_funnel(request: Request) -> dict[str, Any]:
        return state.load_funnel(_root_from_app(request))

    @app.get("/api/goals", dependencies=[Depends(require_token)])
    def api_goals(request: Request) -> dict[str, Any]:
        return state.load_goals(_root_from_app(request))

    @app.get("/api/gates", dependencies=[Depends(require_token)])
    def api_gates(request: Request) -> dict[str, Any]:
        return state.load_gates(_root_from_app(request))

    @app.get("/api/tasks", dependencies=[Depends(require_token)])
    def api_tasks() -> dict[str, Any]:
        result = state.list_scheduled_tasks()
        return {
            "tasks": result.get("tasks", []),
            "error": result.get("error"),
            "supported": result.get("supported", True),
            "platform": os.name,
        }

    @app.get("/api/candidates", dependencies=[Depends(require_token)])
    def api_candidates(
        request: Request,
        hours: int = Query(default=24, ge=1, le=720),
        limit: int = Query(default=40, ge=1, le=200),
        decision: str | None = Query(default=None, max_length=40),
        family: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        return state.load_candidates(
            _root_from_app(request),
            hours=hours,
            limit=limit,
            decision=decision,
            family=family,
        )

    # Brain: live decision cascade + the research pipeline's own state.
    # MEASURED warm ~21 ms total on the live warehouse (scorer GROUP BY on the
    # ts index 5 ms, ALLOW ids 1 ms, decision_events rowid tail + parse 14 ms,
    # decision-log tail 1.4 ms) plus ~1 ms for the research half once its
    # 300 s artifact cache is warm — inside the 250 ms poll budget with room to
    # spare, so it rides the 12 s poll with no staleness added.
    @app.get("/api/brain", dependencies=[Depends(require_token)])
    def api_brain(
        request: Request,
        window_minutes: int = Query(default=state.BRAIN_WINDOW_MINUTES, ge=5, le=720),
    ) -> dict[str, Any]:
        root = _root_from_app(request)
        return {
            "cascade": state.load_brain(root, window_minutes=window_minutes),
            "research": {
                "artifacts": state.load_research_artifacts(root),
                "ledger": state.load_refuted_ledger(root),
                "promotion": state.load_promotion_path(root),
            },
        }

    @app.get("/api/audit", dependencies=[Depends(require_token)])
    def api_audit(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        path = state._resolve(_root_from_app(request), state.AUDIT_PATH)
        return {
            "entries": state.load_audit(_root_from_app(request), limit=limit),
            "file": state.file_meta(path),
        }

    @app.get("/api/mtsi", dependencies=[Depends(require_token)])
    def api_mtsi(request: Request) -> dict[str, Any]:
        """Micro Two-Sided Inventory research surface (read-only)."""
        return state.load_mtsi(_root_from_app(request))

    # ------------------------------------------------------------------
    # Operator controls (POST /api/ops/*) — wired to mission_control/ops.py.
    # Same auth dependency as the GETs; ops.py enforces operator_note,
    # confirm phrases, allowlists, and the fail-closed CONTROLLED_LIVE gate,
    # and appends every action to data/mission_control_audit.jsonl.

    @app.post("/api/ops/restart", dependencies=[Depends(require_token)])
    def api_restart(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.restart_bot(
            _root_from_app(request),
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase or "",
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/mode", dependencies=[Depends(require_token)])
    def api_mode(body: ModeBody, request: Request) -> dict[str, Any]:
        detail = ops.set_safe_mode(
            _root_from_app(request),
            mode=body.mode,
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase or "",
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/paper-profile", dependencies=[Depends(require_token)])
    def api_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
        detail = ops.set_paper_profile(
            _root_from_app(request),
            profile=body.profile,
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/kill-switch", dependencies=[Depends(require_token)])
    def api_kill(body: KillSwitchBody, request: Request) -> dict[str, Any]:
        detail = ops.set_kill_switch(
            _root_from_app(request),
            enabled=body.enabled,
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/clear-incident", dependencies=[Depends(require_token)])
    def api_clear(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        ops.require_confirm("clear-incident", body.confirm_phrase)
        detail = ops.clear_incident(
            _root_from_app(request),
            operator_note=body.operator_note,
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/run-funnel", dependencies=[Depends(require_token)])
    def api_funnel_run(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_script(
            _root_from_app(request), kind="funnel", operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/run-goal-report", dependencies=[Depends(require_token)])
    def api_goal_run(body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_script(
            _root_from_app(request), kind="goal-report", operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/task/{name}/run", dependencies=[Depends(require_token)])
    def api_task_run(name: str, body: OpsNoteBody, request: Request) -> dict[str, Any]:
        detail = ops.run_scheduled_task(
            _root_from_app(request), name=name, operator_note=body.operator_note
        )
        return {"ok": True, "detail": detail}

    @app.post("/api/ops/controlled-live", dependencies=[Depends(require_token)])
    def api_live(body: ControlledLiveBody, request: Request) -> dict[str, Any]:
        detail = ops.controlled_live(
            _root_from_app(request),
            action=body.action,
            operator_note=body.operator_note,
            confirm_phrase=body.confirm_phrase,
        )
        return {"ok": True, "detail": detail}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFilesNoStore(directory=str(STATIC_DIR)), name="static")

    return app
