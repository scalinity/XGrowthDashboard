"""Manual post / reply / quote logging — spec.md §15.2.

Writes ``posts`` rows with ``posted_via = 'manual'``. Confirmation status is
derived from whether the user has the X post id yet:

- has ``x_post_id`` → ``manual_confirmation_status = 'confirmed'``.
- no ``x_post_id`` → ``manual_confirmation_status = 'needs_id'`` (§22 edge
  "Manual reply has no post ID").

Post type mapping (spec uses 'post' colloquially; schema uses 'standalone'):

- ``'post'``  → ``type = 'standalone'``
- ``'reply'`` → ``type = 'reply'``
- ``'quote'`` → ``type = 'quote'``

Thread roots/children are not part of the §15.2 form; they land via import or
the X API in later phases.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from datetime import datetime
from typing import Any

from app.forms import FormError, now_utc_iso

POST_TYPES_UI: tuple[str, ...] = ("post", "reply", "quote")
_TYPE_UI_TO_SCHEMA: dict[str, str] = {
    "post": "standalone",
    "reply": "reply",
    "quote": "quote",
}

# Phase 5.9 / §28.17 — manual posts can be classified too. Daniel may
# leave this as 'unspecified' (the default) when logging fast and revisit
# it during the classification pass.
_CONTENT_TYPE_CHOICES: tuple[str, ...] = (
    "unspecified",
    "value",
    "growth",
    "personality",
    "proof",
)


def submit_post(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert a ``posts`` row. Returns the new id.

    Validates type, text, and dates. Derives ``created_date`` from
    ``posted_at_utc`` and ``manual_confirmation_status`` from ``x_post_id``.
    """
    errors: dict[str, str] = {}

    type_ui = payload.get("type")
    if type_ui not in POST_TYPES_UI:
        errors["type"] = f"Must be one of: {', '.join(POST_TYPES_UI)}."

    text = payload.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        errors["text"] = "Required."

    posted_at_utc = payload.get("posted_at_utc") or now_utc_iso()
    if not isinstance(posted_at_utc, str):
        errors["posted_at_utc"] = "Must be ISO-8601 string."
    else:
        try:
            datetime.fromisoformat(posted_at_utc.replace("Z", "+00:00"))
        except ValueError:
            errors["posted_at_utc"] = "Must be ISO-8601 timestamp."

    if type_ui == "reply" and not payload.get("in_reply_to_x_post_id"):
        # Spec doesn't force the in-reply-to post id at MVP — but if user
        # picked 'reply' with no in_reply_to_user either, the row is weird.
        # We allow it (matches §22 "Manual reply has no post ID") but the
        # post_log queue will surface it.
        pass

    if errors:
        raise FormError("Post log validation failed.", field_errors=errors)

    x_post_id = payload.get("x_post_id") or None
    manual_confirmation_status = (
        "confirmed" if x_post_id else "needs_id"
    )
    if payload.get("manual_confirmation_status") in {
        "confirmed", "needs_id", "needs_metrics", "draft",
    }:
        manual_confirmation_status = payload["manual_confirmation_status"]

    try:
        created_date = datetime.fromisoformat(
            posted_at_utc.replace("Z", "+00:00")
        ).date().isoformat()
    except ValueError:
        created_date = _date_t.today().isoformat()

    type_schema = _TYPE_UI_TO_SCHEMA[type_ui]

    # Phase 5.9 / §28.17 — manual flow: 'unspecified' is the documented
    # default. The CHECK constraint on posts.content_type rejects junk
    # values; the orchestrator only enforces non-unspecified on agent
    # drafts, never on manual logging.
    content_type = payload.get("content_type") or "unspecified"
    if content_type not in _CONTENT_TYPE_CHOICES:
        raise FormError(
            "content_type validation failed.",
            field_errors={"content_type": f"Must be one of {_CONTENT_TYPE_CHOICES}."},
        )

    cursor = conn.execute(
        """
        INSERT INTO posts (
            x_post_id,
            created_at_utc,
            created_date,
            text,
            url,
            type,
            in_reply_to_post_id,
            in_reply_to_user,
            posted_via,
            manual_confirmation_status,
            contains_link,
            content_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)
        """,
        (
            x_post_id,
            posted_at_utc,
            created_date,
            text.strip(),
            (payload.get("manual_url") or None),
            type_schema,
            (payload.get("in_reply_to_x_post_id") or None),
            (payload.get("in_reply_to_user") or None),
            manual_confirmation_status,
            1 if payload.get("contains_link") else 0,
            content_type,
        ),
    )
    return int(cursor.lastrowid)


