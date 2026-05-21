"""Stir conversion event form — spec.md §15.4.

Inserts ``stir_conversion_events`` rows. Strictly enforces the privacy rule
that ``is_likely_icp`` is only settable when
``attribution_method = 'self_reported'`` (schema CHECK + §13 hard rule 11 +
§18 privacy). The form refuses to auto-attribute downloads to posts; the
``referring_post_id`` FK is a manually-chosen link only.

§14.5 App Store gap: downstream attribution (downloads, retention) is *never*
inferred from a user action on the Daniel side. The form encourages
self-reported source text (e.g. "DM'd me to say they found me from reply to
@parenting_account") and stores it in the free-text ``source`` field.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from datetime import datetime
from typing import Any

import streamlit as st

from app.forms import FormError, now_utc_iso

EVENT_CATEGORIES: tuple[str, ...] = ("acquisition", "activation", "usage", "feedback")
ATTRIBUTION_METHODS: tuple[str, ...] = (
    "self_reported", "utm", "referrer_header", "inferred", "unknown",
)
SOURCE_DATA_QUALITY: tuple[str, ...] = (
    "exact", "manual", "estimated", "inferred", "unknown",
)

# Suggested event types per §15.4 examples; UI-only convenience list. Free
# text is allowed so retroactive categorization (§10.2) stays possible.
SUGGESTED_EVENT_TYPES: tuple[str, ...] = (
    "signup_intent",
    "tester_install",
    "kitchen_scan",
    "got_plausible_dinners",
    "cook_mode_used",
    "tester_feedback",
    "unprompted_feedback",
)


def _validate(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if payload.get("event_category") not in EVENT_CATEGORIES:
        errors["event_category"] = f"Must be one of: {', '.join(EVENT_CATEGORIES)}."
    event_type = payload.get("event_type")
    if not event_type or not isinstance(event_type, str) or not event_type.strip():
        errors["event_type"] = "Required (free text, e.g. tester_install)."

    occurred = payload.get("occurred_at_utc")
    if not occurred or not isinstance(occurred, str):
        errors["occurred_at_utc"] = "Required (ISO-8601 UTC)."
    else:
        try:
            datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except ValueError:
            errors["occurred_at_utc"] = "Must be ISO-8601 timestamp."

    attribution = payload.get("attribution_method")
    if attribution not in ATTRIBUTION_METHODS:
        errors["attribution_method"] = (
            f"Must be one of: {', '.join(ATTRIBUTION_METHODS)}."
        )

    is_likely_icp = payload.get("is_likely_icp")
    # §13 hard rule 11 + schema CHECK: is_likely_icp is only valid when
    # attribution is self_reported. We enforce here so the UI gets a precise
    # error before hitting the DB.
    if is_likely_icp is not None:
        if attribution != "self_reported":
            errors["is_likely_icp"] = (
                "Only settable when attribution_method is self_reported "
                "(spec §13 hard rule 11)."
            )
        elif is_likely_icp not in (0, 1, True, False):
            errors["is_likely_icp"] = "Must be 0/1 or boolean."

    quality = payload.get("source_data_quality")
    if quality not in SOURCE_DATA_QUALITY:
        errors["source_data_quality"] = (
            f"Must be one of: {', '.join(SOURCE_DATA_QUALITY)}."
        )

    return errors


def submit_stir_event(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert a ``stir_conversion_events`` row. Returns the new id."""
    errors = _validate(payload)
    if errors:
        raise FormError("Stir event validation failed.", field_errors=errors)

    occurred = payload["occurred_at_utc"]
    try:
        event_date = (
            datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            .date()
            .isoformat()
        )
    except ValueError:
        event_date = _date_t.today().isoformat()

    is_likely_icp_raw = payload.get("is_likely_icp")
    if is_likely_icp_raw is None:
        is_likely_icp = None
    else:
        is_likely_icp = 1 if int(bool(is_likely_icp_raw)) == 1 else 0

    cursor = conn.execute(
        """
        INSERT INTO stir_conversion_events (
            occurred_at_utc,
            event_date,
            event_category,
            event_type,
            source,
            referring_post_id,
            referring_x_handle,
            attribution_method,
            is_likely_icp,
            qualitative_feedback,
            source_data_quality
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occurred,
            event_date,
            payload["event_category"],
            payload["event_type"].strip(),
            (payload.get("source") or None),
            payload.get("referring_post_id") or None,
            (payload.get("referring_x_handle") or None),
            payload["attribution_method"],
            is_likely_icp,
            (payload.get("qualitative_feedback") or None),
            payload["source_data_quality"],
        ),
    )
    return int(cursor.lastrowid)


def render(conn: sqlite3.Connection, *, key_prefix: str = "stir_event") -> None:
    """Streamlit fragment: Stir conversion event form."""
    st.subheader("Stir conversion event")
    st.caption(
        "Spec §15.4. **Never auto-attribute downloads to posts** (§13 hard "
        "rule 11 + §14.5 App Store gap). `is_likely_icp` only settable when "
        "attribution is `self_reported`."
    )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=True):
        col_cat, col_type = st.columns([1, 2])
        category = col_cat.selectbox(
            "Category", EVENT_CATEGORIES, key=f"{key_prefix}_cat"
        )
        event_type = col_type.text_input(
            "Event type",
            help=f"Suggested: {', '.join(SUGGESTED_EVENT_TYPES)}",
            key=f"{key_prefix}_type",
        )

        occurred = st.text_input(
            "Occurred at (UTC ISO-8601)", value=now_utc_iso(),
            key=f"{key_prefix}_when",
        )

        col_attr, col_dq = st.columns(2)
        attribution = col_attr.selectbox(
            "Attribution method", ATTRIBUTION_METHODS,
            key=f"{key_prefix}_attr",
        )
        source_data_quality = col_dq.selectbox(
            "Source data quality", SOURCE_DATA_QUALITY,
            index=SOURCE_DATA_QUALITY.index("manual"),
            key=f"{key_prefix}_dq",
        )

        source_hint = st.text_input(
            "Source hint (free text — e.g. 'replied to @parenting_account')",
            key=f"{key_prefix}_source",
        )
        referring_handle = st.text_input(
            "Referring X handle (optional, without @)",
            key=f"{key_prefix}_handle",
        )

        # Manual post link is allowed but optional (§15.4): never auto-derived.
        post_options = conn.execute(
            """
            SELECT id, created_date, substr(text, 1, 60) AS preview
              FROM posts
             ORDER BY created_at_utc DESC
             LIMIT 100
            """
        ).fetchall()
        post_labels = {"(none)": None}
        for r in post_options:
            post_labels[
                f"#{r['id']} · {r['created_date']} · {r['preview']}…"
            ] = int(r["id"])
        linked_label = st.selectbox(
            "Linked post (optional, manual link only — never inferred)",
            list(post_labels.keys()),
            key=f"{key_prefix}_linked",
        )
        linked_post_id = post_labels[linked_label]

        # is_likely_icp is gated on attribution choice.
        is_likely_icp: int | None = None
        if attribution == "self_reported":
            icp_choice = st.radio(
                "Is likely ICP? (only settable when self_reported)",
                options=("unknown", "yes", "no"),
                horizontal=True,
                key=f"{key_prefix}_icp",
            )
            is_likely_icp = (
                None if icp_choice == "unknown"
                else (1 if icp_choice == "yes" else 0)
            )
        else:
            st.caption(
                "ICP flag disabled — attribution must be `self_reported` to set "
                "(spec §13 hard rule 11)."
            )

        feedback = st.text_area(
            "Qualitative feedback (optional)",
            key=f"{key_prefix}_feedback", height=80,
        )

        submitted = st.form_submit_button("Save event", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "event_category": category,
            "event_type": event_type,
            "occurred_at_utc": occurred.strip(),
            "source": source_hint.strip(),
            "referring_x_handle": referring_handle.strip(),
            "referring_post_id": linked_post_id,
            "attribution_method": attribution,
            "is_likely_icp": is_likely_icp,
            "qualitative_feedback": feedback,
            "source_data_quality": source_data_quality,
        }
        try:
            new_id = submit_stir_event(conn, payload)
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Stir event #{new_id} logged.")
