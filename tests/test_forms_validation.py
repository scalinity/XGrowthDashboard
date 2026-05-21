"""Form-level validation tests — spec.md §13 / §15 / §22.

Each form's pure submit function should reject bad input with a structured
``FormError`` before touching the DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.forms import FormError
from app.forms.classify import submit_classification
from app.forms.correction import submit_correction
from app.forms.daily_reps import submit_daily_activity
from app.forms.post_log import submit_post
from app.forms.snapshot import submit_snapshot
from app.forms.stir_event import submit_stir_event
from app.forms.stir_tester import submit_tester
from app.forms.weekly_review import submit_weekly_review


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def test_snapshot_rejects_negative_counts(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_snapshot(
            db_conn,
            {
                "snapshot_date": "2026-05-20",
                "username": "dannyscalant",
                "profile_url": "https://x.com/dannyscalant",
                "followers_count": -1,
                "following_count": 100,
                "post_count": 50,
                "listed_count": 0,
                "baseline_followers": 61,
            },
        )
    assert "followers_count" in exc.value.field_errors


def test_snapshot_rejects_missing_required_fields(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_snapshot(db_conn, {})
    # Multiple errors expected
    fields = exc.value.field_errors.keys()
    assert "snapshot_date" in fields
    assert "username" in fields
    assert "profile_url" in fields


def test_snapshot_rejects_bad_date_format(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_snapshot(
            db_conn,
            {
                "snapshot_date": "05/20/2026",
                "username": "dannyscalant",
                "profile_url": "https://x.com/dannyscalant",
                "followers_count": 100,
                "following_count": 100,
                "post_count": 50,
                "listed_count": 0,
                "baseline_followers": 61,
            },
        )
    assert "snapshot_date" in exc.value.field_errors


def test_snapshot_refuses_duplicate_date(db_conn: sqlite3.Connection) -> None:
    base = {
        "snapshot_date": "2026-05-21",
        "username": "dannyscalant",
        "profile_url": "https://x.com/dannyscalant",
        "followers_count": 100,
        "following_count": 100,
        "post_count": 50,
        "listed_count": 0,
        "baseline_followers": 61,
    }
    submit_snapshot(db_conn, base)
    with pytest.raises(FormError) as exc:
        submit_snapshot(db_conn, base)
    assert "snapshot_date" in exc.value.field_errors
    assert "duplicate_snapshot_id" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Post log
# ---------------------------------------------------------------------------

def test_post_log_rejects_invalid_type(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_post(db_conn, {"type": "tweet", "text": "hi"})
    assert "type" in exc.value.field_errors


def test_post_log_rejects_missing_text(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_post(db_conn, {"type": "post", "text": ""})
    assert "text" in exc.value.field_errors


def test_post_log_rejects_malformed_timestamp(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_post(
            db_conn,
            {"type": "post", "text": "hi", "posted_at_utc": "not-a-date"},
        )
    assert "posted_at_utc" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _make_post(db_conn: sqlite3.Connection) -> int:
    return submit_post(
        db_conn,
        {
            "type": "post",
            "text": "Hello world",
            "posted_at_utc": "2026-05-21T15:00:00Z",
            "x_post_id": "1000000001",
        },
    )


def test_classification_rejects_bad_pillar(db_conn: sqlite3.Connection) -> None:
    pid = _make_post(db_conn)
    with pytest.raises(FormError) as exc:
        submit_classification(
            db_conn,
            {"post_id": pid, "pillar": "growth", "audience": "icp", "cta": "none"},
        )
    assert "pillar" in exc.value.field_errors


def test_classification_rejects_bad_audience(db_conn: sqlite3.Connection) -> None:
    pid = _make_post(db_conn)
    with pytest.raises(FormError) as exc:
        submit_classification(
            db_conn,
            {"post_id": pid, "pillar": "stir", "audience": "builders", "cta": "none"},
        )
    assert "audience" in exc.value.field_errors


def test_classification_refuses_overwrite_by_default(
    db_conn: sqlite3.Connection,
) -> None:
    pid = _make_post(db_conn)
    submit_classification(
        db_conn,
        {"post_id": pid, "pillar": "stir", "audience": "icp", "cta": "ask"},
    )
    with pytest.raises(FormError) as exc:
        submit_classification(
            db_conn,
            {"post_id": pid, "pillar": "build", "audience": "icp", "cta": "ask"},
        )
    assert "post_id" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Daily reps
# ---------------------------------------------------------------------------

def test_daily_reps_rejects_negative_counts(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_daily_activity(
            db_conn,
            {
                "activity_date": "2026-05-21",
                "posts_shipped": -1,
                "replies_shipped": 10,
                "quotes_shipped": 0,
                "reply_sessions_completed": 1,
                "high_quality_reply_targets_found": 0,
            },
        )
    assert "posts_shipped" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Stir event
# ---------------------------------------------------------------------------

def test_stir_event_rejects_icp_when_not_self_reported(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(FormError) as exc:
        submit_stir_event(
            db_conn,
            {
                "event_category": "acquisition",
                "event_type": "tester_install",
                "occurred_at_utc": "2026-05-21T15:00:00Z",
                "attribution_method": "inferred",
                "source_data_quality": "manual",
                "is_likely_icp": 1,
            },
        )
    assert "is_likely_icp" in exc.value.field_errors


def test_stir_event_rejects_invalid_category(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_stir_event(
            db_conn,
            {
                "event_category": "conversion",
                "event_type": "tester_install",
                "occurred_at_utc": "2026-05-21T15:00:00Z",
                "attribution_method": "unknown",
                "source_data_quality": "manual",
            },
        )
    assert "event_category" in exc.value.field_errors


def test_stir_event_requires_event_type(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_stir_event(
            db_conn,
            {
                "event_category": "acquisition",
                "event_type": "  ",
                "occurred_at_utc": "2026-05-21T15:00:00Z",
                "attribution_method": "unknown",
                "source_data_quality": "manual",
            },
        )
    assert "event_type" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Tester
# ---------------------------------------------------------------------------

def test_tester_rejects_icp_when_not_self_reported(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(FormError) as exc:
        submit_tester(
            db_conn,
            {
                "alias": "tester_a",
                "first_seen_date": "2026-05-21",
                "status": "lead",
                "self_reported_icp": False,
                "is_working_parent_home_cook": 1,
            },
        )
    assert "is_working_parent_home_cook" in exc.value.field_errors


def test_tester_requires_alias(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_tester(
            db_conn,
            {
                "alias": "",
                "first_seen_date": "2026-05-21",
                "status": "lead",
            },
        )
    assert "alias" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------

def test_weekly_review_blocks_when_counterfactual_empty(
    db_conn: sqlite3.Connection,
) -> None:
    # Setting `counterfactual_required` is True by default in seed.
    with pytest.raises(FormError) as exc:
        submit_weekly_review(
            db_conn,
            {
                "week_start_date": "2026-05-18",
                "week_end_date": "2026-05-24",
                "counterfactual_note": "   ",
            },
        )
    assert "counterfactual_note" in exc.value.field_errors


def test_weekly_review_allows_empty_counterfactual_when_setting_off(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        "UPDATE settings SET value_json = 'false' WHERE key = 'counterfactual_required'"
    )
    new_id = submit_weekly_review(
        db_conn,
        {
            "week_start_date": "2026-05-18",
            "week_end_date": "2026-05-24",
            "counterfactual_note": None,
        },
    )
    assert isinstance(new_id, int) and new_id > 0


def test_weekly_review_rejects_end_before_start(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(FormError) as exc:
        submit_weekly_review(
            db_conn,
            {
                "week_start_date": "2026-05-24",
                "week_end_date": "2026-05-18",
                "counterfactual_note": "anything",
            },
        )
    assert "week_end_date" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Correction
# ---------------------------------------------------------------------------

def test_correction_rejects_uncorrectable_field(db_conn: sqlite3.Connection) -> None:
    # Pre-create a snapshot.
    new_id = submit_snapshot(
        db_conn,
        {
            "snapshot_date": "2026-05-20",
            "username": "dannyscalant",
            "profile_url": "https://x.com/dannyscalant",
            "followers_count": 100,
            "following_count": 100,
            "post_count": 50,
            "listed_count": 0,
            "baseline_followers": 61,
        },
    )
    with pytest.raises(FormError) as exc:
        submit_correction(
            db_conn,
            {
                "snapshot_id": new_id,
                "field_name": "snapshot_date",  # not in CORRECTABLE_FIELDS
                "new_value": "2026-05-21",
                "reason": "wrong date",
            },
        )
    assert "field_name" in exc.value.field_errors


def test_correction_requires_reason(db_conn: sqlite3.Connection) -> None:
    new_id = submit_snapshot(
        db_conn,
        {
            "snapshot_date": "2026-05-20",
            "username": "dannyscalant",
            "profile_url": "https://x.com/dannyscalant",
            "followers_count": 100,
            "following_count": 100,
            "post_count": 50,
            "listed_count": 0,
            "baseline_followers": 61,
        },
    )
    with pytest.raises(FormError) as exc:
        submit_correction(
            db_conn,
            {
                "snapshot_id": new_id,
                "field_name": "followers_count",
                "new_value": 105,
                "reason": "  ",
            },
        )
    assert "reason" in exc.value.field_errors
