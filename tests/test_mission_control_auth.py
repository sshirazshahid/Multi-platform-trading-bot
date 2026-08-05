"""Mission Control auth and bind-guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mission_control.app import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "data").mkdir()
    (tmp_path / ".env").write_text("OPERATING_MODE=PAPER\n", encoding="utf-8")
    app = create_app(root=tmp_path, token="secret-token", bind_host="127.0.0.1")
    return TestClient(app)


def test_health_open(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_status_requires_token(client: TestClient) -> None:
    res = client.get("/api/status")
    assert res.status_code == 401


def test_status_with_bearer(client: TestClient) -> None:
    res = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200
    assert "mode" in res.json()


def test_status_with_header_token(client: TestClient) -> None:
    res = client.get("/api/status", headers={"X-Mission-Control-Token": "secret-token"})
    assert res.status_code == 200


def test_mtsi_requires_token(client: TestClient) -> None:
    res = client.get("/api/mtsi")
    assert res.status_code == 401


def test_mtsi_with_bearer(client: TestClient) -> None:
    res = client.get("/api/mtsi", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200
    body = res.json()
    assert body["family"] == "mtsi_inventory_v1"
    assert body["max_gross_inventory_usd"] == 1.0
    assert "honesty" in body


def test_create_app_refuses_non_loopback_without_lan() -> None:
    with pytest.raises(ValueError, match="loopback"):
        create_app(token="x", bind_host="0.0.0.0", allow_lan=False)


def test_index_serves_html(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "<title>Mission Deck</title>" in res.text
    assert "MISSION<b>DECK</b>" in res.text
