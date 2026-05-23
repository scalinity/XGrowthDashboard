"""Phase 8 — X API write-side tests.

Pins:

* The §22 + §29.11 token-consumed matrix (429 / 403 / 5xx / timeout).
* The §29.1 "Manual workflows remain inviolable" round-trip (publish
  flow ends with no X API call when publish_via_api_enabled = FALSE).
* The end-to-end success path with posts.x_post_id populated from the
  X API response.
* The sliding-window rate-limit accounting that gates the publish
  flow before the X API call fires.

All X API I/O is served from vcr.py-shaped YAML cassettes under
``tests/fixtures/x_api/`` via the subprocess-aware
``tests._xurl_fixture.use_cassette`` context manager. CI never makes a
real X API call.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from freezegun import freeze_time

from app.agent import confirmation
from app.agent._internal_tools import publish_post_to_x
from tests._xurl_fixture import assert_no_x_api_calls, use_cassette


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_draft_post(
    conn: sqlite3.Connection, text: str = "draft text", *, type_: str = "standalone"
) -> int:
    cur = conn.execute(
        """
        INSERT INTO posts
            (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES (date('now'), ?, ?, 'agent_assisted', 'draft')
        """,
        (text, type_),
    )
    return int(cur.lastrowid)


def _seed_published_row(
    conn: sqlite3.Connection,
    *,
    text: str,
    x_post_id: str | None,
    published_at_utc: str,
) -> int:
    """Insert a row with the given (published_to_x_at, x_post_id) shape.

    Used by RV2-6 tests to seed both 'actually landed on X' rows and
    'timeout/abandoned' rows that should NOT count toward the rate-limit
    sliding window.
    """
    cur = conn.execute(
        """
        INSERT INTO posts
            (created_date, text, type, posted_via, manual_confirmation_status,
             published_to_x_at, x_post_id, publish_method)
        VALUES (date('now'), ?, 'standalone', 'agent_assisted',
                CASE WHEN ? IS NULL THEN 'draft' ELSE 'confirmed' END,
                ?, ?, 'agent_confirmed')
        """,
        (text, x_post_id, published_at_utc, x_post_id),
    )
    return int(cur.lastrowid)


def _make_reply_draft_post(
    conn: sqlite3.Connection,
    text: str = "reply text",
    *,
    target_x_post_id: str = "1234567890",
) -> tuple[int, int]:
    """Create a target posts row (with x_post_id) + a reply draft pointing at it.

    Per publish.py::_resolve_reply_target_x_post_id docstring, posts.in_reply_to_post_id
    is a TEXT column storing the target's X post ID directly (matches migration
    001 line 111 + the app/forms/post_log.py populator). Earlier test helper
    versions wrote the local posts.id integer here — that worked only because
    an earlier (buggy) resolver did a PK→x_post_id lookup. The fixed resolver
    treats the column verbatim, so this helper now matches the established
    semantics."""
    cur = conn.execute(
        """
        INSERT INTO posts
            (created_date, text, type, posted_via, manual_confirmation_status, x_post_id)
        VALUES (date('now'), 'original', 'standalone', 'manual', 'confirmed', ?)
        """,
        (target_x_post_id,),
    )
    target_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO posts
            (created_date, text, type, in_reply_to_post_id, posted_via, manual_confirmation_status)
        VALUES (date('now'), ?, 'reply', ?, 'agent_assisted', 'draft')
        """,
        (text, target_x_post_id),
    )
    return int(cur.lastrowid), target_id


def _set_setting(conn: sqlite3.Connection, key: str, value_json: str) -> None:
    conn.execute("UPDATE settings SET value_json = ? WHERE key = ?", (value_json, key))
    conn.commit()


def _disable_api_branch(conn: sqlite3.Connection) -> None:
    _set_setting(conn, "publish_via_api_enabled", "false")


