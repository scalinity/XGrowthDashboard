"""Phase 7 — scheduled-job tests + xurl-wrapper tests + manual-fallback parity.

Covers the four §17 Phase 7 jobs (collect_account_snapshot,
import_recent_posts, post_metrics_refresh, reply_target_metrics_refresh)
plus the cross-cutting concerns:

- Rate-limit handling (429 + Retry-After) — each job should record
  rate_limit_hits=1 and leave per-row last-refresh timestamps stable.
- 404 → target_deleted transition for the reply-target job per §29.11.
- Manual-fallback parity — every job no-ops cleanly when
  data_collection_mode='manual'.
- Backfill idempotency for import_recent_posts.

All tests monkey-patch ``app.x_client.request`` (and
``app.x_client.batch_request``) so the subprocess plumbing isn't
exercised; that surface gets its own unit tests above.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app import x_client
from app.db import apply_migrations, connect
from app.jobs import post_metrics_refresh, reply_target_metrics_refresh
from scripts import collect_account_snapshot, import_recent_posts
from scripts.seed_settings import seed_settings


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "phase7.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    yield conn
    conn.close()


def _set_mode(conn: sqlite3.Connection, mode: str) -> None:
    conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'data_collection_mode'",
        (json.dumps(mode),),
    )


def _fake_response(
    body: dict[str, Any] | list[Any], *, status: int = 200
) -> x_client.XApiResponse:
    return x_client.XApiResponse(
        status_code=status,
        body=body,
        raw_response_id=None,
        endpoint="(test)",
        method="GET",
        elapsed_seconds=0.001,
    )


# ---------------------------------------------------------------------------
# collect_account_snapshot.py — daily snapshot job.
# ---------------------------------------------------------------------------
def test_collect_account_snapshot_inserts_row_on_200(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_body = {
        "data": {
            "id": "12345",
            "username": "dannyscalant",
            "name": "Daniel",
            "description": "build in public",
            "public_metrics": {
                "followers_count": 64,
                "following_count": 200,
                "tweet_count": 1234,
                "listed_count": 1,
            },
        }
    }
    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: _fake_response(fake_body),
    )
    summary = collect_account_snapshot.run(db_conn, today_iso="2026-05-22")
    assert summary["snapshot_inserted"] is True
    assert summary["error"] is None
    row = db_conn.execute(
        "SELECT followers_count, source FROM account_snapshots "
        "WHERE snapshot_date = '2026-05-22'"
    ).fetchone()
    assert row is not None
    assert row["followers_count"] == 64
    assert row["source"] == "api"


def test_collect_account_snapshot_skips_when_manual_mode(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(db_conn, "manual")
    called = {"n": 0}

    def fail(*a, **kw):
        called["n"] += 1
        raise AssertionError("xurl should not be invoked in manual mode")

    monkeypatch.setattr(x_client, "request", fail)
    summary = collect_account_snapshot.run(db_conn)
    assert summary["snapshot_inserted"] is False
    assert summary["skipped_reason"] == "data_collection_mode=manual"
    assert called["n"] == 0


def test_collect_account_snapshot_preserves_manual_entry_same_day(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Daniel already entered a manual snapshot for today.
    db_conn.execute(
        """
        INSERT INTO account_snapshots
          (snapshot_date, collected_at_utc, username, profile_url,
           followers_count, following_count, post_count, listed_count,
           baseline_followers, source, data_quality)
        VALUES ('2026-05-22', datetime('now'), 'dannyscalant',
                'https://x.com/dannyscalant', 64, 200, 1234, 1, 61,
                'manual', 'manual')
        """
    )
    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: _fake_response({"data": {"public_metrics": {
            "followers_count": 99, "following_count": 99, "tweet_count": 99, "listed_count": 99
        }}}),
    )
    summary = collect_account_snapshot.run(db_conn, today_iso="2026-05-22")
    assert summary["snapshot_inserted"] is False
    assert summary["skipped_reason"] == "duplicate_day_manual_entry_present"
    # Manual row's follower_count is untouched.
    row = db_conn.execute(
        "SELECT followers_count, source FROM account_snapshots WHERE snapshot_date = '2026-05-22'"
    ).fetchone()
    assert row["followers_count"] == 64
    assert row["source"] == "manual"


def test_collect_account_snapshot_429_does_not_insert_row(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rate_limit(*a, **kw):
        raise x_client.XApiRateLimited("rate-limited", retry_after_seconds=60.0)

    monkeypatch.setattr(x_client, "request", rate_limit)
    summary = collect_account_snapshot.run(db_conn, today_iso="2026-05-22")
    assert summary["snapshot_inserted"] is False
    assert summary["rate_limit_hits"] == 1
    assert "rate-limited" in (summary["error"] or "")
    count = db_conn.execute(
        "SELECT COUNT(*) FROM account_snapshots WHERE snapshot_date = '2026-05-22'"
    ).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# import_recent_posts.py — daily incremental + --backfill.
# ---------------------------------------------------------------------------
def test_import_recent_posts_inserts_new_rows(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_body = {
        "data": [
            {
                "id": "111",
                "text": "first post body",
                "created_at": "2026-05-22T10:00:00.000Z",
                "conversation_id": "111",
            },
            {
                "id": "222",
                "text": "reply to someone",
                "created_at": "2026-05-22T10:05:00.000Z",
                "conversation_id": "999",
                "referenced_tweets": [{"type": "replied_to", "id": "999"}],
                "in_reply_to_user_id": "888",
            },
        ]
    }
    monkeypatch.setattr(x_client, "request", lambda *a, **kw: _fake_response(fake_body))
    summary = import_recent_posts.run(db_conn)
    assert summary["posts_inserted"] == 2
    assert summary["posts_skipped_existing"] == 0
    row = db_conn.execute(
        "SELECT type, posted_via, manual_confirmation_status FROM posts WHERE x_post_id = '222'"
    ).fetchone()
    assert row["type"] == "reply"
    assert row["posted_via"] == "api"
    assert row["manual_confirmation_status"] == "needs_metrics"


def test_import_recent_posts_skips_existing_x_post_ids(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-populate one row.
    db_conn.execute(
        "INSERT INTO posts (x_post_id, created_date, text, type, posted_via, manual_confirmation_status) "
        "VALUES ('111', '2026-05-22', 'manually logged earlier', 'standalone', 'manual', 'confirmed')"
    )
    fake_body = {
        "data": [
            {"id": "111", "text": "x api version", "created_at": "2026-05-22T10:00:00.000Z"},
            {"id": "333", "text": "new one", "created_at": "2026-05-22T11:00:00.000Z"},
        ]
    }
    monkeypatch.setattr(x_client, "request", lambda *a, **kw: _fake_response(fake_body))
    summary = import_recent_posts.run(db_conn)
    assert summary["posts_inserted"] == 1
    assert summary["posts_skipped_existing"] == 1
    # Manual row's text + posted_via unchanged.
    row = db_conn.execute(
        "SELECT text, posted_via FROM posts WHERE x_post_id = '111'"
    ).fetchone()
    assert row["text"] == "manually logged earlier"
    assert row["posted_via"] == "manual"


def test_import_recent_posts_backfill_is_idempotent(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_body = {
        "data": [
            {"id": "555", "text": "backfilled", "created_at": "2026-05-22T10:00:00.000Z"},
        ]
    }
    monkeypatch.setattr(x_client, "request", lambda *a, **kw: _fake_response(fake_body))
    first = import_recent_posts.run(db_conn, backfill=True)
    assert first["posts_inserted"] == 1
    assert first["skipped_reason"] is None
    # Second --backfill run short-circuits without touching X API.
    called = {"n": 0}

    def must_not_call(*a, **kw):
        called["n"] += 1
        raise AssertionError("backfill must not re-call X API after audit gate exists")

    monkeypatch.setattr(x_client, "request", must_not_call)
    second = import_recent_posts.run(db_conn, backfill=True)
    assert second["skipped_reason"] == "already_ran"
    assert second["posts_inserted"] == 0
    assert called["n"] == 0
    # Exactly one phase_7_post_backfill audit row exists.
    n = db_conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'phase_7_post_backfill'"
    ).fetchone()[0]
    assert n == 1


def test_import_recent_posts_skips_when_manual_mode(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(db_conn, "manual")
    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: pytest.fail("must not call X API in manual mode"),
    )
    summary = import_recent_posts.run(db_conn)
    assert summary["skipped_reason"] == "data_collection_mode=manual"
    assert summary["posts_inserted"] == 0


# ---------------------------------------------------------------------------
# post_metrics_refresh.py — hourly metrics refresh.
# ---------------------------------------------------------------------------
def test_post_metrics_refresh_inserts_snapshot_and_updates_last_refresh(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_conn.execute(
        "INSERT INTO posts (x_post_id, created_date, text, type, posted_via, manual_confirmation_status) "
        "VALUES ('aaa', date('now', '-2 days'), 'p', 'standalone', 'manual', 'confirmed')"
    )
    fake_body = {
        "data": [
            {
                "id": "aaa",
                "public_metrics": {
                    "like_count": 12,
                    "reply_count": 3,
                    "retweet_count": 1,
                    "quote_count": 0,
                    "bookmark_count": 2,
                    "impression_count": 800,
                },
            }
        ]
    }
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: [_fake_response(fake_body)],
    )
    summary = post_metrics_refresh.run(db_conn)
    assert summary["posts_refreshed"] == 1
    snap = db_conn.execute(
        "SELECT likes, impressions FROM post_metric_snapshots WHERE x_post_id = 'aaa'"
    ).fetchone()
    assert snap["likes"] == 12
    assert snap["impressions"] == 800
    last = db_conn.execute(
        "SELECT last_metrics_refresh_at_utc FROM posts WHERE x_post_id = 'aaa'"
    ).fetchone()[0]
    assert last is not None


def test_post_metrics_refresh_429_keeps_last_refresh_stable(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_conn.execute(
        "INSERT INTO posts (x_post_id, created_date, text, type, posted_via, manual_confirmation_status) "
        "VALUES ('bbb', date('now', '-2 days'), 'p', 'standalone', 'manual', 'confirmed')"
    )
    db_conn.execute(
        "UPDATE posts SET last_metrics_refresh_at_utc = '2026-05-21 10:00:00' WHERE x_post_id = 'bbb'"
    )

    def rate_limit(*a, **kw):
        raise x_client.XApiRateLimited("rate-limited", retry_after_seconds=30.0)

    monkeypatch.setattr(x_client, "batch_request", rate_limit)
    summary = post_metrics_refresh.run(db_conn)
    assert summary["rate_limit_hits"] == 1
    # last_metrics_refresh_at_utc unchanged.
    last = db_conn.execute(
        "SELECT last_metrics_refresh_at_utc FROM posts WHERE x_post_id = 'bbb'"
    ).fetchone()[0]
    assert last == "2026-05-21 10:00:00"


def test_post_metrics_refresh_skips_when_manual_mode(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(db_conn, "manual")
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: pytest.fail("must not call X API in manual mode"),
    )
    summary = post_metrics_refresh.run(db_conn)
    assert summary["skipped_reason"] == "data_collection_mode=manual"


# ---------------------------------------------------------------------------
# reply_target_metrics_refresh.py — 404 → target_deleted detection.
# ---------------------------------------------------------------------------
def _seed_candidate(
    conn: sqlite3.Connection, *, x_post_id: str, follower_count: int = 1000
) -> int:
    cur = conn.execute(
        """
        INSERT INTO reply_targets
          (discovered_via, target_post_url, target_x_post_id,
           target_author_handle, target_author_follower_count,
           target_text, last_checked_at_utc, like_count, reply_count,
           relevance_score, reply_opportunity_score)
        VALUES ('manual', ?, ?, 'someone', ?, 'a post', date('now', '-2 days'),
                10, 4, 2, 2)
        """,
        (f"https://x.com/someone/status/{x_post_id}", x_post_id, follower_count),
    )
    return int(cur.lastrowid)


def test_reply_target_metrics_refresh_updates_snapshot_and_scores(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt_id = _seed_candidate(db_conn, x_post_id="100")
    fake_body = {
        "data": [
            {
                "id": "100",
                "public_metrics": {
                    "like_count": 30,
                    "reply_count": 12,
                    "retweet_count": 5,
                    "quote_count": 1,
                    "bookmark_count": 8,
                    "impression_count": 1200,
                },
            }
        ]
    }
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: [_fake_response(fake_body)],
    )
    summary = reply_target_metrics_refresh.run(db_conn)
    assert summary["candidates_refreshed"] == 1
    assert summary["candidates_marked_deleted"] == 0
    snap_count = db_conn.execute(
        "SELECT COUNT(*) FROM reply_target_snapshots WHERE reply_target_id = ?",
        (rt_id,),
    ).fetchone()[0]
    assert snap_count == 1
    parent = db_conn.execute(
        "SELECT like_count, reply_count FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()
    assert parent["like_count"] == 30
    assert parent["reply_count"] == 12


def test_reply_target_404_transitions_to_target_deleted(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt_id = _seed_candidate(db_conn, x_post_id="404a")
    # X API omits the missing ID from data and surfaces it via errors.
    fake_body = {
        "data": [],
        "errors": [{"value": "404a", "title": "Not Found Error", "resource_id": "404a"}],
    }
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: [_fake_response(fake_body)],
    )
    summary = reply_target_metrics_refresh.run(db_conn)
    assert summary["candidates_marked_deleted"] == 1
    row = db_conn.execute(
        "SELECT status FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()
    assert row["status"] == "target_deleted"
    # An audit_logs 'data/reply_target_marked_deleted' row exists.
    audit = db_conn.execute(
        "SELECT details_json FROM audit_logs WHERE event_type = 'reply_target_marked_deleted'"
    ).fetchone()
    assert audit is not None
    details = json.loads(audit[0])
    assert details["target_x_post_id"] == "404a"
    assert details["detected_via"] == "x_api_404"


def test_reply_target_metrics_refresh_429_keeps_last_checked_stable(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt_id = _seed_candidate(db_conn, x_post_id="429a")
    prev_checked = db_conn.execute(
        "SELECT last_checked_at_utc FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()[0]

    def rate_limit(*a, **kw):
        raise x_client.XApiRateLimited("rate-limited", retry_after_seconds=15.0)

    monkeypatch.setattr(x_client, "batch_request", rate_limit)
    summary = reply_target_metrics_refresh.run(db_conn)
    assert summary["rate_limit_hits"] == 1
    cur_checked = db_conn.execute(
        "SELECT last_checked_at_utc FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()[0]
    assert cur_checked == prev_checked  # no silent score drift


def test_reply_target_already_posted_candidate_404_stays_posted(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-hoc deletion of an already-posted candidate must NOT transition
    its status — already-posted is a terminal-ish state."""
    rt_id = _seed_candidate(db_conn, x_post_id="posted1")
    db_conn.execute(
        "UPDATE reply_targets SET status = 'posted' WHERE id = ?", (rt_id,)
    )
    # The job only selects status='candidate' rows, so this candidate
    # isn't picked up.
    fake_body = {"data": [], "errors": [{"title": "Not Found", "value": "posted1"}]}
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: [_fake_response(fake_body)],
    )
    summary = reply_target_metrics_refresh.run(db_conn)
    assert summary["candidates_considered"] == 0
    row = db_conn.execute(
        "SELECT status FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()
    assert row["status"] == "posted"


def test_reply_target_metrics_refresh_skips_when_manual_mode(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_mode(db_conn, "manual")
    _seed_candidate(db_conn, x_post_id="m1")
    monkeypatch.setattr(
        x_client, "batch_request",
        lambda *a, **kw: pytest.fail("must not call X API in manual mode"),
    )
    summary = reply_target_metrics_refresh.run(db_conn)
    assert summary["skipped_reason"] == "data_collection_mode=manual"


# ---------------------------------------------------------------------------
# x_client wrapper unit tests (no subprocess) — exercise the inference
# helpers + the audit-row logging without invoking xurl.
# ---------------------------------------------------------------------------
def test_x_client_infer_status_code_200_envelope():
    assert x_client._infer_status_code({"data": []}, 0, "") == 200


def test_x_client_infer_status_code_429_envelope():
    body = {"errors": [{"status": 429, "title": "Too Many Requests"}]}
    assert x_client._infer_status_code(body, 0, "") == 429


def test_x_client_infer_status_code_title_only_404():
    body = {"errors": [{"title": "Not Found"}]}
    assert x_client._infer_status_code(body, 0, "") == 404


def test_x_client_parse_retry_after_from_body():
    import time as _t
    future = _t.time() + 90
    ra = x_client._parse_retry_after(
        {"errors": [{"status": 429, "reset": future}]}, ""
    )
    assert ra is not None
    assert 80 < ra < 100


def test_x_client_log_raw_inserts_audit_row(
    db_conn: sqlite3.Connection,
) -> None:
    audit_id = x_client._log_raw(
        db_conn,
        source="xurl",
        endpoint="/2/users/me",
        method="GET",
        body_json=None,
        response_text='{"data": {"id": "1"}}',
        status_code=200,
        notes="unit test",
    )
    assert audit_id is not None
    row = db_conn.execute(
        "SELECT source, endpoint_or_command, status_code FROM raw_api_responses WHERE id = ?",
        (audit_id,),
    ).fetchone()
    assert row["source"] == "xurl"
    assert row["status_code"] == 200
