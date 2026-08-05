"""Mission Control soft-ops and CONTROLLED_LIVE fail-closed tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mission_control import ops
from mission_control.app import create_app


def _signed_checklist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path.write_text(
        f"""# checklist
- [x] item one
- [x] item two

Signed-By: Test Owner {today}
""",
        encoding="utf-8",
    )


def _unsigned_checklist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """# checklist
- [ ] still open
""",
        encoding="utf-8",
    )


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / ".env").write_text(
        "OPERATING_MODE=PAPER\nCONTROLLED_LIVE_ENABLED=false\nDRY_RUN=true\nPAPER_TRADING_PROFILE=STANDARD\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def client(root: Path) -> TestClient:
    app = create_app(root=root, token="tok", bind_host="127.0.0.1")
    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok"}


def test_kill_switch_toggle(root: Path) -> None:
    detail = ops.set_kill_switch(root, enabled=True, operator_note="halt entries")
    assert detail["enabled"] is True
    assert (root / "data" / "KILL_SWITCH").exists()
    ops.set_kill_switch(root, enabled=False, operator_note="resume entries")
    assert not (root / "data" / "KILL_SWITCH").exists()


def test_clear_incident_archives(root: Path) -> None:
    latch = root / "data" / "risk_incident_latch.json"
    latch.write_text(json.dumps({"reason": "test"}), encoding="utf-8")
    ops.clear_incident(root, operator_note="cleared after review")
    assert not latch.exists()
    history = (root / "data" / "risk_incident_history.jsonl").read_text(encoding="utf-8")
    assert "cleared after review" in history


def test_set_mode_requires_confirm(root: Path) -> None:
    with pytest.raises(ops.OpsError):
        ops.set_safe_mode(
            root, mode="OBSERVATION", operator_note="note", confirm_phrase="WRONG"
        )


def test_set_mode_paper_observation(root: Path) -> None:
    ops.set_safe_mode(
        root,
        mode="OBSERVATION",
        operator_note="switch to obs",
        confirm_phrase="CHANGE MODE",
    )
    text = (root / ".env").read_text(encoding="utf-8")
    assert "OPERATING_MODE=OBSERVATION" in text
    assert "CONTROLLED_LIVE_ENABLED=false" in text


def test_controlled_live_arm_refuses_unsigned(root: Path) -> None:
    _unsigned_checklist(root / "docs" / "CONTROLLED_LIVE_CHECKLIST.md")
    with pytest.raises(ops.OpsError, match="checklist"):
        ops.controlled_live(
            root,
            action="arm",
            operator_note="try arm",
            confirm_phrase=ops.LIVE_CONFIRM_PHRASE,
        )
    text = (root / ".env").read_text(encoding="utf-8")
    assert "OPERATING_MODE=PAPER" in text


def test_controlled_live_arm_refuses_wrong_phrase(root: Path) -> None:
    _signed_checklist(root / "docs" / "CONTROLLED_LIVE_CHECKLIST.md")
    with pytest.raises(ops.OpsError, match="confirm_phrase"):
        ops.controlled_live(
            root,
            action="arm",
            operator_note="try arm",
            confirm_phrase="please",
        )


def test_controlled_live_arm_and_start_when_signed(root: Path) -> None:
    checklist = root / "docs" / "CONTROLLED_LIVE_CHECKLIST.md"
    _signed_checklist(checklist)
    before = checklist.read_text(encoding="utf-8")

    spawned: dict = {}

    def fake_spawn(r: Path) -> dict:
        spawned["root"] = str(r)
        return {"pid": 4242, "via": "test"}

    ops.controlled_live(
        root,
        action="arm",
        operator_note="arm live",
        confirm_phrase=ops.LIVE_CONFIRM_PHRASE,
    )
    text = (root / ".env").read_text(encoding="utf-8")
    assert "OPERATING_MODE=CONTROLLED_LIVE" in text
    assert "CONTROLLED_LIVE_ENABLED=true" in text

    detail = ops.controlled_live(
        root,
        action="start",
        operator_note="start live",
        confirm_phrase=ops.LIVE_CONFIRM_PHRASE,
        spawn=fake_spawn,
    )
    assert detail["pid"] == 4242
    assert spawned["root"] == str(root)

    # Never forge / mutate checklist
    assert checklist.read_text(encoding="utf-8") == before

    ops.controlled_live(root, action="disarm", operator_note="back to paper")
    text = (root / ".env").read_text(encoding="utf-8")
    assert "OPERATING_MODE=PAPER" in text
    assert "CONTROLLED_LIVE_ENABLED=false" in text


def test_restart_uses_injected_runner(root: Path) -> None:
    called = {}

    def runner(r: Path) -> dict:
        called["ok"] = True
        return {"method": "test"}

    detail = ops.restart_bot(
        root,
        operator_note="bounce",
        confirm_phrase="RESTART BOT",
        runner=runner,
    )
    assert called["ok"] is True
    assert detail["method"] == "test"


def test_task_allowlist() -> None:
    assert ops.task_allowed("TradingBot-24x7")
    assert ops.task_allowed("TradingBot_PromotionFunnel")
    assert not ops.task_allowed("EvilTask")
    assert not ops.task_allowed("../TradingBot-24x7")


def test_controlled_live_forbidden_when_unsigned(root: Path) -> None:
    """The ops module fails closed on an unsigned checklist."""
    _unsigned_checklist(root / "docs" / "CONTROLLED_LIVE_CHECKLIST.md")
    with pytest.raises(ops.OpsError) as excinfo:
        ops.controlled_live(
            root,
            action="arm",
            operator_note="nope",
            confirm_phrase=ops.LIVE_CONFIRM_PHRASE,
        )
    assert "checklist" in str(excinfo.value).lower()


def test_api_controlled_live_forbidden_when_unsigned(client: TestClient, root: Path) -> None:
    """Fail-closed CONTROLLED_LIVE gate holds over HTTP: unsigned checklist -> 403."""
    _unsigned_checklist(root / "docs" / "CONTROLLED_LIVE_CHECKLIST.md")
    res = client.post(
        "/api/ops/controlled-live",
        headers=_auth(),
        json={
            "action": "arm",
            "operator_note": "nope",
            "confirm_phrase": ops.LIVE_CONFIRM_PHRASE,
        },
    )
    assert res.status_code == 403
    assert "checklist" in res.json()["error"].lower()


OPS_ROUTES = [
    ("/api/ops/kill-switch", {"enabled": True, "operator_note": "halt via ui"}),
    ("/api/ops/restart", {"operator_note": "x", "confirm_phrase": "RESTART BOT"}),
    ("/api/ops/mode", {"mode": "OBSERVATION", "operator_note": "x", "confirm_phrase": "CHANGE MODE"}),
    ("/api/ops/paper-profile", {"profile": "STANDARD", "operator_note": "x"}),
    ("/api/ops/clear-incident", {"operator_note": "x", "confirm_phrase": "CLEAR INCIDENT"}),
    ("/api/ops/run-funnel", {"operator_note": "x"}),
    ("/api/ops/run-goal-report", {"operator_note": "x"}),
    ("/api/ops/task/TradingBot-24x7/run", {"operator_note": "x"}),
    ("/api/ops/controlled-live", {"action": "arm", "operator_note": "x"}),
]


@pytest.mark.parametrize("path, payload", OPS_ROUTES)
def test_ops_routes_require_token(
    client: TestClient, root: Path, path: str, payload: dict
) -> None:
    """All 9 ops routes exist and reject unauthenticated calls with 401."""
    res = client.post(path, json=payload)
    assert res.status_code == 401
    assert not (root / "data" / "KILL_SWITCH").exists()


@pytest.mark.parametrize("path, payload", OPS_ROUTES)
def test_ops_routes_require_operator_note(
    client: TestClient, root: Path, path: str, payload: dict
) -> None:
    """Empty operator_note is rejected (422) before any action executes."""
    res = client.post(path, headers=_auth(), json={**payload, "operator_note": ""})
    assert res.status_code == 422
    assert not (root / "data" / "KILL_SWITCH").exists()


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/ops/restart", {"operator_note": "x", "confirm_phrase": "wrong"}),
        ("/api/ops/mode", {"mode": "OBSERVATION", "operator_note": "x", "confirm_phrase": "wrong"}),
        ("/api/ops/clear-incident", {"operator_note": "x", "confirm_phrase": "wrong"}),
        ("/api/ops/controlled-live", {"action": "arm", "operator_note": "x", "confirm_phrase": "wrong"}),
    ],
)
def test_ops_destructive_routes_enforce_confirm_phrase(
    client: TestClient, root: Path, path: str, payload: dict
) -> None:
    """Destructive routes refuse a wrong confirm phrase with 400 before acting."""
    res = client.post(path, headers=_auth(), json=payload)
    assert res.status_code == 400
    assert "confirm_phrase" in res.json()["error"]
    env_text = (root / ".env").read_text(encoding="utf-8")
    assert "OPERATING_MODE=PAPER" in env_text


def test_api_kill_switch_roundtrip(client: TestClient, root: Path) -> None:
    """Kill switch toggles through the HTTP surface and audits both actions."""
    res = client.post(
        "/api/ops/kill-switch",
        headers=_auth(),
        json={"enabled": True, "operator_note": "halt via ui"},
    )
    assert res.status_code == 200
    assert (root / "data" / "KILL_SWITCH").exists()
    res = client.post(
        "/api/ops/kill-switch",
        headers=_auth(),
        json={"enabled": False, "operator_note": "resume via ui"},
    )
    assert res.status_code == 200
    assert not (root / "data" / "KILL_SWITCH").exists()
    audit = (root / "data" / "mission_control_audit.jsonl").read_text(encoding="utf-8")
    assert "halt via ui" in audit and "resume via ui" in audit


def test_api_task_run_rejects_non_allowlisted(client: TestClient) -> None:
    res = client.post(
        "/api/ops/task/EvilTask/run",
        headers=_auth(),
        json={"operator_note": "x"},
    )
    assert res.status_code == 403


def test_app_imports_ops_but_not_live_rails() -> None:
    """The served app wires ops.py; live trading rails stay out of app.py."""
    import mission_control.app as mc_app

    source = Path(mc_app.__file__).read_text(encoding="utf-8")
    assert "from mission_control import ops" in source
    for banned in (
        "order_manager",
        "bot_engine",
        "risk_manager",
    ):
        assert f"import {banned}" not in source
        assert f"from core.{banned}" not in source
