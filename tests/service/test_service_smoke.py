"""Phase 11.0 smoke tests for the FastAPI sidecar (§31.3).

Covers the load-bearing guarantees of the service foundation:
  - /health is unauthenticated (the shell's handshake probe),
  - every other route requires the per-launch bearer token,
  - a real read endpoint returns the expected shape against a migrated DB,
  - create_app runs the §28 startup invariants.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from app.service.security import BearerTokenAuth, generate_launch_token

TOKEN = "test-token-abc123"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "svc.db"
    # Apply migrations once; the per-request factory reopens the same file.
    conn = connect(db_path)
    apply_migrations(conn)
    conn.close()

    def factory() -> sqlite3.Connection:
        return connect(db_path)

    app = create_app(token=TOKEN, conn_factory=factory)
    return TestClient(app)


def test_health_is_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "x-growth-dashboard-service"


def test_protected_route_requires_token(client: TestClient) -> None:
    assert client.get("/views/today").status_code == 401
    assert (
        client.get(
            "/views/today", headers={"Authorization": "Bearer not-the-token"}
        ).status_code
        == 401
    )


def test_today_view_with_valid_token(client: TestClient) -> None:
    resp = client.get(
        "/views/today", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["slice"] == "today"
    assert "daily_reps" in body
    assert "account_last_7" in body
    assert isinstance(body["daily_reps"], list)


def test_create_app_runs_startup_invariants() -> None:
    # Production AGENT_TOOLS satisfies the §28 invariants — should not raise.
    app = create_app(token=TOKEN, conn_factory=lambda: connect(":memory:"))
    assert app.title.startswith("X Growth Dashboard")


def test_generate_launch_token_is_unique_and_nonempty() -> None:
    a, b = generate_launch_token(), generate_launch_token()
    assert a and b and a != b


def test_bearer_auth_constant_time_compare_rejects_wrong() -> None:
    auth = BearerTokenAuth("secret")
    import fastapi

    with pytest.raises(fastapi.HTTPException):
        auth(authorization="Bearer wrong")
    with pytest.raises(fastapi.HTTPException):
        auth(authorization=None)
    # Correct token passes (returns None).
    assert auth(authorization="Bearer secret") is None
