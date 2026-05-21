"""Form-level persistence tests — each submit writes the right rows.

Pairs with ``test_forms_validation.py`` (rejection paths) — this file covers
the happy path. Queues are also tested here since they query persisted state.
"""

from __future__ import annotations

import sqlite3

from app.forms.classify import submit_classification
from app.forms.daily_reps import submit_daily_activity
from app.forms.post_log import add_post_id, submit_post
from app.forms.queues import needs_post_id, needs_tagging
from app.forms.snapshot import submit_snapshot
from app.forms.stir_event import submit_stir_event
from app.forms.stir_tester import submit_tester
from app.forms.weekly_review import submit_weekly_review


def _seed_post(db_conn: sqlite3.Connection, **overrides) -> int:
    payload = {
        "type": "post",
        "text": "Default text",
        "posted_at_utc": "2026-05-21T15:00:00Z",
        "x_post_id": "1000000001",
    }
    payload.update(overrides)
    return submit_post(db_conn, payload)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def test_snapshot_persists_with_manual_quality(db_conn: sqlite3.Connection) -> None:
    new_id = submit_snapshot(
        db_conn,
        {
            "snapshot_date": "2026-05-20",
            "username": "dannyscalant",
            "profile_url": "https://x.com/dannyscalant",
            "followers_count": 120,
            "following_count": 80,
            "post_count": 200,
            "listed_count": 3,
            "like_count": 1500,
            "media_count": 12,
            "bio_text": "scalinity / building Stir",
            "baseline_followers": 61,
        },
    )
    row = db_conn.execute(
        "SELECT * FROM account_snapshots WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["source"] == "manual"
    assert row["data_quality"] == "manual"
    assert row["followers_count"] == 120
    assert row["bio_text"] == "scalinity / building Stir"
    assert row["baseline_followers"] == 61


# ---------------------------------------------------------------------------
# Post log
# ---------------------------------------------------------------------------

def test_post_with_x_post_id_is_confirmed(db_conn: sqlite3.Connection) -> None:
    new_id = _seed_post(db_conn)
    row = db_conn.execute("SELECT * FROM posts WHERE id = ?", (new_id,)).fetchone()
    assert row["manual_confirmation_status"] == "confirmed"
    assert row["posted_via"] == "manual"
    assert row["type"] == "standalone"  # "post" → "standalone" per UI mapping


def test_post_without_x_post_id_is_needs_id(db_conn: sqlite3.Connection) -> None:
    new_id = submit_post(
        db_conn,
        {
            "type": "reply",
            "text": "Quick reply",
            "posted_at_utc": "2026-05-21T15:00:00Z",
            # no x_post_id
        },
    )
    row = db_conn.execute("SELECT * FROM posts WHERE id = ?", (new_id,)).fetchone()
    assert row["manual_confirmation_status"] == "needs_id"
    assert row["x_post_id"] is None
    assert row["type"] == "reply"


def test_add_post_id_raises_when_post_missing(db_conn: sqlite3.Connection) -> None:
    import pytest
    from app.forms import FormError

    with pytest.raises(FormError) as exc:
        add_post_id(db_conn, 99999, "1234567890")
    assert "post_id" in exc.value.field_errors


def test_add_post_id_raises_when_x_post_id_duplicate(
    db_conn: sqlite3.Connection,
) -> None:
    import pytest
    from app.forms import FormError

    pid1 = _seed_post(db_conn, text="first", x_post_id="555")
    pid2 = submit_post(
        db_conn,
        {
            "type": "reply",
            "text": "no id yet",
            "posted_at_utc": "2026-05-21T15:00:00Z",
        },
    )
    with pytest.raises(FormError) as exc:
        add_post_id(db_conn, pid2, "555")
    assert "x_post_id" in exc.value.field_errors
    # Original row should be untouched.
    row = db_conn.execute(
        "SELECT manual_confirmation_status FROM posts WHERE id = ?", (pid2,)
    ).fetchone()
    assert row["manual_confirmation_status"] == "needs_id"


def test_add_post_id_flips_to_confirmed(db_conn: sqlite3.Connection) -> None:
    new_id = submit_post(
        db_conn,
        {
            "type": "reply",
            "text": "no id yet",
            "posted_at_utc": "2026-05-21T15:00:00Z",
        },
    )
    add_post_id(db_conn, new_id, "9999999999", "https://x.com/x/status/9999999999")
    row = db_conn.execute("SELECT * FROM posts WHERE id = ?", (new_id,)).fetchone()
    assert row["manual_confirmation_status"] == "confirmed"
    assert row["x_post_id"] == "9999999999"
    assert row["url"] == "https://x.com/x/status/9999999999"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_classification_persists(db_conn: sqlite3.Connection) -> None:
    pid = _seed_post(db_conn)
    new_id = submit_classification(
        db_conn,
        {
            "post_id": pid,
            "pillar": "stir",
            "audience": "icp",
            "cta": "ask",
            "why_posted": "trying stir-CTA-icp recipe",
            "hypothesis": "tester install in 24h",
            "expected_signal": "DM or download with self-reported source",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM post_classifications WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["pillar"] == "stir"
    assert row["audience"] == "icp"
    assert row["cta"] == "ask"
    assert row["why_posted"] == "trying stir-CTA-icp recipe"


def test_classification_overwrite_with_flag(db_conn: sqlite3.Connection) -> None:
    pid = _seed_post(db_conn)
    first = submit_classification(
        db_conn,
        {"post_id": pid, "pillar": "stir", "audience": "icp", "cta": "ask"},
    )
    second = submit_classification(
        db_conn,
        {"post_id": pid, "pillar": "build", "audience": "other", "cta": "none"},
        allow_overwrite=True,
    )
    assert first == second  # same row id, updated in place
    row = db_conn.execute(
        "SELECT * FROM post_classifications WHERE id = ?", (first,)
    ).fetchone()
    assert row["pillar"] == "build"
    assert row["cta"] == "none"


# ---------------------------------------------------------------------------
# Daily reps
# ---------------------------------------------------------------------------

def test_daily_reps_upserts(db_conn: sqlite3.Connection) -> None:
    # First insert.
    submit_daily_activity(
        db_conn,
        {
            "activity_date": "2026-05-21",
            "posts_shipped": 1,
            "replies_shipped": 12,
            "quotes_shipped": 0,
            "reply_sessions_completed": 1,
            "high_quality_reply_targets_found": 5,
        },
    )
    row1 = db_conn.execute(
        "SELECT * FROM daily_activity WHERE activity_date = ?",
        ("2026-05-21",),
    ).fetchone()
    assert row1["posts_shipped"] == 1
    assert row1["minimum_reps_completed"] == 1  # hits all targets

    # Re-submit lower numbers — should overwrite, not append, and flip min reps.
    submit_daily_activity(
        db_conn,
        {
            "activity_date": "2026-05-21",
            "posts_shipped": 0,
            "replies_shipped": 5,
            "quotes_shipped": 0,
            "reply_sessions_completed": 0,
            "high_quality_reply_targets_found": 0,
        },
    )
    row2 = db_conn.execute(
        "SELECT * FROM daily_activity WHERE activity_date = ?",
        ("2026-05-21",),
    ).fetchone()
    assert row2["posts_shipped"] == 0
    assert row2["replies_shipped"] == 5
    assert row2["minimum_reps_completed"] == 0

    count = db_conn.execute(
        "SELECT COUNT(*) FROM daily_activity WHERE activity_date = ?",
        ("2026-05-21",),
    ).fetchone()[0]
    assert count == 1  # still one row — upserted, not duplicated


def test_daily_reps_pulls_targets_from_settings(db_conn: sqlite3.Connection) -> None:
    submit_daily_activity(
        db_conn,
        {
            "activity_date": "2026-05-22",
            "posts_shipped": 1,
            "replies_shipped": 12,
            "quotes_shipped": 0,
            "reply_sessions_completed": 1,
            "high_quality_reply_targets_found": 0,
        },
    )
    row = db_conn.execute(
        "SELECT * FROM daily_activity WHERE activity_date = ?",
        ("2026-05-22",),
    ).fetchone()
    # The seeded defaults: daily_post_target=1, daily_reply_target=12.
    assert row["planned_posts"] == 1
    assert row["planned_replies"] == 12


# ---------------------------------------------------------------------------
# Stir event
# ---------------------------------------------------------------------------

def test_stir_event_persists_with_self_reported_icp(
    db_conn: sqlite3.Connection,
) -> None:
    new_id = submit_stir_event(
        db_conn,
        {
            "event_category": "acquisition",
            "event_type": "tester_install",
            "occurred_at_utc": "2026-05-21T17:00:00Z",
            "attribution_method": "self_reported",
            "source_data_quality": "manual",
            "is_likely_icp": 1,
            "source": "DM said: 'saw your reply to @parenting_acct'",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM stir_conversion_events WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["is_likely_icp"] == 1
    assert row["attribution_method"] == "self_reported"
    assert row["event_date"] == "2026-05-21"


def test_stir_event_links_post_only_when_chosen(
    db_conn: sqlite3.Connection,
) -> None:
    pid = _seed_post(db_conn)
    new_id = submit_stir_event(
        db_conn,
        {
            "event_category": "feedback",
            "event_type": "unprompted_feedback",
            "occurred_at_utc": "2026-05-21T18:00:00Z",
            "attribution_method": "self_reported",
            "source_data_quality": "manual",
            "referring_post_id": pid,
        },
    )
    row = db_conn.execute(
        "SELECT * FROM stir_conversion_events WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["referring_post_id"] == pid


# ---------------------------------------------------------------------------
# Tester
# ---------------------------------------------------------------------------

def test_tester_persists_self_reported_icp(db_conn: sqlite3.Connection) -> None:
    new_id = submit_tester(
        db_conn,
        {
            "alias": "tester_a",
            "first_seen_date": "2026-05-21",
            "status": "downloaded",
            "self_reported_icp": True,
            "is_working_parent_home_cook": 1,
            "icp_notes": "told me her kids are 4 and 7",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM stir_testers WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["alias"] == "tester_a"
    assert row["status"] == "downloaded"
    assert row["is_working_parent_home_cook"] == 1


def test_tester_persists_with_null_icp_when_unknown(
    db_conn: sqlite3.Connection,
) -> None:
    new_id = submit_tester(
        db_conn,
        {
            "alias": "tester_b",
            "first_seen_date": "2026-05-21",
            "status": "lead",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM stir_testers WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["is_working_parent_home_cook"] is None


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------

def test_weekly_review_persists_counterfactual(db_conn: sqlite3.Connection) -> None:
    new_id = submit_weekly_review(
        db_conn,
        {
            "week_start_date": "2026-05-18",
            "week_end_date": "2026-05-24",
            "followers_start": 100,
            "followers_end": 112,
            "posts_shipped": 7,
            "replies_shipped": 80,
            "reply_sessions_completed": 5,
            "counterfactual_note": "can't see App Store source of downloads",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM weekly_reviews WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["counterfactual_note"].startswith("can't see App Store")
    # Auto-derived follower_delta when both endpoints provided.
    assert row["follower_delta"] == 12


def test_weekly_review_upserts(db_conn: sqlite3.Connection) -> None:
    first = submit_weekly_review(
        db_conn,
        {
            "week_start_date": "2026-05-18",
            "week_end_date": "2026-05-24",
            "counterfactual_note": "v1",
        },
    )
    second = submit_weekly_review(
        db_conn,
        {
            "week_start_date": "2026-05-18",
            "week_end_date": "2026-05-24",
            "counterfactual_note": "v2 — revised",
        },
    )
    assert first == second
    row = db_conn.execute(
        "SELECT * FROM weekly_reviews WHERE id = ?", (first,)
    ).fetchone()
    assert row["counterfactual_note"] == "v2 — revised"


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------

def test_needs_tagging_queue_lists_unclassified_posts(
    db_conn: sqlite3.Connection,
) -> None:
    pid1 = _seed_post(db_conn, text="post a", x_post_id="11")
    pid2 = _seed_post(db_conn, text="post b", x_post_id="22")
    submit_classification(
        db_conn,
        {"post_id": pid1, "pillar": "stir", "audience": "icp", "cta": "none"},
    )
    rows = needs_tagging(db_conn)
    ids = [r["id"] for r in rows]
    assert pid1 not in ids
    assert pid2 in ids


def test_needs_post_id_queue_lists_only_null_x_post_id(
    db_conn: sqlite3.Connection,
) -> None:
    pid_confirmed = _seed_post(db_conn, text="has id", x_post_id="333")
    pid_needs = submit_post(
        db_conn,
        {
            "type": "reply",
            "text": "no id",
            "posted_at_utc": "2026-05-21T15:00:00Z",
        },
    )
    rows = needs_post_id(db_conn)
    ids = [r["id"] for r in rows]
    assert pid_confirmed not in ids
    assert pid_needs in ids
