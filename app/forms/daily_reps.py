"""Daily reps form — spec.md §14.1 + §15.2.

Upserts a ``daily_activity`` row keyed on ``activity_date``. ``daily_activity``
uses ``activity_date`` as the PK per §10.2 so a re-submit for the same day
replaces the prior values (this is the "edit your morning numbers" pattern,
not an append-only audit trail like snapshots).

Targets (``planned_posts``, ``planned_replies``, ``planned_quotes``,
``minimum_reps_completed``) are derived from the seeded settings keys
``daily_post_target``, ``daily_reply_target``, ``daily_reply_session_target``
so editing settings later doesn't silently invalidate already-recorded rows.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from typing import Any

from app._optional_streamlit import st

from app.forms import FormError, get_setting


def _validate(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not payload.get("activity_date"):
        errors["activity_date"] = "Required."
    else:
        try:
            _date_t.fromisoformat(str(payload["activity_date"]))
        except ValueError:
            errors["activity_date"] = "Must be ISO-8601 (YYYY-MM-DD)."
    for field in (
        "posts_shipped",
        "replies_shipped",
        "quotes_shipped",
        "reply_sessions_completed",
        "high_quality_reply_targets_found",
    ):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, int) or value < 0:
            errors[field] = "Must be a non-negative integer."
    time_spent = payload.get("time_spent_minutes")
    if time_spent is not None and (not isinstance(time_spent, int) or time_spent < 0):
        errors["time_spent_minutes"] = "Must be a non-negative integer."
    return errors


def submit_daily_activity(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    """Upsert a ``daily_activity`` row. Returns the activity_date PK."""
    errors = _validate(payload)
    if errors:
        raise FormError("Daily reps validation failed.", field_errors=errors)

    planned_posts = int(get_setting(conn, "daily_post_target", 0) or 0)
    planned_replies = int(get_setting(conn, "daily_reply_target", 0) or 0)
    # quotes have no separate target; default to 0.
    planned_quotes = 0
    reply_session_target = int(get_setting(conn, "daily_reply_session_target", 0) or 0)

    posts_shipped = int(payload.get("posts_shipped") or 0)
    replies_shipped = int(payload.get("replies_shipped") or 0)
    quotes_shipped = int(payload.get("quotes_shipped") or 0)
    reply_sessions = int(payload.get("reply_sessions_completed") or 0)
    hq_targets = int(payload.get("high_quality_reply_targets_found") or 0)

    # "Minimum reps completed" is the §14.1 daily green/red signal: did Daniel
    # hit BOTH the post target AND the reply target AND the reply-session
    # target? Stored as 0/1 per the schema CHECK.
    min_reps = int(
        posts_shipped >= planned_posts
        and replies_shipped >= planned_replies
        and reply_sessions >= reply_session_target
    )

    conn.execute(
        """
        INSERT INTO daily_activity (
            activity_date,
            planned_posts, planned_replies, planned_quotes,
            posts_shipped, replies_shipped, quotes_shipped,
            high_quality_reply_targets_found,
            reply_sessions_completed,
            minimum_reps_completed,
            time_spent_minutes,
            manual_actions_count, api_actions_count,
            avoidance_notes, daily_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(activity_date) DO UPDATE SET
            planned_posts = excluded.planned_posts,
            planned_replies = excluded.planned_replies,
            planned_quotes = excluded.planned_quotes,
            posts_shipped = excluded.posts_shipped,
            replies_shipped = excluded.replies_shipped,
            quotes_shipped = excluded.quotes_shipped,
            high_quality_reply_targets_found = excluded.high_quality_reply_targets_found,
            reply_sessions_completed = excluded.reply_sessions_completed,
            minimum_reps_completed = excluded.minimum_reps_completed,
            time_spent_minutes = excluded.time_spent_minutes,
            manual_actions_count = excluded.manual_actions_count,
            api_actions_count = excluded.api_actions_count,
            avoidance_notes = excluded.avoidance_notes,
            daily_note = excluded.daily_note,
            updated_at = datetime('now')
        """,
        (
            payload["activity_date"],
            planned_posts, planned_replies, planned_quotes,
            posts_shipped, replies_shipped, quotes_shipped,
            hq_targets,
            reply_sessions,
            min_reps,
            payload.get("time_spent_minutes"),
            payload.get("manual_actions_count"),
            payload.get("api_actions_count"),
            (payload.get("avoidance_notes") or None),
            (payload.get("daily_note") or None),
        ),
    )
    return str(payload["activity_date"])


def render(conn: sqlite3.Connection, *, key_prefix: str = "daily_reps") -> None:
    """Streamlit fragment: daily reps form."""
    st.subheader("Daily reps")
    st.caption(
        "Spec §14.1 / §15.2. PK is `activity_date` — re-submitting for the "
        "same day overwrites (not append-only like snapshots)."
    )

    activity_date = st.date_input(
        "Date", value=_date_t.today(), key=f"{key_prefix}_date"
    )

    existing = conn.execute(
        "SELECT * FROM daily_activity WHERE activity_date = ?",
        (activity_date.isoformat(),),
    ).fetchone()
    if existing is not None:
        st.info(
            f"Existing row for {activity_date.isoformat()}: posts="
            f"{existing['posts_shipped']}, replies={existing['replies_shipped']}, "
            f"sessions={existing['reply_sessions_completed']}. Saving replaces it."
        )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        col_p, col_r, col_q = st.columns(3)
        posts_shipped = col_p.number_input(
            "Posts shipped", min_value=0, step=1,
            value=int(existing["posts_shipped"]) if existing else 0,
            key=f"{key_prefix}_posts",
        )
        replies_shipped = col_r.number_input(
            "Replies shipped", min_value=0, step=1,
            value=int(existing["replies_shipped"]) if existing else 0,
            key=f"{key_prefix}_replies",
        )
        quotes_shipped = col_q.number_input(
            "Quotes shipped", min_value=0, step=1,
            value=int(existing["quotes_shipped"]) if existing else 0,
            key=f"{key_prefix}_quotes",
        )
        col_s, col_t = st.columns(2)
        reply_sessions = col_s.number_input(
            "Reply sessions completed", min_value=0, step=1,
            value=int(existing["reply_sessions_completed"]) if existing else 0,
            key=f"{key_prefix}_sessions",
        )
        hq_targets = col_t.number_input(
            "High-quality reply targets found", min_value=0, step=1,
            value=int(existing["high_quality_reply_targets_found"]) if existing else 0,
            key=f"{key_prefix}_targets",
        )
        time_spent = st.number_input(
            "Time spent (minutes, optional)", min_value=0, step=5,
            value=int(existing["time_spent_minutes"]) if existing and existing["time_spent_minutes"] else 0,
            key=f"{key_prefix}_time",
        )
        avoidance_notes = st.text_area(
            "Avoidance notes (optional)",
            value=(existing["avoidance_notes"] or "") if existing else "",
            key=f"{key_prefix}_avoid", height=70,
        )
        daily_note = st.text_area(
            "Daily note (optional)",
            value=(existing["daily_note"] or "") if existing else "",
            key=f"{key_prefix}_note", height=70,
        )
        submitted = st.form_submit_button("Save daily reps", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "activity_date": activity_date.isoformat(),
            "posts_shipped": int(posts_shipped),
            "replies_shipped": int(replies_shipped),
            "quotes_shipped": int(quotes_shipped),
            "reply_sessions_completed": int(reply_sessions),
            "high_quality_reply_targets_found": int(hq_targets),
            "time_spent_minutes": int(time_spent) or None,
            "avoidance_notes": avoidance_notes,
            "daily_note": daily_note,
        }
        try:
            day = submit_daily_activity(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Daily reps saved for {day}.")
