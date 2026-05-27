"""Smoke coverage for all registered native views."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "test-token-abc123"

VIEW_ENDPOINTS: dict[str, str | None] = {
    "today": "/views/today",
    "next-rep": "/views/next-rep",
    "progress": "/views/progress",
    "content-performance": "/views/content-performance",
    "funnel": "/views/validation",
    "weekly-review": "/views/weekly-review",
    "manual-entry": "/views/needs-tagging",
    "settings": "/settings",
    "agent-chat": "/agent/conversations",
    "reply-queue": "/views/reply-queue",
    "brain-dump": "/views/brain-dump",
    "coach": "/agent/conversations",
    "account-researcher": "/views/account-researcher",
    "content-calendar": "/views/content-calendar",
    "campaigns": "/views/campaigns",
    "inspiration": "/views/inspiration",
    "blogs": "/views/blogs",
    "blog-editor": "/views/blogs",
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "views.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    return TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))


@pytest.mark.parametrize("view_id", list(VIEW_ENDPOINTS))
def test_registered_view_has_backend_smoke(client: TestClient, view_id: str) -> None:
    path = VIEW_ENDPOINTS[view_id]
    assert path is not None
    resp = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, f"{view_id} -> {path} returned {resp.status_code}"
    assert isinstance(resp.json(), dict)
