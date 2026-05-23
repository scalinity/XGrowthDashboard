"""v_daily_reps §29.9 extension — the four new columns aggregate correctly."""

from __future__ import annotations

import sqlite3


def _today(conn: sqlite3.Connection) -> str:
    """P6R-35: read 'today' from SQLite's ``date('now')`` (UTC) so the
    seed date matches the view's UTC-based cutoffs. Python's
    ``date.today()`` returns LOCAL date — near UTC midnight the two
    differ by a day and the view's filters drop the seeded daily_activity
    row, causing the test to fail at certain wall-clock times."""
    return conn.execute("SELECT date('now')").fetchone()[0]


def _today_row(conn: sqlite3.Connection):
    """Return today's v_daily_reps row, creating the daily_activity row if missing."""
    today_str = _today(conn)
    conn.execute(
        "INSERT OR IGNORE INTO daily_activity (activity_date) VALUES (?)",
        (today_str,),
    )
    return conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = ?", (today_str,)
    ).fetchone()


def test_v_daily_reps_has_the_four_new_columns(db_conn: sqlite3.Connection):
    row = _today_row(db_conn)
    assert "candidates_reviewed_today" in row.keys()
    assert "high_engagement_replies_shipped" in row.keys()
    assert "icp_intent_replies_shipped" in row.keys()
    assert "average_engagement_surface_of_posted" in row.keys()


def test_candidates_reviewed_today_counts_distinct_rows(db_conn: sqlite3.Connection):
    # Three rows discovered today plus one expired today plus one from yesterday.
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, target_author_handle) "
        "VALUES ('manual','https://x.com/x/status/1','x')"
    )
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, target_author_handle) "
        "VALUES ('manual','https://x.com/x/status/2','x')"
    )
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, status, expired_at_utc) "
        "VALUES ('manual','https://x.com/x/status/3','x','expired', datetime('now'))"
    )
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, discovered_at_utc, last_checked_at_utc) "
        "VALUES ('manual','https://x.com/x/status/4','x', "
        "datetime('now','-2 days'), datetime('now','-2 days'))"
    )
    row = _today_row(db_conn)
    # 3 rows touched today (1, 2 from discovered; 3 from expired_at_utc).
    assert int(row["candidates_reviewed_today"]) == 3


def test_high_engagement_replies_shipped_filters_by_engagement_score(db_conn: sqlite3.Connection):
    # Two reply_targets — one high engagement (score 2), one low (score 0).
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, engagement_surface_score) "
        "VALUES ('manual','https://x.com/x/status/10','x', 2)"
    )
    rt_high = db_conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, engagement_surface_score) "
        "VALUES ('manual','https://x.com/x/status/11','x', 0)"
    )
    rt_low = db_conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]
    # Two posts created today, both linked.
    db_conn.execute(
        "INSERT INTO posts (created_at_utc, created_date, text, type, "
        "posted_via, manual_confirmation_status, in_reply_to_reply_target_id) "
        "VALUES (datetime('now'), date('now'), 'r1', 'reply', 'manual', 'needs_metrics', ?)",
        (rt_high,),
    )
    db_conn.execute(
        "INSERT INTO posts (created_at_utc, created_date, text, type, "
        "posted_via, manual_confirmation_status, in_reply_to_reply_target_id) "
        "VALUES (datetime('now'), date('now'), 'r2', 'reply', 'manual', 'needs_metrics', ?)",
        (rt_low,),
    )
    row = _today_row(db_conn)
    assert int(row["high_engagement_replies_shipped"]) == 1


def test_icp_intent_replies_shipped_counts_posts_with_intent(db_conn: sqlite3.Connection):
    db_conn.execute(
        "INSERT INTO posts (created_at_utc, created_date, text, type, "
        "posted_via, manual_confirmation_status, reply_intent) "
        "VALUES (datetime('now'), date('now'), 'r1', 'reply', 'manual', "
        "'needs_metrics', 'icp_discovery')"
    )
    db_conn.execute(
        "INSERT INTO posts (created_at_utc, created_date, text, type, "
        "posted_via, manual_confirmation_status, reply_intent) "
        "VALUES (datetime('now'), date('now'), 'r2', 'reply', 'manual', "
        "'needs_metrics', 'relationship')"
    )
    row = _today_row(db_conn)
    assert int(row["icp_intent_replies_shipped"]) == 1


def test_average_engagement_surface_of_posted_returns_mean(db_conn: sqlite3.Connection):
    # Two reply_targets with surface scores 2 and 0.
    for url, score in (("https://x.com/x/status/20", 2), ("https://x.com/x/status/21", 0)):
        db_conn.execute(
            "INSERT INTO reply_targets (discovered_via, target_post_url, "
            "target_author_handle, engagement_surface_score) "
            "VALUES ('manual', ?, 'x', ?)",
            (url, score),
        )
        rt_id = db_conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]
        db_conn.execute(
            "INSERT INTO posts (created_at_utc, created_date, text, type, "
            "posted_via, manual_confirmation_status, in_reply_to_reply_target_id) "
            "VALUES (datetime('now'), date('now'), 'r', 'reply', 'manual', "
            "'needs_metrics', ?)",
            (rt_id,),
        )
    row = _today_row(db_conn)
    avg = float(row["average_engagement_surface_of_posted"])
    assert abs(avg - 1.0) < 1e-9


def test_average_engagement_surface_is_null_when_no_replies(db_conn: sqlite3.Connection):
    row = _today_row(db_conn)
    assert row["average_engagement_surface_of_posted"] is None
