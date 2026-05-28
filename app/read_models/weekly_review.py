"""Read model for the weekly review view (§31.10)."""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from datetime import timedelta as _timedelta
from typing import Any

from app.components.badges.confidence_label import ui_label_for_db_label
from app.forms import get_setting

def build_weekly_review_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Weekly Review view (§14.6) needs.

    Mirrors the Streamlit page: auto-filled summary metrics for this week,
    existing review row, counterfactual_required setting, and past review
    history — all computed server-side (§31.10).
    """


    today = _date_t.today()
    # Monday-anchored week.
    week_start = today - _timedelta(days=today.weekday())
    week_end = week_start + _timedelta(days=6)

    # 1. Summary metrics (same SQL as _summary_for_week in 6_Weekly_Review.py).
    ws_iso = week_start.isoformat()
    we_iso = week_end.isoformat()

    foll_row = conn.execute(
        """
        SELECT
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date >= ? AND snapshot_date <= ?
              ORDER BY snapshot_date ASC LIMIT 1) AS followers_start,
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date <= ? AND snapshot_date >= ?
              ORDER BY snapshot_date DESC LIMIT 1) AS followers_end
        """,
        (ws_iso, we_iso, we_iso, ws_iso),
    ).fetchone()
    followers_start = foll_row["followers_start"]
    followers_end = foll_row["followers_end"]
    follower_delta = (
        (followers_end or 0) - (followers_start or 0)
        if followers_start is not None and followers_end is not None
        else None
    )

    reps = conn.execute(
        """
        SELECT
            COALESCE(SUM(posts_shipped), 0)            AS posts_shipped,
            COALESCE(SUM(replies_shipped), 0)          AS replies_shipped,
            COALESCE(SUM(reply_sessions_completed), 0) AS reply_sessions_completed,
            COALESCE(SUM(minimum_reps_completed), 0)   AS daily_reps_days_completed
        FROM v_daily_reps
        WHERE activity_date BETWEEN ? AND ?
        """,
        (ws_iso, we_iso),
    ).fetchone()

    funnel = conn.execute(
        """
        SELECT
            COALESCE(SUM(downloads), 0)             AS downloads,
            COALESCE(SUM(qualified_icp_testers), 0) AS qualified_icp_testers
        FROM v_funnel_daily
        WHERE event_date BETWEEN ? AND ?
        """,
        (ws_iso, we_iso),
    ).fetchone()

    # Strongest pillar candidate.
    lanes = conn.execute(
        """
        SELECT pillar, median_impressions, confidence_label
        FROM v_lane_performance
        ORDER BY median_impressions DESC NULLS LAST
        """
    ).fetchall()
    eligible = [
        r for r in lanes
        if ui_label_for_db_label(r["confidence_label"]) in {"tentative", "confident"}
    ]
    strongest_pillar_candidate = (
        f"{eligible[0]['pillar']} ({eligible[0]['confidence_label']})"
        if eligible
        else None
    )

    summary = {
        "follower_delta": follower_delta,
        "posts_shipped": int(reps["posts_shipped"]),
        "replies_shipped": int(reps["replies_shipped"]),
        "reply_sessions_completed": int(reps["reply_sessions_completed"]),
        "daily_reps_days_completed": int(reps["daily_reps_days_completed"]),
        "downloads": int(funnel["downloads"]),
        "qualified_icp_testers": int(funnel["qualified_icp_testers"]),
        "strongest_pillar_candidate": strongest_pillar_candidate,
    }

    # 2. Existing review for this week.
    existing_row = conn.execute(
        "SELECT * FROM weekly_reviews WHERE week_start_date = ?",
        (ws_iso,),
    ).fetchone()
    existing_review = dict(existing_row) if existing_row else None

    # 3. counterfactual_required setting.
    counterfactual_required = bool(get_setting(conn, "counterfactual_required", True))

    # 4. Past reviews history.
    history_rows = conn.execute(
        """
        SELECT
            id, week_start_date, week_end_date, follower_delta,
            posts_shipped, replies_shipped, downloads,
            counterfactual_note, lesson, what_moved, what_got_stuck,
            next_week_experiment, qualified_icp_testers,
            reply_sessions_completed, daily_reps_days_completed
        FROM weekly_reviews
        ORDER BY week_start_date DESC
        """
    ).fetchall()
    past_reviews = [dict(r) for r in history_rows]

    return {
        "slice": "weekly_review",
        "week_start": ws_iso,
        "week_end": we_iso,
        "summary": summary,
        "existing_review": existing_review,
        "counterfactual_required": counterfactual_required,
        "past_reviews": past_reviews,
    }



