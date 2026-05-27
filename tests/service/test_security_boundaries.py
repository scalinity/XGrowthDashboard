"""CORS and bearer-token boundary tests for the FastAPI sidecar."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app

TOKEN = "test-token-abc123"


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "security.db"
    conn = connect(db_path)
    apply_migrations(conn)
    conn.close()
    return TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))


def test_protected_endpoints_reject_missing_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/views/today").status_code == 401
    assert client.get("/settings").status_code == 401


def test_protected_endpoints_reject_invalid_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer wrong-token"}
    assert client.get("/views/today", headers=headers).status_code == 401
    assert client.get("/settings/secrets", headers=headers).status_code == 401


def test_health_remains_public_without_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_foreign_origin_does_not_receive_permissive_cors(tmp_path: Path) -> None:
    client = _client(tmp_path)
    preflight = client.options(
        "/views/today",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.headers.get("access-control-allow-origin") is None