# ---------------------------------------------------------------------------
# End-to-end fixture-recorded success
# ---------------------------------------------------------------------------
def test_end_to_end_api_success_populates_posts_x_post_id(db_conn, monkeypatch):
    """Default branch (publish_via_api_enabled=TRUE): draft → token mint
    → confirm → POST /2/tweets (fixture) → posts row created with the
    fixture's x_post_id + published_to_x_at + publish_method='agent_confirmed'."""
    post_id = _make_draft_post(db_conn, text="phase 8 end-to-end")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="phase 8 end-to-end"
    )

    with use_cassette(monkeypatch, "publish_post_success_200") as recorded_calls:
        result = publish_post_to_x(
            db_conn, post_id=post_id, confirmation_token=minted.raw_token
        )

    assert result.success is True
    assert result.method == "agent_confirmed"
    assert result.x_post_id == "1747000000000000001"

    post_row = db_conn.execute(
        """
        SELECT publish_method, x_post_id, published_to_x_at,
               manual_confirmation_status, publish_attempt_count
        FROM posts WHERE id = ?
        """,
        (post_id,),
    ).fetchone()
    assert post_row["publish_method"] == "agent_confirmed"
    assert post_row["x_post_id"] == "1747000000000000001"
    assert post_row["published_to_x_at"] is not None
    assert post_row["manual_confirmation_status"] == "confirmed"
    assert post_row["publish_attempt_count"] == 1

    # Token row updated with consumed_by_x_post_id (audit-trail invariant).
    token_row = db_conn.execute(
        "SELECT consumed_at_utc, consumed_by_x_post_id FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is not None
    assert token_row["consumed_by_x_post_id"] == "1747000000000000001"

    # Confirm the subprocess.run patch actually saw a POST /2/tweets call
    # with the right body shape — proves the publish wrapper reaches the
    # API call rather than short-circuiting on a manual branch.
    assert any("POST" in call for call in recorded_calls)
    assert any("/2/tweets" in arg for call in recorded_calls for arg in call)


def test_end_to_end_reply_success_includes_in_reply_to(db_conn, monkeypatch):
    """Reply variant: in_reply_to_tweet_id is included in the X API body."""
    reply_id, target_id = _make_reply_draft_post(
        db_conn, text="phase 8 reply", target_x_post_id="9876543210"
    )
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=reply_id, draft_text="phase 8 reply"
    )

    with use_cassette(monkeypatch, "publish_reply_success_200") as recorded_calls:
        result = publish_post_to_x(
            db_conn, post_id=reply_id, confirmation_token=minted.raw_token
        )

    assert result.success is True
    assert result.x_post_id == "1747000000000000002"

    # Confirm in_reply_to_tweet_id landed in the recorded argv body.
    body_seen = False
    for call in recorded_calls:
        for i, token in enumerate(call):
            if token == "--data" and i + 1 < len(call):
                if "in_reply_to_tweet_id" in call[i + 1] and "9876543210" in call[i + 1]:
                    body_seen = True
                    break
    assert body_seen, "publish_reply_to_x did not include the target x_post_id in body"


