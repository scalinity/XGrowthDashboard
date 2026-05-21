"""Stir tester registration form — spec.md §15.4 + §10.2.

Inserts ``stir_testers`` rows. The ``is_working_parent_home_cook`` attribute
is **only** stored when the tester self-reports it (§13 hard rule 11). The
form represents the "unknown" case explicitly and never infers from
behavior or X profile signals.

Status enum is the §10.2 stage ladder: lead → downloaded → activated →
cook_mode_used → churned (or unknown).
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from typing import Any

import streamlit as st

from app.forms import FormError, today_iso

STATUS_VALUES: tuple[str, ...] = (
    "lead", "downloaded", "activated", "cook_mode_used", "churned", "unknown",
)


def _validate(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    alias = payload.get("alias")
    if not alias or not isinstance(alias, str) or not alias.strip():
        errors["alias"] = "Required (any short label — no need to be real)."
    first_seen = payload.get("first_seen_date")
    if not first_seen:
        errors["first_seen_date"] = "Required."
    else:
        try:
            _date_t.fromisoformat(str(first_seen))
        except ValueError:
            errors["first_seen_date"] = "Must be ISO-8601 (YYYY-MM-DD)."
    if payload.get("status") not in STATUS_VALUES:
        errors["status"] = f"Must be one of: {', '.join(STATUS_VALUES)}."

    icp = payload.get("is_working_parent_home_cook")
    # Schema CHECK already enforces 0/1/NULL; we add the spec rule here.
    if icp is not None:
        self_reported = payload.get("self_reported_icp", False)
        if not self_reported:
            errors["is_working_parent_home_cook"] = (
                "Only settable when explicitly self-reported "
                "(§13 hard rule 11)."
            )
        elif icp not in (0, 1, True, False):
            errors["is_working_parent_home_cook"] = "Must be 0/1 or boolean."
    return errors


def submit_tester(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert a ``stir_testers`` row. Returns the new id."""
    errors = _validate(payload)
    if errors:
        raise FormError("Tester validation failed.", field_errors=errors)

    icp_raw = payload.get("is_working_parent_home_cook")
    if icp_raw is None:
        icp = None
    else:
        icp = 1 if int(bool(icp_raw)) == 1 else 0

    cursor = conn.execute(
        """
        INSERT INTO stir_testers (
            alias,
            x_handle,
            contact_ref,
            source,
            first_seen_date,
            is_working_parent_home_cook,
            icp_notes,
            feedback_summary,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["alias"].strip(),
            (payload.get("x_handle") or None),
            (payload.get("contact_ref") or None),
            (payload.get("source") or None),
            payload["first_seen_date"],
            icp,
            (payload.get("icp_notes") or None),
            (payload.get("feedback_summary") or None),
            payload["status"],
        ),
    )
    return int(cursor.lastrowid)


def render(conn: sqlite3.Connection, *, key_prefix: str = "stir_tester") -> None:
    """Streamlit fragment: tester registration."""
    st.subheader("Register a Stir tester")
    st.caption(
        "Spec §15.4. ICP attributes (working_parent/home_cook) are "
        "**self-report only** (§13 hard rule 11). Leaving them blank is "
        "the correct default."
    )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=True):
        col_a, col_h = st.columns(2)
        alias = col_a.text_input("Alias (required)", key=f"{key_prefix}_alias")
        x_handle = col_h.text_input(
            "X handle (without @)", key=f"{key_prefix}_handle"
        )

        first_seen = st.date_input(
            "First seen date", value=_date_t.today(), key=f"{key_prefix}_first"
        )

        col_status, col_contact = st.columns(2)
        status = col_status.selectbox(
            "Status", STATUS_VALUES, key=f"{key_prefix}_status"
        )
        contact_ref = col_contact.text_input(
            "Contact reference (optional)", key=f"{key_prefix}_contact"
        )

        source = st.text_input(
            "Source (where did they come from?)", key=f"{key_prefix}_source"
        )

        self_reported = st.checkbox(
            "Tester self-reported ICP attributes?",
            key=f"{key_prefix}_self_reported",
            help="Tick only when the tester explicitly told you. Inference is "
                 "forbidden (§13 hard rule 11).",
        )
        icp_value: int | None = None
        if self_reported:
            icp_choice = st.radio(
                "Working parent / home cook? (self-reported)",
                options=("unknown", "yes", "no"),
                horizontal=True,
                key=f"{key_prefix}_icp",
            )
            icp_value = (
                None if icp_choice == "unknown"
                else (1 if icp_choice == "yes" else 0)
            )

        icp_notes = st.text_area(
            "ICP notes (optional, anything they said)",
            key=f"{key_prefix}_icp_notes", height=70,
        )
        feedback_summary = st.text_area(
            "Feedback summary (optional)",
            key=f"{key_prefix}_feedback", height=70,
        )

        submitted = st.form_submit_button("Save tester", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "alias": alias,
            "x_handle": x_handle,
            "contact_ref": contact_ref,
            "source": source,
            "first_seen_date": first_seen.isoformat(),
            "status": status,
            "self_reported_icp": self_reported,
            "is_working_parent_home_cook": icp_value,
            "icp_notes": icp_notes,
            "feedback_summary": feedback_summary,
        }
        try:
            new_id = submit_tester(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Tester #{new_id} ({alias}) registered.")
