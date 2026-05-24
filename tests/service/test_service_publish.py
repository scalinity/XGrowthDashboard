"""Phase 11.0 publish-endpoint tests for the FastAPI sidecar (§28.10, §31.3).

Exercises the server-side click-handler replication on the manual-clipboard
branch (publish_via_api_enabled = FALSE, so no X API / network). Confirms the
§28.10 contract holds through the service: confirm-phrase + length guards, the
six-check token flow, and that the raw token never appears in the response.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "publish-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _setup_draft_post(conn: sqlite3.Connection, text: str = "Draft v1") -> int:
    row = conn.execute(
        """
        INSERT INTO posts
          (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES ('2026-05-22', ?, 'standalone', 'agent_assisted', 'draft')
        RETURNING id
        """,
        (text,),
    ).fetchone()
    return int(row[0])


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "publish_svc.db"
    conn = connect(path)
    apply_migrations(conn)
    seed_settings(conn)
    # Force the manual-clipboard branch — no X API call, no network.
    conn.execute(
        """
        INSERT INTO settings (key, value_json) VALUES ('publish_via_api_enabled', 'false')
        ON CONFLICT(key) DO UPDATE SET value_json = 'false'
        """
    )
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    return TestClient(create_app(token=TOKEN, conn_factory=conn_factory))


def test_publish_requires_token(client: TestClient) -> None:
    assert (
        client.post(
            "/publish", json={"post_id": 1, "text": "hi", "confirm": "confirm"}
        ).status_code
        == 401
    )


def test_publish_requires_confirm_phrase(client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    post_id = _setup_draft_post(conn)
    conn.close()
    resp = client.post(
        "/publish",
        json={"post_id": post_id, "text": "Draft v1", "confirm": "nope"},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_publish_rejects_over_280(client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    post_id = _setup_draft_post(conn)
    conn.close()
    resp = client.post(
        "/publish",
        json={"post_id": post_id, "text": "x" * 281, "confirm": "confirm"},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_publish_manual_clipboard_succeeds(client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    post_id = _setup_draft_post(conn, text="Draft v1")
    conn.close()

    resp = client.post(
        "/publish",
        json={"post_id": post_id, "text": "Draft v1", "confirm": "confirm"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["method"] == "manual_clipboard"
    assert body["intent_url"] and "twitter.com/intent/tweet" in body["intent_url"]
    # The raw confirmation token must never be in the response payload.
    assert "token" not in {k.lower() for k in body}
    assert "raw_token" not in body

    # The publish landed on the posts row (method + timestamp recorded).
    # NB: the manual-clipboard branch intentionally leaves
    # manual_confirmation_status='draft' (only the API branch sets
    # 'confirmed'), since reopening the intent URL posts nothing on its own.
    conn = connect(db_path)
    row = conn.execute(
        "SELECT publish_method, published_to_x_at FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    conn.close()
    assert row["publish_method"] == "manual_clipboard"
    assert row["published_to_x_at"] is not None
