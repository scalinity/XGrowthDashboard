"""Parity tests between read models and FastAPI view endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.read_models.content_performance import build_content_performance_read_model
from app.read_models.progress import build_progress_read_model
from app.read_models.reply_queue import build_reply_queue_read_model
from app.read_models.today import build_today_read_model
from app.read_models.weekly_review import build_weekly_review_read_model
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "test-token-abc123"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "parity.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    return db_path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))


@pytest.mark.parametrize(
    ("path", "builder"),
    [
        ("/views/today", build_today_read_model),
        ("/views/progress", build_progress_read_model),
        ("/views/weekly-review", build_weekly_review_read_model),
        ("/views/content-performance", build_content_performance_read_model),
        ("/views/reply-queue", build_reply_queue_read_model),
    ],
)
def test_read_model_matches_service_endpoint(
    client: TestClient, db_path: Path, path: str, builder
) -> None:
    resp = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    conn = connect(db_path)
    try:
        assert builder(conn) == resp.json()
    finally:
        conn.close()