def add_post_id(
    conn: sqlite3.Connection,
    post_id: int,
    x_post_id: str,
    manual_url: str | None = None,
) -> None:
    """Backfill the X post id for an existing manual row.

    Handles the §22 "Reply URL entered later" edge case. Flips
    ``manual_confirmation_status`` from ``needs_id`` → ``confirmed``.

    Raises ``FormError`` when (a) ``post_id`` doesn't exist or (b)
    ``x_post_id`` is already linked to another row (the UNIQUE constraint
    would raise IntegrityError; we surface a friendly error first).
    """
    x_post_id = (x_post_id or "").strip()
    if not x_post_id:
        raise FormError(
            "x_post_id required.",
            field_errors={"x_post_id": "Required."},
        )

    existing = conn.execute(
        "SELECT id FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if existing is None:
        raise FormError(
            f"Post id={post_id} not found.",
            field_errors={"post_id": "No such post row."},
        )

    duplicate = conn.execute(
        "SELECT id FROM posts WHERE x_post_id = ? AND id != ?",
        (x_post_id, post_id),
    ).fetchone()
    if duplicate is not None:
        raise FormError(
            f"x_post_id={x_post_id} is already linked to post #{duplicate['id']}.",
            field_errors={
                "x_post_id": f"Already linked to post #{duplicate['id']}.",
            },
        )

    cursor = conn.execute(
        """
        UPDATE posts
           SET x_post_id = ?,
               url = COALESCE(?, url),
               manual_confirmation_status = 'confirmed'
         WHERE id = ?
        """,
        (x_post_id, (manual_url or None), post_id),
    )
    if cursor.rowcount != 1:
        # Defense in depth — the SELECT above already proves the row exists.
        raise FormError(
            f"Failed to update post id={post_id} (rowcount={cursor.rowcount}).",
            field_errors={"post_id": "Update affected zero rows."},
        )

    # P8R-8: backfill publish_confirmation_tokens.consumed_by_x_post_id
    # for the most-recently-consumed token attached to this post (if any).
    # The API branch in app/agent/publish.py populates this column at
    # publish-success time; the manual-clipboard flow used to leave it
    # NULL forever, breaking analytics that join from a consumed token
    # to its resulting X post. Idempotent: only updates rows whose
    # column is currently NULL (manual reconcile via Mark posted runs
    # at most once per post). NULL-tolerant: posts logged outside the
    # agent flow (no confirmation token at all) silently no-op.
    conn.execute(
        """
        UPDATE publish_confirmation_tokens
           SET consumed_by_x_post_id = ?
         WHERE id = (
             SELECT id FROM publish_confirmation_tokens
              WHERE post_id = ?
                AND consumed_at_utc IS NOT NULL
                AND consumed_by_x_post_id IS NULL
              ORDER BY consumed_at_utc DESC
              LIMIT 1
         )
        """,
        (x_post_id, post_id),
    )


def render(conn: sqlite3.Connection, *, key_prefix: str = "post_log") -> None:
    """Streamlit fragment: post/reply logging."""
    import streamlit as st  # lazy — keeps the FastAPI sidecar graph streamlit-free (§31.6)

    st.subheader("Log a post / reply / quote")
    st.caption(
        "Spec §15.2 — manual entries are first-class. Sets `posted_via='manual'`. "
        "If `x_post_id` is empty the row lands in the 'Needs post ID' queue (§22)."
    )

    # clear_on_submit=False — validation failures must preserve typed text.
    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        col_type, col_when = st.columns([1, 2])
        type_ui = col_type.selectbox(
            "Type", POST_TYPES_UI, key=f"{key_prefix}_type"
        )
        posted_at = col_when.text_input(
            "Posted at (UTC ISO-8601, e.g. 2026-05-21T14:30:00Z)",
            value=now_utc_iso(),
            key=f"{key_prefix}_when",
        )
        text = st.text_area(
            "Text", key=f"{key_prefix}_text", height=120,
            help="Paste the full post/reply body."
        )

        col_xid, col_url = st.columns(2)
        x_post_id = col_xid.text_input(
            "x_post_id (optional — leave blank for 'needs ID' queue)",
            key=f"{key_prefix}_xid",
        )
        manual_url = col_url.text_input(
            "Manual URL (optional)", key=f"{key_prefix}_url"
        )

        col_rt, col_ru = st.columns(2)
        in_reply_to_x_post_id = col_rt.text_input(
            "In reply to post id (only if reply)",
            key=f"{key_prefix}_rtid",
        )
        in_reply_to_user = col_ru.text_input(
            "In reply to @user (only if reply)",
            key=f"{key_prefix}_rtuser",
        )

        content_type = st.selectbox(
            "Content type (§28.17 V/G/P/P axis)",
            options=_CONTENT_TYPE_CHOICES,
            index=0,
            key=f"{key_prefix}_content_type",
            help=(
                "Purpose of the post, orthogonal to pillar/topic. "
                "Leave 'unspecified' when logging fast — revisit during "
                "the classification pass."
            ),
        )

        contains_link = st.checkbox(
            "Contains link", key=f"{key_prefix}_contains_link"
        )

        submitted = st.form_submit_button("Log post", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "type": type_ui,
            "text": text,
            "posted_at_utc": posted_at.strip(),
            "x_post_id": x_post_id.strip(),
            "manual_url": manual_url.strip(),
            "in_reply_to_x_post_id": in_reply_to_x_post_id.strip(),
            "in_reply_to_user": in_reply_to_user.strip(),
            "contains_link": contains_link,
            "content_type": content_type,
        }
        try:
            new_id = submit_post(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Post #{new_id} logged. Don't forget to classify it.")
