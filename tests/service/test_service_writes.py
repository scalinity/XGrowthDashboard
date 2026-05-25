"""Phase 11.0 write/settings/read tests for the FastAPI sidecar (§15, §14.7, §31.3).

Covers the manual-entry write endpoints (snapshot/post/correction), the settings
read+update endpoints, and the next-rep/validation read slices. Each wraps the
existing pure forms/settings helpers — validation + FormError semantics unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "writes-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

VALID_SNAPSHOT = {
    "snapshot_date": "2026-05-24",
    "username": "dannyscalant",
    "profile_url": "https://x.com/dannyscalant",
    "followers_count": 64,
    "following_count": 351,
    "post_count": 57,
    "listed_count": 0,
    "baseline_followers": 61,
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "writes_svc.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()

    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    return TestClient(create_app(token=TOKEN, conn_factory=conn_factory))


def test_write_endpoints_require_token(client: TestClient) -> None:
    assert client.post("/forms/post", json={"type": "post", "text": "x"}).status_code == 401


def test_post_snapshot_ok(client: TestClient) -> None:
    resp = client.post("/forms/snapshot", json=VALID_SNAPSHOT, headers=AUTH)
    assert resp.status_code == 200
    assert isinstance(resp.json()["snapshot_id"], int)


def test_post_snapshot_validation_returns_400_with_field_errors(
    client: TestClient,
) -> None:
    resp = client.post(
        "/forms/snapshot", json={"username": "x"}, headers=AUTH
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "field_errors" in detail
    assert "snapshot_date" in detail["field_errors"]


def test_post_log_ok(client: TestClient) -> None:
    resp = client.post(
        "/forms/post", json={"type": "post", "text": "hello world"}, headers=AUTH
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["post_id"], int)


def test_post_log_validation_400(client: TestClient) -> None:
    resp = client.post("/forms/post", json={"type": "bogus"}, headers=AUTH)
    assert resp.status_code == 400


def test_correction_flow(client: TestClient) -> None:
    sid = client.post("/forms/snapshot", json=VALID_SNAPSHOT, headers=AUTH).json()[
        "snapshot_id"
    ]
    resp = client.post(
        "/forms/correction",
        json={
            "snapshot_id": sid,
            "field_name": "followers_count",
            "new_value": "70",
            "reason": "recount after API refresh",
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["correction_id"], int)


def test_next_rep_and_validation_views(client: TestClient) -> None:
    nr = client.get("/views/next-rep", headers=AUTH)
    assert nr.status_code == 200
    assert nr.json()["slice"] == "next_rep"
    # Expanded response: coverage, hypotheses, reply_targets, etc.
    assert "coverage" in nr.json()
    assert "hypotheses" in nr.json()
    assert "reply_targets" in nr.json()

    val = client.get("/views/validation", headers=AUTH)
    assert val.status_code == 200
    assert val.json()["slice"] == "funnel"
    assert "aggregate" in val.json()
    assert "what_we_know" in val.json()


def test_settings_get_and_update(client: TestClient) -> None:
    got = client.get("/settings", headers=AUTH)
    assert got.status_code == 200
    settings = got.json()["settings"]
    assert isinstance(settings, dict) and settings  # seeded, non-empty

    put = client.put(
        "/settings/daily_reply_target", json={"value": 15}, headers=AUTH
    )
    assert put.status_code == 200
    assert put.json()["value"] == 15

    after = client.get("/settings", headers=AUTH).json()["settings"]
    assert after["daily_reply_target"] == 15
