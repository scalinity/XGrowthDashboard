"""Agent mode and capabilities endpoint tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "test-token-abc123"


def test_agent_mode_and_capabilities_shapes(tmp_path: Path) -> None:
    db_path = tmp_path / "agent-status.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    client = TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}

    mode = client.get("/agent/mode", headers=headers).json()
    assert "data_collection_mode" in mode
    assert "tool_permissions" in mode
    assert mode["tool_permissions"]["publish"] is False

    caps = client.get("/capabilities", headers=headers).json()
    assert "anthropic" in caps
    assert "xurl" in caps
