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


@pytest.mark.parametrize(
    "path",
    [
        "/views/today",
        "/views/next-rep",
        "/views/progress",
        "/views/content-performance",
        "/views/validation",
        "/views/weekly-review",
        "/views/reply-queue",
        "/views/content-calendar",
        "/views/campaigns",
        "/views/inspiration",
        "/views/blogs",
        "/views/brain-dump",
        "/views/account-researcher",
        "/agent/conversations",
        "/charts/follower-trend",
        "/charts/lane-scatter",
        "/charts/funnel",
        "/charts/funnel-daily",
        "/settings",
    ],
)
def test_all_view_endpoints_return_200(client: TestClient, path: str) -> None:
    """RV5-W7: parametrized smoke test for every read endpoint."""
    resp = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert isinstance(body, dict), f"{path} did not return a JSON object"


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
    assert "today_iso" in body
    assert "snapshot" in body
    assert "daily_reps" in body
    # S2: account_last_7 removed (unused by frontend).
    # daily_reps is now a dict with row/targets/mix, not a list.
    reps = body["daily_reps"]
    assert isinstance(reps, dict)
    assert "row" in reps
    assert "targets" in reps
    assert "mix" in reps
    # snapshot_defaults is provided for the snapshot form.
    assert "snapshot_defaults" in body
    assert "username" in body["snapshot_defaults"]


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


def test_bearer_auth_non_ascii_header_raises_401_not_typeerror() -> None:
    # RV11-1: Starlette decodes header bytes as latin-1, so a malformed
    # Authorization header can reach the dependency as a non-ASCII str. The
    # old secrets.compare_digest(str, str) raised TypeError (→ 500); the fix
    # compares on UTF-8 bytes and must cleanly raise HTTPException (401).
    # (httpx's TestClient can't transmit non-ASCII header values, so this is
    # exercised at the dependency level — the actual fixed code path.)
    import fastapi

    auth = BearerTokenAuth("secret")
    with pytest.raises(fastapi.HTTPException):
        auth(authorization="Bearer café")
