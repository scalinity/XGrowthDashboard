"""Daily account snapshot form — spec.md §15.1.

The MVP-default morning-ritual form. Designed to land 30 seconds of data
entry into ``account_snapshots`` with ``source = 'manual'`` and
``data_quality = 'manual'``.

§22 edge cases handled here:

- **Multiple snapshots same day** — if a snapshot already exists for the same
  (username, snapshot_date) the form returns ``FormError`` with a
  ``duplicate_snapshot_id`` field so the UI can offer "edit instead?" via
  the correction form. We never overwrite the prior snapshot (§13 hard rule 2).
- **Manual snapshot, then later API snapshot** — both are stored; canonical
  selection happens in ``v_account_daily`` (§11), not here.

The pure ``submit_snapshot`` function is the unit tested by
``tests/test_forms_persistence.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from typing import Any

import streamlit as st

from app.forms import FormError, get_setting, now_utc_iso, today_iso


def _validate(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field in ("snapshot_date", "username", "profile_url"):
        if not payload.get(field):
            errors[field] = "Required."
    for field in (
        "followers_count",
        "following_count",
        "post_count",
        "listed_count",
        "baseline_followers",
    ):
        value = payload.get(field)
        if value is None or not isinstance(value, int) or value < 0:
            errors[field] = "Must be a non-negative integer."
    snapshot_date = payload.get("snapshot_date")
    if isinstance(snapshot_date, str):
        try:
            _date_t.fromisoformat(snapshot_date)
        except ValueError:
            errors["snapshot_date"] = "Must be ISO-8601 (YYYY-MM-DD)."
    return errors


def find_existing_for_date(
    conn: sqlite3.Connection, username: str, snapshot_date: str
) -> sqlite3.Row | None:
    """Return the first existing snapshot for (username, snapshot_date) or None.

    Used by the render layer to surface the §22 "edit or correct?" affordance
    before the user even submits.

    Note (TOCTOU race): the schema's UNIQUE index is on
    ``(username, collected_at_utc)``, NOT ``(username, snapshot_date)``, so
    nothing stops two near-simultaneous submits for the same date with
    distinct collection timestamps. This is a single-user local app — a
    double-submit is the only realistic case and the render layer surfaces
    the existing row before the form is shown. If multi-user ever lands,
    add a partial UNIQUE index on ``(username, snapshot_date) WHERE
    source='manual'`` and let the schema raise.
    """
    return conn.execute(
        """
        SELECT *
          FROM account_snapshots
         WHERE username = ?
           AND snapshot_date = ?
         ORDER BY collected_at_utc ASC
         LIMIT 1
        """,
        (username, snapshot_date),
    ).fetchone()


def submit_snapshot(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert a new ``account_snapshots`` row. Returns the new row id.

    Refuses to insert when a snapshot already exists for the same
    (username, snapshot_date) per §15.1 / §22 — that path must go through
    the correction form instead.
    """
    errors = _validate(payload)
    if errors:
        raise FormError("Snapshot validation failed.", field_errors=errors)

    existing = find_existing_for_date(
        conn, payload["username"], payload["snapshot_date"]
    )
    if existing is not None:
        raise FormError(
            "A snapshot already exists for this date — edit via correction form.",
            field_errors={"snapshot_date": "Duplicate for this date.",
                          "duplicate_snapshot_id": str(existing["id"])},
        )

    cursor = conn.execute(
        """
        INSERT INTO account_snapshots (
            snapshot_date,
            collected_at_utc,
            x_user_id,
            username,
            profile_url,
            followers_count,
            following_count,
            post_count,
            listed_count,
            like_count,
            media_count,
            bio_text,
            baseline_followers,
            source,
            data_quality
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 'manual')
        """,
        (
            payload["snapshot_date"],
            payload.get("collected_at_utc") or now_utc_iso(),
            payload.get("x_user_id"),
            payload["username"],
            payload["profile_url"],
            payload["followers_count"],
            payload["following_count"],
            payload["post_count"],
            payload["listed_count"],
            payload.get("like_count"),
            payload.get("media_count"),
            payload.get("bio_text"),
            payload["baseline_followers"],
        ),
    )
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Streamlit render layer
# ---------------------------------------------------------------------------

def _defaults_from_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "username": get_setting(conn, "x_handle", "") or "",
        "profile_url": get_setting(conn, "profile_url", "") or "",
        "baseline_followers": int(get_setting(conn, "baseline_followers", 0) or 0),
        "x_user_id": get_setting(conn, "x_user_id"),
    }


def render(conn: sqlite3.Connection, *, key_prefix: str = "snapshot") -> None:
    """Streamlit fragment: pinned snapshot form."""
    defaults = _defaults_from_settings(conn)

    st.subheader("Pinned daily snapshot")
    st.caption(
        "Spec §15.1 — designed to take 30 seconds. Sets `source='manual'`, "
        "`data_quality='manual'`. Corrections never overwrite (§13 hard rule 2)."
    )

    snapshot_date = st.date_input(
        "Snapshot date",
        value=_date_t.today(),
        key=f"{key_prefix}_date",
    )

    existing = find_existing_for_date(
        conn, defaults["username"], snapshot_date.isoformat()
    )
    if existing is not None:
        st.warning(
            f"A snapshot for {snapshot_date.isoformat()} already exists (id="
            f"{existing['id']}, followers={existing['followers_count']}). "
            "Use the Correction tab to record a fix — original snapshots are "
            "immutable per §13 hard rule 2."
        )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        col1, col2, col3 = st.columns(3)
        followers_count = col1.number_input(
            "Followers", min_value=0, step=1, key=f"{key_prefix}_followers"
        )
        following_count = col2.number_input(
            "Following", min_value=0, step=1, key=f"{key_prefix}_following"
        )
        post_count = col3.number_input(
            "Posts", min_value=0, step=1, key=f"{key_prefix}_posts"
        )

        col4, col5, col6 = st.columns(3)
        listed_count = col4.number_input(
            "Listed", min_value=0, step=1, key=f"{key_prefix}_listed"
        )
        like_count = col5.number_input(
            "Total likes (optional)", min_value=0, step=1, value=0,
            key=f"{key_prefix}_likes",
        )
        media_count = col6.number_input(
            "Media (optional)", min_value=0, step=1, value=0,
            key=f"{key_prefix}_media",
        )

        bio_text = st.text_area(
            "Bio text (optional)", key=f"{key_prefix}_bio", height=80
        )

        submitted = st.form_submit_button("Save snapshot", type="primary")
        if not submitted:
            return

        payload = {
            "snapshot_date": snapshot_date.isoformat(),
            "username": defaults["username"],
            "profile_url": defaults["profile_url"],
            "x_user_id": defaults["x_user_id"],
            "followers_count": int(followers_count),
            "following_count": int(following_count),
            "post_count": int(post_count),
            "listed_count": int(listed_count),
            "like_count": int(like_count) or None,
            "media_count": int(media_count) or None,
            "bio_text": bio_text.strip() or None,
            "baseline_followers": defaults["baseline_followers"],
        }
        try:
            new_id = submit_snapshot(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Snapshot #{new_id} saved for {payload['snapshot_date']}.")
