"""Mission Control state reader tests."""

from __future__ import annotations

import json
from pathlib import Path

from mission_control import state


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def test_load_status_from_fixtures(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "heartbeat.json",
        {"ts": "2026-07-27T12:00:00+00:00", "operating_mode": "PAPER", "paper_trading_profile": "MAX_FLOW_BAND"},
    )
    _write(tmp_path / "data" / "risk_state.json", {"is_halted": False, "daily_pnl": 1.2})
    _write(tmp_path / ".env", "OPERATING_MODE=PAPER\nPAPER_TRADING_PROFILE=MAX_FLOW_BAND\nCONTROLLED_LIVE_ENABLED=false\n")
    (tmp_path / "data" / "KILL_SWITCH").write_text("x", encoding="utf-8")

    status = state.load_status(tmp_path)
    assert status["mode"] == "PAPER"
    assert status["paper_profile"] == "MAX_FLOW_BAND"
    assert status["kill_switch"] is True
    assert status["incident_latch_present"] is False
    assert status["env"]["OPERATING_MODE"] == "PAPER"


def test_load_positions_open_list(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "positions.json",
        {"open": [{"symbol": "BTC/USDT", "side": "long"}], "closed": [{}, {}]},
    )
    _write(tmp_path / "data" / "virtual_wallet.json", {"balance": 100.0})
    pos = state.load_positions(tmp_path)
    assert pos["open_count"] == 1
    assert pos["closed_count"] == 2
    assert pos["wallet"]["balance"] == 100.0


def test_validate_bind_host_loopback_ok() -> None:
    state.validate_bind_host("127.0.0.1")
    state.validate_bind_host("localhost")


def test_validate_bind_host_lan_refused() -> None:
    try:
        state.validate_bind_host("0.0.0.0", allow_lan=False, token_set=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "loopback" in str(exc)


def test_validate_bind_host_lan_allowed_with_token() -> None:
    state.validate_bind_host("0.0.0.0", allow_lan=True, token_set=True)


def test_load_audit_tail(tmp_path: Path) -> None:
    path = tmp_path / "data" / "mission_control_audit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"ts": "1", "action": "a"}) + "\n" + json.dumps({"ts": "2", "action": "b"}) + "\n",
        encoding="utf-8",
    )
    rows = state.load_audit(tmp_path, limit=10)
    assert rows[0]["action"] == "b"
    assert rows[1]["action"] == "a"
