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


def test_rv2_12_handle_with_at_prefix_still_finds_manual_snapshot(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV2-12: if Daniel edits x_handle to '@dannyscalant', the
    duplicate-day guard must still match the bare-handle manual rows."""
    db_conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'x_handle'",
        ('"@dannyscalant"',),
    )
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
        lambda *a, **kw: pytest.fail("must not call X API when manual row exists"),
    )
    summary = collect_account_snapshot.run(db_conn, today_iso="2026-05-22")
    assert summary["snapshot_inserted"] is False
    assert summary["skipped_reason"] == "duplicate_day_manual_entry_present"


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


def test_rv2_8_validate_x_handle_rejects_path_injection_attempts() -> None:
    """RV2-8: handle validation refuses anything that could escape the
    intended endpoint when interpolated into a xurl URL path."""
    import pytest
    bad_handles = [
        "foo/../tweets?max_results=1000",  # path traversal
        "foo?expansions=author_id",         # query injection
        "foo&user.fields=email",            # ampersand injection
        "foo%2Ftweets",                      # url-encoded slash
        "foo bar",                            # whitespace
        "foo.bar",                            # dot
        "this-handle-is-way-too-long-to-be-valid",  # > 15 chars
        "",                                   # empty
        "  ",                                 # whitespace-only
    ]
    for handle in bad_handles:
        with pytest.raises(ValueError):
            x_client.validate_x_handle(handle)


def test_rv2_8_validate_x_handle_accepts_normal_shapes() -> None:
    """RV2-8: real X handle shapes pass through cleanly, '@' stripped."""
    assert x_client.validate_x_handle("dannyscalant") == "dannyscalant"
    assert x_client.validate_x_handle("@dannyscalant") == "dannyscalant"
    assert x_client.validate_x_handle("  @user_15  ") == "user_15"
    assert x_client.validate_x_handle("X") == "X"  # 1 char is min
    assert x_client.validate_x_handle("a" * 15) == "a" * 15  # 15 is max


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


# ---------------------------------------------------------------------------
# RV2-2: import_recent_posts ↔ publish resolver contract — the X post ID
# stored in posts.in_reply_to_post_id by import_recent_posts.py must be the
# same string publish._resolve_reply_target_x_post_id reads back as the
# in_reply_to_x_post_id for the X API call. Earlier Phase 8 code did a
# local-id PK lookup that silently turned replies into standalone tweets.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# RV2-4: raw_api_responses MUST survive publish-flow rollback.
#
# The rollback-survival promise (§17 Phase 7 / §28.30 "every xurl call is
# logged") is satisfied BY ARCHITECTURE: publish_post_atomic in
# app/agent/publish.py runs the X API call OUTSIDE any open transaction
# in its split-txn design (step 2). When the X API call fires, conn is
# in autocommit mode, so _log_raw's INSERT commits immediately. A
# subsequent ROLLBACK in publish_post_atomic step 3 cannot affect the
# already-committed audit row.
#
# These tests pin the architectural invariant. If a future refactor
# wraps the X API call in a transaction (e.g. by moving step 2 inside a
# new `with transaction(conn):` block), the audit row would be wiped on
# rollback and these tests would fail loudly.
# ---------------------------------------------------------------------------
def test_log_raw_outside_transaction_commits_immediately(tmp_path: Path) -> None:
    """Autocommit invariant: when called outside any transaction (the
    actual publish flow's step 2 state), _log_raw's INSERT commits
    immediately. Verified by reading from a SECOND connection — only
    committed rows are visible cross-connection."""
    from app.db import connect

    db_path = tmp_path / "rv2_4_autocommit.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)

    raw_id = x_client._log_raw(
        conn,
        source="xurl",
        endpoint="/2/users/me",
        method="GET",
        body_json=None,
        response_text='{"data": {"id": "1"}}',
        status_code=200,
        notes="RV2-4 autocommit invariant",
    )
    assert raw_id is not None

    # Read from a SEPARATE connection — only sees committed rows.
    other = connect(db_path)
    row = other.execute(
        "SELECT id, status_code, notes FROM raw_api_responses WHERE id = ?",
        (raw_id,),
    ).fetchone()
    other.close()
    conn.close()
    assert row is not None, (
        "RV2-4: _log_raw must commit immediately when called outside any "
        "open transaction (publish flow step 2 state). A second connection "
        "should see the audit row."
    )
    assert row["status_code"] == 200


def test_log_raw_post_publish_failure_audit_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the publish-flow architecture means _log_raw inside
    publish_post_to_x_via_api commits the audit row BEFORE any later
    ROLLBACK in publish_post_atomic step 3. This test simulates the
    real flow shape (X API call OUTSIDE the transaction, then a
    transactional commit that fails) and confirms the audit survives.
    """
    from app.db import connect, transaction

    db_path = tmp_path / "rv2_4_e2e.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)

    # Step 1+2: X API call OUTSIDE any txn (per publish_post_atomic's
    # split-txn design). _log_raw commits in autocommit mode.
    raw_id = x_client._log_raw(
        conn,
        source="xurl",
        endpoint="/2/tweets",
        method="POST",
        body_json={"text": "hello"},
        response_text='{"data":{"id":"1234567890","text":"hello"}}',
        status_code=200,
        notes="publish_post_to_x_via_api attempt 1/3",
    )
    assert raw_id is not None

    # Step 3: post-API commit transaction fires + rolls back.
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO posts (text, type, posted_via, created_date, "
                "manual_confirmation_status) VALUES (?, ?, ?, ?, ?)",
                ("hello", "standalone", "agent_assisted", "2026-05-22", "draft"),
            )
            raise sqlite3.IntegrityError("simulated commit-time failure")
    except sqlite3.IntegrityError:
        pass

    # The audit row written BEFORE the transaction must still exist —
    # the rollback only affects what was inside the txn.
    row = conn.execute(
        "SELECT id, status_code, notes FROM raw_api_responses WHERE id = ?",
        (raw_id,),
    ).fetchone()
    conn.close()
    assert row is not None, (
        "RV2-4 regression: publish-flow audit row was wiped by a later "
        "rollback. The split-txn architecture must keep _log_raw outside "
        "the transaction so the audit commits independently."
    )
    assert "publish_post_to_x_via_api" in row["notes"]


def test_publish_post_atomic_keeps_x_api_call_outside_transaction() -> None:
    """RV2-4 architectural invariant pin: a structural inspection of
    publish_post_atomic confirms the X API call lives in step 2 OUTSIDE
    any `with transaction(conn):` block. If anyone moves the X API call
    inside a transaction (whether by accident or by refactor), this
    test will fail."""
    import inspect

    from app.agent import publish

    source = inspect.getsource(publish.publish_post_atomic)
    # Find the X API call line.
    api_call_idx = source.index("publish_post_to_x_via_api(")
    api_call_line_start = source.rfind("\n", 0, api_call_idx) + 1
    # Find the nearest enclosing transaction context above the API call.
    pre_call = source[:api_call_line_start]
    # The publish flow's split-txn pattern: there's a step-3 commit
    # transaction, but it lives AFTER the X API call, not wrapping it.
    last_txn_open = pre_call.rfind("with transaction(conn):")
    if last_txn_open >= 0:
        # The transaction opened before the X API call must have closed
        # — i.e., there's a dedent before the API call. Check that the
        # last `with transaction(conn):` is followed by a return/exception
        # before the API call. Simplest proxy: count indent of the API
        # call line vs the last transaction open line.
        txn_line_start = pre_call.rfind("\n", 0, last_txn_open) + 1
        txn_indent = last_txn_open - txn_line_start
        api_line_indent = api_call_idx - api_call_line_start
        assert api_line_indent <= txn_indent, (
            "RV2-4 architectural invariant violated: publish_post_to_x_via_api "
            "appears to live INSIDE a `with transaction(conn):` block. The "
            "split-txn design requires the X API call to be OUTSIDE any open "
            "transaction so _log_raw's audit row commits immediately and "
            "survives any later rollback. See app/x_client.py::_log_raw "
            "docstring."
        )


def test_import_recent_posts_in_reply_to_round_trips_through_publish_resolver(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: import a reply via the daily incremental job, then
    confirm publish._resolve_reply_target_x_post_id returns the original
    X post ID string (not None, not a local-id miss)."""
    from app.agent import publish as _publish

    fake_body = {
        "data": [
            {
                "id": "555",
                "text": "my reply text",
                "created_at": "2026-05-22T10:00:00.000Z",
                "conversation_id": "100",
                "referenced_tweets": [{"type": "replied_to", "id": "100"}],
                "in_reply_to_user_id": "999",
            },
        ]
    }
    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: _fake_response(fake_body),
    )
    summary = import_recent_posts.run(db_conn)
    assert summary["posts_inserted"] == 1
    # Resolve the imported reply row.
    row = db_conn.execute(
        "SELECT id, type, in_reply_to_post_id FROM posts WHERE x_post_id = '555'"
    ).fetchone()
    assert row is not None
    assert row["type"] == "reply"
    # The publish resolver must see the original X post ID '100' verbatim.
    resolved = _publish._resolve_reply_target_x_post_id(db_conn, row)
    assert resolved == "100", (
        "publish._resolve_reply_target_x_post_id must round-trip the X post "
        "ID string written by import_recent_posts.py (RV2-2 regression). "
        f"got {resolved!r}"
    )
