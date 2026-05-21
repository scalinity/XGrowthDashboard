"""Backlog queues — "Needs tagging" and "Needs post ID" — spec.md §15.3 / §22.

Pure query helpers + Streamlit render fragments. Both queues are pull-based:
they list the rows that need attention, and clicking through routes to the
appropriate form pre-populated.
"""

from __future__ import annotations

import sqlite3

import streamlit as st

from app.forms import FormError
from app.forms.post_log import add_post_id


def needs_tagging(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    """Posts without a ``post_classifications`` row."""
    return list(
        conn.execute(
            """
            SELECT p.id, p.created_date, p.type, p.x_post_id,
                   substr(p.text, 1, 90) AS preview, p.created_at_utc
              FROM posts p
              LEFT JOIN post_classifications c ON c.post_id = p.id
             WHERE c.id IS NULL
             ORDER BY p.created_at_utc DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def needs_post_id(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    """Posts with ``x_post_id IS NULL`` — §22 'reply URL entered later'."""
    return list(
        conn.execute(
            """
            SELECT id, created_date, type, manual_confirmation_status,
                   substr(text, 1, 90) AS preview, created_at_utc
              FROM posts
             WHERE x_post_id IS NULL
             ORDER BY created_at_utc DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def render_needs_tagging(
    conn: sqlite3.Connection, *, key_prefix: str = "queue_tag"
) -> None:
    """List untagged posts; clicking 'Classify' sets a session-state hand-off
    that the Classification tab reads to pre-select the post.
    """
    rows = needs_tagging(conn)
    st.subheader(f"Needs tagging — {len(rows)} post(s)")
    if not rows:
        st.success("All posts are classified. 🎉")
        return
    for r in rows:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(
                f"**#{r['id']}** · {r['created_date']} · `{r['type']}` · "
                f"x_post_id={r['x_post_id'] or '(missing)'}\n\n{r['preview']}…"
            )
            if c2.button("Classify →", key=f"{key_prefix}_btn_{r['id']}"):
                st.session_state["preselected_classify_post_id"] = int(r["id"])
                st.session_state["manual_entry_active_tab"] = "Classify"
                st.rerun()


def render_needs_post_id(
    conn: sqlite3.Connection, *, key_prefix: str = "queue_pid"
) -> None:
    """List posts with no x_post_id; expose an inline 'Add ID' form per row."""
    rows = needs_post_id(conn)
    st.subheader(f"Needs post ID — {len(rows)} post(s)")
    if not rows:
        st.success("Every logged post has an X id. 🎉")
        return
    for r in rows:
        with st.container(border=True):
            st.markdown(
                f"**#{r['id']}** · {r['created_date']} · `{r['type']}` · "
                f"status=`{r['manual_confirmation_status']}`\n\n{r['preview']}…"
            )
            with st.form(key=f"{key_prefix}_form_{r['id']}", clear_on_submit=True):
                c1, c2 = st.columns([2, 3])
                x_id = c1.text_input(
                    "x_post_id", key=f"{key_prefix}_xid_{r['id']}"
                )
                url = c2.text_input(
                    "URL (optional)", key=f"{key_prefix}_url_{r['id']}"
                )
                if st.form_submit_button("Save ID", type="primary"):
                    try:
                        add_post_id(
                            conn, int(r["id"]), x_id.strip(), url.strip() or None
                        )
                    except FormError as exc:
                        st.error(str(exc))
                        for field, msg in exc.field_errors.items():
                            st.caption(f"• {field}: {msg}")
                        continue
                    st.success(f"Post #{r['id']} now confirmed.")
                    st.rerun()
