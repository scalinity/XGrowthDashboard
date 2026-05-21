"""Weekly review form — spec.md §15.5 + §14.6.

Inserts/updates ``weekly_reviews`` rows keyed on ``week_start_date``
(``UNIQUE`` per the schema). Submit is **blocked** when
``counterfactual_note`` is empty AND the ``counterfactual_required`` setting
is true (which it is by default per §14.6). This is the §22 edge case
"Weekly review export with no counterfactual note".

Display of the auto-filled quantitative summary (followers_start/end,
posts_shipped, etc.) is a Phase 3 concern — this phase just accepts those
values from the form payload and writes them. Phase 3 will fill them in
automatically from `v_account_daily` / `v_daily_reps`.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from datetime import timedelta
from typing import Any

import streamlit as st

from app.forms import FormError, get_setting


def _previous_monday() -> _date_t:
    today = _date_t.today()
    # weekday(): Monday is 0
    return today - timedelta(days=today.weekday())


def _validate(payload: dict[str, Any], *, counterfactual_required: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    for f in ("week_start_date", "week_end_date"):
        v = payload.get(f)
        if not v:
            errors[f] = "Required."
        else:
            try:
                _date_t.fromisoformat(str(v))
            except ValueError:
                errors[f] = "Must be ISO-8601."
    if (
        not errors
        and payload["week_end_date"] <= payload["week_start_date"]
    ):
        errors["week_end_date"] = "Must be after week_start_date."
    if counterfactual_required:
        note = payload.get("counterfactual_note")
        if not note or not isinstance(note, str) or not note.strip():
            errors["counterfactual_note"] = (
                "Required — what couldn't this tool measure this week? "
                "(§14.6, §22 edge case)."
            )
    for f in (
        "followers_start", "followers_end", "follower_delta",
        "posts_shipped", "replies_shipped", "reply_sessions_completed",
        "daily_reps_days_completed", "downloads", "qualified_icp_testers",
    ):
        v = payload.get(f)
        if v is None:
            continue
        if not isinstance(v, int):
            errors[f] = "Must be an integer."
    return errors


def submit_weekly_review(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Upsert a ``weekly_reviews`` row. Returns the row id.

    Blocks when ``counterfactual_required = True`` (default) and the
    counterfactual note is empty.
    """
    counterfactual_required = bool(
        get_setting(conn, "counterfactual_required", True)
    )
    errors = _validate(payload, counterfactual_required=counterfactual_required)
    if errors:
        raise FormError(
            "Weekly review validation failed.", field_errors=errors
        )

    existing = conn.execute(
        "SELECT id FROM weekly_reviews WHERE week_start_date = ?",
        (payload["week_start_date"],),
    ).fetchone()

    follower_delta = payload.get("follower_delta")
    if (
        follower_delta is None
        and payload.get("followers_start") is not None
        and payload.get("followers_end") is not None
    ):
        follower_delta = int(payload["followers_end"]) - int(payload["followers_start"])

    if existing is not None:
        conn.execute(
            """
            UPDATE weekly_reviews
               SET week_end_date = ?,
                   followers_start = ?,
                   followers_end = ?,
                   follower_delta = ?,
                   posts_shipped = ?,
                   replies_shipped = ?,
                   reply_sessions_completed = ?,
                   daily_reps_days_completed = ?,
                   best_post_id = ?,
                   worst_post_id = ?,
                   strongest_pillar = ?,
                   weakest_pillar = ?,
                   downloads = ?,
                   qualified_icp_testers = ?,
                   what_moved = ?,
                   what_got_stuck = ?,
                   lesson = ?,
                   next_week_experiment = ?,
                   counterfactual_note = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                payload["week_end_date"],
                payload.get("followers_start"),
                payload.get("followers_end"),
                follower_delta,
                int(payload.get("posts_shipped") or 0),
                int(payload.get("replies_shipped") or 0),
                int(payload.get("reply_sessions_completed") or 0),
                int(payload.get("daily_reps_days_completed") or 0),
                payload.get("best_post_id"),
                payload.get("worst_post_id"),
                (payload.get("strongest_pillar") or None),
                (payload.get("weakest_pillar") or None),
                int(payload.get("downloads") or 0),
                int(payload.get("qualified_icp_testers") or 0),
                (payload.get("what_moved") or None),
                (payload.get("what_got_stuck") or None),
                (payload.get("lesson") or None),
                (payload.get("next_week_experiment") or None),
                payload.get("counterfactual_note"),
                int(existing["id"]),
            ),
        )
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO weekly_reviews (
            week_start_date, week_end_date,
            followers_start, followers_end, follower_delta,
            posts_shipped, replies_shipped, reply_sessions_completed,
            daily_reps_days_completed,
            best_post_id, worst_post_id,
            strongest_pillar, weakest_pillar,
            downloads, qualified_icp_testers,
            what_moved, what_got_stuck, lesson,
            next_week_experiment, counterfactual_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["week_start_date"], payload["week_end_date"],
            payload.get("followers_start"), payload.get("followers_end"),
            follower_delta,
            int(payload.get("posts_shipped") or 0),
            int(payload.get("replies_shipped") or 0),
            int(payload.get("reply_sessions_completed") or 0),
            int(payload.get("daily_reps_days_completed") or 0),
            payload.get("best_post_id"), payload.get("worst_post_id"),
            (payload.get("strongest_pillar") or None),
            (payload.get("weakest_pillar") or None),
            int(payload.get("downloads") or 0),
            int(payload.get("qualified_icp_testers") or 0),
            (payload.get("what_moved") or None),
            (payload.get("what_got_stuck") or None),
            (payload.get("lesson") or None),
            (payload.get("next_week_experiment") or None),
            payload.get("counterfactual_note"),
        ),
    )
    return int(cursor.lastrowid)


def render(conn: sqlite3.Connection, *, key_prefix: str = "weekly_review") -> None:
    """Streamlit fragment: weekly review form."""
    counterfactual_required = bool(
        get_setting(conn, "counterfactual_required", True)
    )

    st.subheader("Weekly review")
    st.caption(
        "Spec §15.5. Phase 3 auto-fills the quantitative summary; this phase "
        "is a manual form. "
        + ("**Counterfactual note required** (§14.6)." if counterfactual_required
           else "Counterfactual note optional (setting disabled).")
    )

    monday = _previous_monday()
    sunday = monday + timedelta(days=6)
    col_s, col_e = st.columns(2)
    week_start = col_s.date_input(
        "Week start (Monday)", value=monday, key=f"{key_prefix}_start"
    )
    week_end = col_e.date_input(
        "Week end (Sunday)", value=sunday, key=f"{key_prefix}_end"
    )

    existing = conn.execute(
        "SELECT * FROM weekly_reviews WHERE week_start_date = ?",
        (week_start.isoformat(),),
    ).fetchone()
    if existing is not None:
        st.info(
            f"Existing review for week starting {week_start.isoformat()} "
            f"(id={existing['id']}). Saving updates it."
        )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        col_fs, col_fe = st.columns(2)
        followers_start = col_fs.number_input(
            "Followers at week start (optional)", min_value=0, step=1,
            value=int(existing["followers_start"]) if existing and existing["followers_start"] else 0,
            key=f"{key_prefix}_fs",
        )
        followers_end = col_fe.number_input(
            "Followers at week end (optional)", min_value=0, step=1,
            value=int(existing["followers_end"]) if existing and existing["followers_end"] else 0,
            key=f"{key_prefix}_fe",
        )

        col_p, col_r, col_s2 = st.columns(3)
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
        sessions = col_s2.number_input(
            "Reply sessions", min_value=0, step=1,
            value=int(existing["reply_sessions_completed"]) if existing else 0,
            key=f"{key_prefix}_sessions",
        )

        col_d, col_i = st.columns(2)
        downloads = col_d.number_input(
            "Stir downloads (manual count)", min_value=0, step=1,
            value=int(existing["downloads"]) if existing else 0,
            key=f"{key_prefix}_dl",
        )
        qualified = col_i.number_input(
            "Qualified ICP testers", min_value=0, step=1,
            value=int(existing["qualified_icp_testers"]) if existing else 0,
            key=f"{key_prefix}_icp",
        )

        what_moved = st.text_area(
            "What moved this week?",
            value=(existing["what_moved"] or "") if existing else "",
            key=f"{key_prefix}_moved", height=80,
        )
        what_got_stuck = st.text_area(
            "What got stuck?",
            value=(existing["what_got_stuck"] or "") if existing else "",
            key=f"{key_prefix}_stuck", height=80,
        )
        lesson = st.text_area(
            "One-sentence lesson",
            value=(existing["lesson"] or "") if existing else "",
            key=f"{key_prefix}_lesson", height=60,
        )
        next_experiment = st.text_area(
            "Next week's experiment",
            value=(existing["next_week_experiment"] or "") if existing else "",
            key=f"{key_prefix}_next", height=80,
        )
        counterfactual = st.text_area(
            "Counterfactual note — what couldn't this tool measure?"
            + (" *" if counterfactual_required else ""),
            value=(existing["counterfactual_note"] or "") if existing else "",
            key=f"{key_prefix}_counterfactual", height=100,
            help="§14.6 — required to save (toggleable via Settings).",
        )

        submitted = st.form_submit_button("Save weekly review", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "week_start_date": week_start.isoformat(),
            "week_end_date": week_end.isoformat(),
            "followers_start": int(followers_start) or None,
            "followers_end": int(followers_end) or None,
            "posts_shipped": int(posts_shipped),
            "replies_shipped": int(replies_shipped),
            "reply_sessions_completed": int(sessions),
            "downloads": int(downloads),
            "qualified_icp_testers": int(qualified),
            "what_moved": what_moved,
            "what_got_stuck": what_got_stuck,
            "lesson": lesson,
            "next_week_experiment": next_experiment,
            "counterfactual_note": counterfactual.strip() or None,
        }
        try:
            new_id = submit_weekly_review(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Weekly review #{new_id} saved.")
