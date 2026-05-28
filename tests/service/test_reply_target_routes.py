"""Reply-target mutation routes return 404 for unknown IDs."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "test-token-abc123"


def test_skip_unknown_reply_target_returns_404(tmp_path: Path) -> None:
    db_path = tmp_path / "rt.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    client = TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    resp = client.put(
        "/reply-targets/99999/skip",
        headers=headers,
        json={"skip_reason": "not_relevant"},
    )
    assert resp.status_code == 404
