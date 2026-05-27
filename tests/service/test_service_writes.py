"""Phase 11.0 write/settings/read tests for the FastAPI sidecar (§15, §14.7, §31.3).

Covers the manual-entry write endpoints (snapshot/post/correction), the settings
read+update endpoints, and the next-rep/validation read slices. Each wraps the
existing pure forms/settings helpers — validation + FormError semantics unchanged.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.jobs import grok_discovery_sweep, x_activity_sync
from app.service.app import create_app
from app import x_client
from scripts import collect_account_snapshot
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


def test_user_metrics_fetch_persists_today_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_api_get_user_metrics() -> dict[str, int | str | None]:
        return {
            "username": "dannyscalant",
            "followers_count": 88,
            "following_count": 351,
            "post_count": 57,
            "listed_count": 2,
        }

    def fake_x_request(*args: object, **kwargs: object) -> x_client.XApiResponse:
        return x_client.XApiResponse(
            status_code=200,
            body={
                "data": {
                    "id": "42",
                    "username": "dannyscalant",
                    "description": "builder",
                    "public_metrics": {
                        "followers_count": 88,
                        "following_count": 351,
                        "tweet_count": 57,
                        "listed_count": 2,
                    },
                }
            },
            raw_response_id=None,
            endpoint="/2/users/me?user.fields=public_metrics,description",
            method="GET",
            elapsed_seconds=0.01,
        )

    monkeypatch.setattr(x_client, "api_get_user_metrics", fake_api_get_user_metrics)
    monkeypatch.setattr(collect_account_snapshot.x_client, "request", fake_x_request)

    resp = client.get("/api/user-metrics", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_inserted"] is True
    assert body["followers_count"] == 88
    assert body["username"] == "dannyscalant"

    today = client.get("/views/today", headers=AUTH).json()
    assert today["snapshot"]["followers_count"] == 88


def test_sync_today_from_x_imports_posts_and_updates_daily_reps(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today_iso = _date_t.today().isoformat()

    def fake_snapshot_run(conn: sqlite3.Connection) -> dict[str, object]:
        return {
            "snapshot_inserted": False,
            "skipped_reason": "duplicate_day_manual_entry_present",
            "error": None,
        }

    def fake_import_run(
        conn: sqlite3.Connection, *, backfill: bool = False
    ) -> dict[str, object]:
        assert backfill is False
        conn.executemany(
            """
            INSERT INTO posts
              (x_post_id, created_at_utc, created_date, text, type,
               posted_via, manual_confirmation_status)
            VALUES (?, datetime('now'), ?, ?, ?, 'api', 'needs_metrics')
            """,
            [
                ("sync-post", today_iso, "today post", "standalone"),
                ("sync-reply", today_iso, "today reply", "reply"),
                ("sync-quote", today_iso, "today quote", "quote"),
            ],
        )
        return {
            "posts_inserted": 3,
            "posts_skipped_existing": 0,
            "rate_limit_hits": 0,
            "error": None,
        }

    def fake_metrics_run(
        conn: sqlite3.Connection, *, batch_limit: int = 100
    ) -> dict[str, object]:
        assert batch_limit == 100
        return {"posts_refreshed": 3, "candidates_considered": 3, "error": None}

    monkeypatch.setattr(
        x_activity_sync.collect_account_snapshot, "run", fake_snapshot_run
    )
    monkeypatch.setattr(x_activity_sync.import_recent_posts, "run", fake_import_run)
    monkeypatch.setattr(x_activity_sync.post_metrics_refresh, "run", fake_metrics_run)

    resp = client.post("/api/sync-today", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["import_posts"]["posts_inserted"] == 3
    assert body["metrics"]["posts_refreshed"] == 3
    assert body["activity"]["daily_activity"]["posts_shipped"] == 1
    assert body["activity"]["daily_activity"]["replies_shipped"] == 1
    assert body["activity"]["daily_activity"]["quotes_shipped"] == 1
    assert body["activity"]["daily_activity"]["reply_sessions_completed"] == 1

    today = client.get("/views/today", headers=AUTH).json()
    assert today["daily_reps"]["row"]["posts_shipped"] == 1
    assert today["daily_reps"]["row"]["replies_shipped"] == 1
    assert today["recent_posts"][0]["confirm_status"] == "needs_metrics"


def test_agent_classify_posts_tags_untagged_imports(client: TestClient) -> None:
    post = client.post(
        "/forms/post",
        json={
            "type": "post",
            "text": "Building Stir so weeknight dinner stops being a tiny kitchen crisis.",
            "manual_confirmation_status": "needs_metrics",
        },
        headers=AUTH,
    )
    assert post.status_code == 200
    post_id = post.json()["post_id"]

    resp = client.post("/agent/classify-posts", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["classified_count"] == 1
    assert body["classified"][0]["post_id"] == post_id
    assert body["classified"][0]["pillar"] == "stir"
    assert body["classified"][0]["audience"] == "icp"

    assert client.get("/views/needs-tagging", headers=AUTH).json()["posts"] == []


def test_agent_grok_sweep_endpoint_formats_summary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(conn: sqlite3.Connection) -> dict[str, object]:
        return {
            "queries_run": 1,
            "candidates_discovered": 2,
            "candidates_verified": 2,
            "candidates_inserted": 2,
            "candidates_rejected_404": 0,
            "error": None,
        }

    monkeypatch.setattr(grok_discovery_sweep, "run", fake_run)

    resp = client.post("/agent/grok-sweep", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["severity"] == "success"
    assert body["summary"]["candidates_inserted"] == 2
    assert "sweep OK" in body["message"]


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

    val = client.get("/views/validation", headers=AUTH)
    assert val.status_code == 200
    assert val.json()["slice"] == "funnel"


def test_create_reply_target_records_and_scores_candidate(client: TestClient) -> None:
    resp = client.post(
        "/reply-targets",
        json={
            "target_post_url": "https://x.com/example/status/12345",
            "target_user": "example",
            "target_post_text": "A thoughtful post about weeknight cooking systems.",
            "like_count": 0,
            "reply_count": 0,
            "repost_count": 0,
            "pillar": "stir",
            "reply_intent": "icp_discovery",
        },
        headers=AUTH,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["created"] is True
    assert isinstance(body["reply_target_id"], int)

    queue = client.get("/views/reply-queue", headers=AUTH)
    assert queue.status_code == 200
    data = queue.json()
    assert data["counters"]["candidates"] == 1
    assert data["items"][0]["handle"] == "example"


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