# ---------------------------------------------------------------------------
# 429 rate-limit (X-side) — token stays UN-consumed
# ---------------------------------------------------------------------------
def test_x_api_429_leaves_token_unconsumed(db_conn, monkeypatch):
    """§22 Phase 8: X API 429 mid-publish → token UN-consumed; Daniel
    retries after reset_at."""
    post_id = _make_draft_post(db_conn, text="rate limit me")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="rate limit me"
    )

    with use_cassette(monkeypatch, "publish_rate_limit_429"):
        result = publish_post_to_x(
            db_conn, post_id=post_id, confirmation_token=minted.raw_token
        )

    assert result.success is False
    assert result.error_kind == "rate_limited"
    assert "rate" in (result.error or "").lower()

    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is None, (
        "429 must leave token UN-consumed per §22 + §29.11; Daniel retries after reset"
    )

    post_row = db_conn.execute(
        "SELECT publish_method, x_post_id, publish_attempt_count, publish_last_error FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    assert post_row["x_post_id"] is None
    # Attempt counter bumps so the audit trail records the rejection.
    assert post_row["publish_attempt_count"] == 1
    assert "Rate" in (post_row["publish_last_error"] or "")


# ---------------------------------------------------------------------------
# 5xx retry-exhausted — ROLLBACK + token consumed per rule #10(f)
# ---------------------------------------------------------------------------
def test_x_api_5xx_retry_exhausted_rolls_back_and_consumes_token(
    db_conn, monkeypatch
):
    """§22 Phase 8: 5xx after bounded retry → ROLLBACK, publish_method='failed',
    token CONSUMED. Crash-recovery reconciles via api_get_recent_tweets."""
    post_id = _make_draft_post(db_conn, text="server error me")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="server error me"
    )

    # x_posting_publish_retry_attempts_per_token=2 default → 3 total attempts.
    # Queue 3 server-error cassettes so the retry loop reaches exhaustion.
    cassettes = ["publish_server_error_500"] * 3
    monkeypatch.setattr(
        "app.x_client._DEFAULT_WRITE_RETRY_SLEEP_SECONDS", 0.0
    )
    with use_cassette(monkeypatch, cassettes):
        result = publish_post_to_x(
            db_conn, post_id=post_id, confirmation_token=minted.raw_token
        )

    assert result.success is False
    assert result.error_kind == "server_error"

    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is not None, (
        "5xx after retries must CONSUME the token per rule #10(f)"
    )

    post_row = db_conn.execute(
        "SELECT publish_method, x_post_id, publish_attempt_count, publish_last_error FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    assert post_row["publish_method"] == "failed"
    assert post_row["x_post_id"] is None
    assert post_row["publish_attempt_count"] == 1
    assert "XApiServerError" in (post_row["publish_last_error"] or "")


# ---------------------------------------------------------------------------
# 403 cold reply — token consumed, no posts.x_post_id, UX surfaces hint
# ---------------------------------------------------------------------------
def test_x_api_403_cold_reply_consumes_token_no_posts_row(db_conn, monkeypatch):
    """§22 + §29.11 Phase 8: 403 cold-reply → token CONSUMED (X considers
    it a real attempt), no posts.x_post_id, agent_tool_calls.status='error',
    UX surfaces 'engage with this author's posts first, or use the manual
    fallback'."""
    reply_id, _ = _make_reply_draft_post(
        db_conn, text="cold reply attempt", target_x_post_id="1234567890"
    )
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=reply_id, draft_text="cold reply attempt"
    )

    with use_cassette(monkeypatch, "publish_cold_reply_403"):
        result = publish_post_to_x(
            db_conn, post_id=reply_id, confirmation_token=minted.raw_token
        )

    assert result.success is False
    assert result.error_kind == "cold_reply"

    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is not None, (
        "403 must CONSUME the token per §22 (X considered it a real attempt)"
    )

    # No posts.x_post_id; publish_method='failed'.
    post_row = db_conn.execute(
        "SELECT publish_method, x_post_id, publish_last_error FROM posts WHERE id = ?",
        (reply_id,),
    ).fetchone()
    assert post_row["publish_method"] == "failed"
    assert post_row["x_post_id"] is None
    assert "XApiColdReply" in (post_row["publish_last_error"] or "")

    # agent_tool_calls row has status='error' for audit.
    tool_call = db_conn.execute(
        """
        SELECT status FROM agent_tool_calls
        WHERE tool_name = 'publish_post_to_x'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert tool_call is not None and tool_call["status"] == "error"


# ---------------------------------------------------------------------------
# Timeout mid-transaction — ROLLBACK + crash-recovery picks up
# ---------------------------------------------------------------------------
def test_x_api_timeout_rolls_back_and_surfaces_for_crash_recovery(
    db_conn, monkeypatch
):
    """§22 + §28.10 step 8 Phase 8: timeout mid-call → ROLLBACK,
    publish_method='unknown', token CONSUMED, recovery.detect_orphans
    picks it up on next boot via the existing x_post_id IS NULL
    predicate."""
    post_id = _make_draft_post(db_conn, text="PHASE_8_CRASH_RECOVERY_ORPHAN_TEXT")
    minted = confirmation.mint_confirmation_token(
        db_conn,
        post_id=post_id,
        draft_text="PHASE_8_CRASH_RECOVERY_ORPHAN_TEXT",
    )

    with use_cassette(monkeypatch, "publish_timeout"):
        result = publish_post_to_x(
            db_conn, post_id=post_id, confirmation_token=minted.raw_token
        )

    assert result.success is False
    assert result.error_kind == "timeout"

    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is not None, (
        "Timeout must CONSUME the token; retrying would risk duplicate post"
    )

    post_row = db_conn.execute(
        "SELECT publish_method, x_post_id, published_to_x_at, publish_last_error FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    assert post_row["publish_method"] == "unknown"
    assert post_row["x_post_id"] is None
    assert post_row["published_to_x_at"] is not None
    assert "XApiTimeout" in (post_row["publish_last_error"] or "")

    # The orphan-detection scan would surface this row — same shape as
    # the existing recovery.detect_orphans test in test_agent.py.
    from app.agent import recovery

    orphans = recovery.detect_orphans(db_conn)
    orphan_ids = {o.post_id for o in orphans}
    assert post_id in orphan_ids, (
        "Timeout orphan must surface in recovery.detect_orphans for §28.10 step 8 reconciliation"
    )


# ---------------------------------------------------------------------------
# Manual fallback round-trip (publish_via_api_enabled = FALSE)
# ---------------------------------------------------------------------------
def test_manual_fallback_round_trip_no_api_call(db_conn, monkeypatch):
    """§29.1 / §25 Phase 8 acceptance gate: with publish_via_api_enabled
    = FALSE the full publish flow ends with no X API call firing —
    Daniel completes the publish manually via the existing
    'Mark posted' UI. The assert_no_x_api_calls guard fails the test
    if subprocess.run sees an xurl invocation."""
    _disable_api_branch(db_conn)
    post_id = _make_draft_post(db_conn, text="manual fallback path")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="manual fallback path"
    )

    with assert_no_x_api_calls(monkeypatch):
        result = publish_post_to_x(
            db_conn, post_id=post_id, confirmation_token=minted.raw_token
        )

    assert result.success is True
    assert result.method == "manual_clipboard"
    assert result.intent_url is not None
    assert "manual+fallback+path" in result.intent_url

    post_row = db_conn.execute(
        "SELECT publish_method, x_post_id FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert post_row["publish_method"] == "manual_clipboard"
    # Manual flow leaves x_post_id NULL until Daniel pastes the live URL
    # via the existing Phase-2 "Mark posted" form.
    assert post_row["x_post_id"] is None


# ---------------------------------------------------------------------------
# Sliding-window rate-limit accounting
# ---------------------------------------------------------------------------
def test_write_rate_capacity_refuses_at_15min_limit(db_conn):
    """50 publishes within a 15-minute window trip check_write_rate_capacity()
    refusal; recovery after the window rolls over succeeds. Uses freezegun
    so the test is deterministic regardless of wall-clock time."""
    from app import x_client

    initial = datetime(2026, 5, 22, 23, 0, 0, tzinfo=timezone.utc)

    with freeze_time(initial):
        # Lower the limits so the test runs fast (1-minute and 5-minute windows
        # would also work but the spec defaults are 50 / 1000).
        _set_setting(db_conn, "x_write_rate_limit_per_15min", "5")
        _set_setting(db_conn, "x_write_rate_limit_per_24h", "100")

        # Seed 5 prior publishes inside the 15-minute window. Per RV2-6
        # the counter filters by x_post_id IS NOT NULL so these rows must
        # carry distinct x_post_ids to count toward the rate-limit window
        # (timeouts / abandoned manual clicks intentionally do not count).
        for i in range(5):
            db_conn.execute(
                """
                INSERT INTO posts
                    (created_date, text, type, posted_via,
                     manual_confirmation_status, published_to_x_at, x_post_id)
                VALUES (date('now'), ?, 'standalone', 'agent_assisted',
                        'confirmed', ?, ?)
                """,
                (
                    f"prior {i}",
                    initial.strftime("%Y-%m-%d %H:%M:%S"),
                    f"99000{i}",
                ),
            )
        db_conn.commit()

        capacity = x_client.check_write_rate_capacity(db_conn)
        assert capacity.ok is False
        assert capacity.count_15min == 5
        assert capacity.limit_15min == 5
        assert "rate-limited" in (capacity.reason or "")

    # Roll the clock past the 15-minute window → capacity returns to OK.
    with freeze_time(initial + timedelta(minutes=16)):
        capacity_after = x_client.check_write_rate_capacity(db_conn)
        assert capacity_after.ok is True
        assert capacity_after.count_15min == 0


def test_publish_refuses_when_write_rate_exhausted_token_unconsumed(
    db_conn, monkeypatch
):
    """The publish wrapper short-circuits before the X API call when the
    15-min window is full. Token stays UN-consumed (matches the §22
    429 path because the X-side state is identical: nothing was sent)."""
    initial = datetime(2026, 5, 22, 23, 0, 0, tzinfo=timezone.utc)

    with freeze_time(initial):
        _set_setting(db_conn, "x_write_rate_limit_per_15min", "1")
        # Seed 1 prior publish to saturate the 1-per-15-minute limit.
        # RV2-6: must include x_post_id so the counter sees this as an
        # actually-landed publish (phantom timeouts no longer count).
        db_conn.execute(
            """
            INSERT INTO posts
                (created_date, text, type, posted_via,
                 manual_confirmation_status, published_to_x_at, x_post_id)
            VALUES (date('now'), 'saturator', 'standalone', 'agent_assisted',
                    'confirmed', ?, '888888')
            """,
            (initial.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        db_conn.commit()

        post_id = _make_draft_post(db_conn, text="this should be refused")
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_id, draft_text="this should be refused"
        )

        # No cassette needed — the publish wrapper short-circuits on
        # check_write_rate_capacity BEFORE the X API call. The guard
        # confirms no xurl subprocess fired.
        with assert_no_x_api_calls(monkeypatch):
            result = publish_post_to_x(
                db_conn, post_id=post_id, confirmation_token=minted.raw_token
            )

        assert result.success is False
        assert result.error_kind == "rate_limited"

        token_row = db_conn.execute(
            "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
            (minted.token_id,),
        ).fetchone()
        assert token_row["consumed_at_utc"] is None, (
            "Pre-API rate-limit refusal must leave token UN-consumed"
        )


# ---------------------------------------------------------------------------
# Migration 019 audit row + settings defaults
# ---------------------------------------------------------------------------
def test_migration_019_seeded_defaults_present(db_conn):
    """Migration 019 + seed_settings landed the three Phase 8 keys with
    the §25 Phase 8 / §29.6 defaults."""
    expected = {
        "publish_via_api_enabled": "true",
        "x_write_rate_limit_per_15min": "50",
        "x_write_rate_limit_per_24h": "1000",
    }
    for key, want in expected.items():
        row = db_conn.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
        assert row is not None, f"Phase 8 settings row missing: {key}"
        assert row["value_json"] == want, (
            f"Phase 8 settings row {key!r} expected {want!r}, got {row['value_json']!r}"
        )

    # Audit row for migration_applied_019 exists.
    audit_row = db_conn.execute(
        """
        SELECT details_json FROM audit_logs
        WHERE event_category = 'migration'
          AND event_type = 'migration_applied_019'
        """
    ).fetchone()
    assert audit_row is not None
    assert "publish_via_api_enabled" in audit_row["details_json"]


# ---------------------------------------------------------------------------
# RV2-6: rate-limit counter must filter by x_post_id IS NOT NULL so
# timeouts (which defensively set published_to_x_at) and abandoned
# manual-clipboard intents (which set published_to_x_at at click-time)
# don't consume rate-limit slots.
# ---------------------------------------------------------------------------
def test_rv2_6_rate_limit_counter_excludes_timeouts_and_abandoned_clicks(
    db_conn: sqlite3.Connection,
) -> None:
    """Counter must filter by x_post_id IS NOT NULL. Pre-RV2-6 it
    counted every row with published_to_x_at — phantom rate-limit slots
    from timeouts and abandoned manual clicks."""
    from datetime import datetime, timedelta, timezone

    from app import x_client

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    # 2 actually-landed rows (real publishes — should count).
    _seed_published_row(
        db_conn, text="real publish A", x_post_id="111111", published_at_utc=recent,
    )
    _seed_published_row(
        db_conn, text="real publish B", x_post_id="222222", published_at_utc=recent,
    )
    # 1 timeout row (published_to_x_at set defensively, no x_post_id — must NOT count).
    _seed_published_row(
        db_conn, text="timeout phantom", x_post_id=None, published_at_utc=recent,
    )
    # 1 abandoned manual-clipboard click (same shape — must NOT count).
    _seed_published_row(
        db_conn, text="abandoned click", x_post_id=None, published_at_utc=recent,
    )

    since = now - timedelta(minutes=15)
    count = x_client._count_recent_publishes(db_conn, since=since)
    assert count == 2, (
        f"RV2-6 regression: rate-limit counter saw {count} (should be 2). "
        "Timeouts + abandoned clicks must not consume rate-limit slots."
    )


def test_rv2_6_oldest_publish_since_excludes_timeouts(
    db_conn: sqlite3.Connection,
) -> None:
    """_oldest_publish_since must apply the same filter so the
    'rate-limited until {reset_time}' UX reflects only real publishes."""
    from datetime import datetime, timedelta, timezone

    from app import x_client

    now = datetime.now(timezone.utc)
    earlier = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    later = (now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")

    # Phantom (no x_post_id) lands earlier — pre-RV2-6 it would dominate MIN().
    _seed_published_row(
        db_conn, text="phantom", x_post_id=None, published_at_utc=earlier,
    )
    _seed_published_row(
        db_conn, text="real", x_post_id="999999", published_at_utc=later,
    )

    since = now - timedelta(minutes=15)
    oldest = x_client._oldest_publish_since(db_conn, since=since)
    assert oldest is not None
    assert oldest.strftime("%Y-%m-%d %H:%M:%S") == later, (
        "RV2-6 regression: oldest-publish reflected the phantom timeout row "
        "instead of the actual landed publish."
    )
