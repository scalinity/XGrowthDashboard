"""Snapshot correction form — spec.md §13 hard rule 2 + §22.

Corrections never mutate ``account_snapshots`` — they append a row to
``account_snapshot_corrections`` capturing the field, prior value, new value,
and the user's reason. ``v_account_daily`` (§11) applies corrections as
derived overlays on read.

The set of correctable fields is restricted to columns whose semantics make
sense to revise: count fields, profile metadata, and bio text. ``snapshot_date``
and ``collected_at_utc`` are not user-correctable here — if either is wrong
that is a "wrong snapshot" situation, not a single-field correction.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import streamlit as st

from app.forms import FormError

CORRECTABLE_FIELDS: tuple[str, ...] = (
    "followers_count",
    "following_count",
    "post_count",
    "listed_count",
    "like_count",
    "media_count",
    "bio_text",
    "username",
    "profile_url",
)


def _get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM account_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()


def submit_correction(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert one ``account_snapshot_corrections`` row. Returns the new id.

    Never UPDATEs ``account_snapshots`` — preserving raw rows is §13 hard
    rule 2.
    """
    errors: dict[str, str] = {}
    snapshot_id = payload.get("snapshot_id")
    if not isinstance(snapshot_id, int) or snapshot_id <= 0:
        errors["snapshot_id"] = "Required (must be a valid snapshot row id)."
    field_name = payload.get("field_name")
    if field_name not in CORRECTABLE_FIELDS:
        errors["field_name"] = f"Must be one of: {', '.join(CORRECTABLE_FIELDS)}."
    new_value = payload.get("new_value")
    if new_value is None or (isinstance(new_value, str) and not new_value.strip()):
        errors["new_value"] = "Required (the corrected value)."
    reason = payload.get("reason")
    if not reason or not isinstance(reason, str) or not reason.strip():
        errors["reason"] = "Required (audit trail — say why)."
    if errors:
        raise FormError("Correction validation failed.", field_errors=errors)

    snapshot = _get_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise FormError(
            f"Snapshot id={snapshot_id} not found.",
            field_errors={"snapshot_id": "No such snapshot row."},
        )

    old_value = snapshot[field_name]
    if old_value is None:
        old_value_str = ""
    else:
        old_value_str = str(old_value)
    new_value_str = str(new_value).strip() if isinstance(new_value, str) else str(new_value)

    cursor = conn.execute(
        """
        INSERT INTO account_snapshot_corrections (
            snapshot_id, field_name, old_value, new_value, reason
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (snapshot_id, field_name, old_value_str, new_value_str, reason.strip()),
    )
    return int(cursor.lastrowid)


def list_snapshot_options(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Recent snapshots, newest first, for the correction-form selectbox."""
    return list(
        conn.execute(
            """
            SELECT id, snapshot_date, username, followers_count, collected_at_utc
              FROM account_snapshots
             ORDER BY collected_at_utc DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


def render(conn: sqlite3.Connection, *, key_prefix: str = "correction") -> None:
    """Streamlit fragment: snapshot correction form."""
    st.subheader("Correct a snapshot field")
    st.caption(
        "Corrections never overwrite the original snapshot (§13 hard rule 2). "
        "Each correction is appended to `account_snapshot_corrections` and "
        "surfaced through `v_account_daily` (Phase 3)."
    )

    snapshots = list_snapshot_options(conn)
    if not snapshots:
        st.info("No snapshots yet — log one via the Snapshot tab first.")
        return

    options = {
        f"#{row['id']} · {row['snapshot_date']} · @{row['username']} · {row['followers_count']} followers": row
        for row in snapshots
    }
    chosen_label = st.selectbox(
        "Snapshot to correct", list(options.keys()), key=f"{key_prefix}_select"
    )
    snapshot = options[chosen_label]

    # clear_on_submit=False so a validation failure preserves the user's
    # typed reason/value instead of wiping it.
    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        field_name = st.selectbox(
            "Field to correct",
            CORRECTABLE_FIELDS,
            key=f"{key_prefix}_field",
        )
        st.caption(
            f"Current value in snapshot #{snapshot['id']}: "
            f"`{snapshot[field_name] if snapshot[field_name] is not None else '(null)'}`"
        )
        new_value_str = st.text_input(
            "Corrected value", key=f"{key_prefix}_new_value",
            help="Numeric fields will be coerced to int; text fields stored verbatim.",
        )
        reason = st.text_area(
            "Reason (required — audit trail)",
            key=f"{key_prefix}_reason", height=80,
        )
        submitted = st.form_submit_button("Save correction", type="primary")
        if not submitted:
            return

        coerced: Any = new_value_str
        if field_name in {
            "followers_count", "following_count", "post_count",
            "listed_count", "like_count", "media_count",
        }:
            try:
                coerced = int(new_value_str)
            except (TypeError, ValueError):
                st.error(f"`{field_name}` requires an integer.")
                return

        payload = {
            "snapshot_id": int(snapshot["id"]),
            "field_name": field_name,
            "new_value": coerced,
            "reason": reason,
        }
        try:
            new_id = submit_correction(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(
            f"Correction #{new_id} recorded for snapshot #{snapshot['id']}."
        )
