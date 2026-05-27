"""Foreground X API activity sync for the Today cockpit.

This wraps the existing Phase 7 X API read jobs into one user-triggered action:
account snapshot, recent owned posts, post metrics, then daily reps reconciliation.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from typing import Any

from app.forms.daily_reps import submit_daily_activity
from app.jobs import post_metrics_refresh
from scripts import collect_account_snapshot, import_recent_posts

_POST_TYPES = ("standalone", "thread_root", "thread_child")
_API_POSTED_VIA = ("api", "xurl", "imported")
_POST_TYPE_PLACEHOLDERS = ",".join("?" for _ in _POST_TYPES)
_API_POSTED_VIA_PLACEHOLDERS = ",".join("?" for _ in _API_POSTED_VIA)


def _int_value(row: sqlite3.Row | None, key: str, default: int = 0) -> int:
    if row is None:
        return default
    value = row[key]
    return int(value or default)


def _max_existing(row: sqlite3.Row | None, key: str, value: int) -> int:
    return max(_int_value(row, key), int(value or 0))


def _counts_for_day(conn: sqlite3.Connection, activity_date: str) -> dict[str, int]:
    row = conn.execute(
        f"""
        SELECT
            COALESCE(
                SUM(CASE WHEN type IN ({_POST_TYPE_PLACEHOLDERS}) THEN 1 ELSE 0 END),
                0
            ) AS posts_shipped,
            COALESCE(SUM(CASE WHEN type = 'reply' THEN 1 ELSE 0 END), 0)
                AS replies_shipped,
            COALESCE(SUM(CASE WHEN type = 'quote' THEN 1 ELSE 0 END), 0)
                AS quotes_shipped,
            COALESCE(
                SUM(
                    CASE
                        WHEN posted_via IN ({_API_POSTED_VIA_PLACEHOLDERS}) THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS api_actions_count
        FROM posts
        WHERE created_date = ?
        """,
        (*_POST_TYPES, *_API_POSTED_VIA, activity_date),
    ).fetchone()
    return {
        "posts_shipped": int(row["posts_shipped"] or 0),
        "replies_shipped": int(row["replies_shipped"] or 0),
        "quotes_shipped": int(row["quotes_shipped"] or 0),
        "api_actions_count": int(row["api_actions_count"] or 0),
    }


def reconcile_daily_activity(
    conn: sqlite3.Connection, *, activity_date: str | None = None
) -> dict[str, Any]:
    """Make ``daily_activity`` reflect post rows already imported for a day."""
    day = activity_date or _date_t.today().isoformat()
    counts = _counts_for_day(conn, day)
    existing = conn.execute(
        "SELECT * FROM daily_activity WHERE activity_date = ?", (day,)
    ).fetchone()

    reply_sessions = _max_existing(
        existing,
        "reply_sessions_completed",
        1 if counts["replies_shipped"] > 0 else 0,
    )
    payload = {
        "activity_date": day,
        "posts_shipped": _max_existing(
            existing, "posts_shipped", counts["posts_shipped"]
        ),
        "replies_shipped": _max_existing(
            existing, "replies_shipped", counts["replies_shipped"]
        ),
        "quotes_shipped": _max_existing(
            existing, "quotes_shipped", counts["quotes_shipped"]
        ),
        "reply_sessions_completed": reply_sessions,
        "high_quality_reply_targets_found": _int_value(
            existing, "high_quality_reply_targets_found"
        ),
        "time_spent_minutes": existing["time_spent_minutes"] if existing else None,
        "manual_actions_count": existing["manual_actions_count"] if existing else None,
        "api_actions_count": _max_existing(
            existing, "api_actions_count", counts["api_actions_count"]
        ),
        "avoidance_notes": existing["avoidance_notes"] if existing else None,
        "daily_note": existing["daily_note"] if existing else None,
    }
    submit_daily_activity(conn, payload)
    row = conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = ?", (day,)
    ).fetchone()
    return {
        "activity_date": day,
        "source_counts": counts,
        "daily_activity": dict(row) if row else payload,
    }


def run(conn: sqlite3.Connection, *, activity_date: str | None = None) -> dict[str, Any]:
    """Run the foreground X sync and return a UI-ready summary."""
    snapshot_summary = collect_account_snapshot.run(conn)
    import_summary = import_recent_posts.run(conn)
    metrics_summary = post_metrics_refresh.run(conn, batch_limit=100)
    activity_summary = reconcile_daily_activity(conn, activity_date=activity_date)

    warnings = [
        str(summary["error"])
        for summary in (snapshot_summary, import_summary, metrics_summary)
        if summary.get("error")
    ]
    return {
        "ok": not warnings,
        "snapshot": snapshot_summary,
        "import_posts": import_summary,
        "metrics": metrics_summary,
        "activity": activity_summary,
        "warnings": warnings,
    }
