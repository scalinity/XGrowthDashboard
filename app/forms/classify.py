"""Post classification form — spec.md §15.3.

v1 taxonomy (text-typed at the schema level — §10.2):

- ``pillar``   ∈ {stir, build, self}
- ``audience`` ∈ {icp, other}
- ``cta``      ∈ {ask, none}

Plus the learning-note fields (``why_posted``, ``hypothesis``,
``expected_signal``) which are optional at create-time and back-fillable
later (``actual_signal``, ``lesson`` land in the Phase 3 weekly review path).

If a classification already exists for the post we refuse to overwrite — the
spec is explicit about preserving prior interpretations as an audit trail
(§13 hard rule 2 generalized; §22 corrections rule). The render layer surfaces
this as "Edit instead?" with a re-classify confirmation that explicitly
overwrites (a future Phase 3 enhancement adds a versioned classification
table; until then we treat the row as single-source).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app._optional_streamlit import st

from app.forms import FormError

PILLAR_VALUES: tuple[str, ...] = ("stir", "build", "self")
AUDIENCE_VALUES: tuple[str, ...] = ("icp", "other")
CTA_VALUES: tuple[str, ...] = ("ask", "none")


def find_existing(conn: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Return the latest classification row for ``post_id`` or None.

    Schema-wise multiple rows are *allowed* per post — ``v_lane_performance``
    intentionally picks the latest ``classified_at`` per post so historical
    reclassifications (e.g. from a Phase 5 CSV import) don't double-count.
    The form-layer overwrite path UPDATEs the latest row in place; if any
    other path inserts a second row, the view stays correct and the form
    will surface and let the user re-overwrite the latest one.
    """
    return conn.execute(
        """
        SELECT *
          FROM post_classifications
         WHERE post_id = ?
         ORDER BY classified_at DESC, id DESC
         LIMIT 1
        """,
        (post_id,),
    ).fetchone()


def submit_classification(
    conn: sqlite3.Connection, payload: dict[str, Any], *, allow_overwrite: bool = False
) -> int:
    """Insert one ``post_classifications`` row. Returns the new id.

    Refuses when one already exists unless ``allow_overwrite=True`` (the
    render layer offers an explicit re-classify checkbox that flips this).
    """
    errors: dict[str, str] = {}
    post_id = payload.get("post_id")
    if not isinstance(post_id, int) or post_id <= 0:
        errors["post_id"] = "Required."
    pillar = payload.get("pillar")
    if pillar not in PILLAR_VALUES:
        errors["pillar"] = f"Must be one of: {', '.join(PILLAR_VALUES)}."
    audience = payload.get("audience")
    if audience not in AUDIENCE_VALUES:
        errors["audience"] = f"Must be one of: {', '.join(AUDIENCE_VALUES)}."
    cta = payload.get("cta")
    if cta not in CTA_VALUES:
        errors["cta"] = f"Must be one of: {', '.join(CTA_VALUES)}."
    quality_score = payload.get("quality_score")
    if quality_score is not None and (
        not isinstance(quality_score, int) or not (1 <= quality_score <= 5)
    ):
        errors["quality_score"] = "Must be 1-5 or empty."
    if errors:
        raise FormError("Classification validation failed.", field_errors=errors)

    existing = find_existing(conn, post_id)
    if existing is not None and not allow_overwrite:
        raise FormError(
            "Post is already classified — pass allow_overwrite=True to replace.",
            field_errors={
                "post_id": "Already classified.",
                "existing_classification_id": str(existing["id"]),
            },
        )

    if existing is not None:
        # Re-classify: update in place but bump updated_at. Prior values are
        # not preserved in v1; spec §10.2 leaves versioning for V1.1.
        conn.execute(
            """
            UPDATE post_classifications
               SET pillar = ?,
                   audience = ?,
                   cta = ?,
                   quality_score = ?,
                   why_posted = ?,
                   hypothesis = ?,
                   expected_signal = ?,
                   actual_signal = COALESCE(?, actual_signal),
                   lesson = COALESCE(?, lesson),
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                pillar, audience, cta, quality_score,
                payload.get("why_posted"),
                payload.get("hypothesis"),
                payload.get("expected_signal"),
                payload.get("actual_signal"),
                payload.get("lesson"),
                existing["id"],
            ),
        )
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO post_classifications (
            post_id, pillar, audience, cta, quality_score,
            why_posted, hypothesis, expected_signal,
            actual_signal, lesson
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id, pillar, audience, cta, quality_score,
            payload.get("why_posted"),
            payload.get("hypothesis"),
            payload.get("expected_signal"),
            payload.get("actual_signal"),
            payload.get("lesson"),
        ),
    )
    return int(cursor.lastrowid)


def render(
    conn: sqlite3.Connection,
    *,
    key_prefix: str = "classify",
    preselected_post_id: int | None = None,
) -> None:
    """Streamlit fragment: classification form."""
    st.subheader("Classify a post")
    st.caption(
        "Spec §15.3 v1 taxonomy. Re-classifying an already-tagged post requires "
        "the explicit 'overwrite' checkbox (preserves intent of §13 hard rule 2)."
    )

    options = conn.execute(
        """
        SELECT p.id, p.created_date, p.type, substr(p.text, 1, 60) AS preview,
               (c.id IS NOT NULL) AS classified
          FROM posts p
          LEFT JOIN post_classifications c ON c.post_id = p.id
         ORDER BY p.created_at_utc DESC
         LIMIT 200
        """
    ).fetchall()
    if not options:
        st.info("No posts to classify — log one via the Post Log tab first.")
        return

    labels = {
        f"#{r['id']} · {r['created_date']} · {r['type']} · "
        f"{'✓ classified' if r['classified'] else '○ needs tagging'} · "
        f"{r['preview']}…": r["id"]
        for r in options
    }
    default_idx = 0
    if preselected_post_id is not None:
        for i, label in enumerate(labels):
            if labels[label] == preselected_post_id:
                default_idx = i
                break
    chosen_label = st.selectbox(
        "Post", list(labels.keys()), index=default_idx, key=f"{key_prefix}_post"
    )
    post_id = labels[chosen_label]
    existing = find_existing(conn, post_id)
    if existing is not None:
        st.warning(
            f"Already classified ({existing['pillar']} / {existing['audience']} / "
            f"{existing['cta']}). Tick 'overwrite' below to replace."
        )

    with st.form(key=f"{key_prefix}_form", clear_on_submit=False):
        col_p, col_a, col_c = st.columns(3)
        pillar = col_p.selectbox(
            "Pillar", PILLAR_VALUES,
            index=PILLAR_VALUES.index(existing["pillar"]) if existing else 0,
            key=f"{key_prefix}_pillar",
        )
        audience = col_a.selectbox(
            "Audience", AUDIENCE_VALUES,
            index=AUDIENCE_VALUES.index(existing["audience"]) if existing else 0,
            key=f"{key_prefix}_audience",
        )
        cta = col_c.selectbox(
            "CTA", CTA_VALUES,
            index=CTA_VALUES.index(existing["cta"]) if existing else 0,
            key=f"{key_prefix}_cta",
        )
        quality_score = st.slider(
            "Quality score (optional)", min_value=0, max_value=5,
            value=int(existing["quality_score"]) if existing and existing["quality_score"] else 0,
            key=f"{key_prefix}_quality",
            help="0 = leave empty; 1-5 = explicit rating.",
        )
        why_posted = st.text_area(
            "Why posted?",
            value=(existing["why_posted"] or "") if existing else "",
            key=f"{key_prefix}_why", height=80,
        )
        hypothesis = st.text_area(
            "Hypothesis",
            value=(existing["hypothesis"] or "") if existing else "",
            key=f"{key_prefix}_hypothesis", height=80,
        )
        expected_signal = st.text_area(
            "Expected signal",
            value=(existing["expected_signal"] or "") if existing else "",
            key=f"{key_prefix}_expected", height=80,
        )
        overwrite = False
        if existing is not None:
            overwrite = st.checkbox(
                "Overwrite existing classification", key=f"{key_prefix}_overwrite"
            )
        submitted = st.form_submit_button("Save classification", type="primary")
        if not submitted:
            return

        payload: dict[str, Any] = {
            "post_id": int(post_id),
            "pillar": pillar,
            "audience": audience,
            "cta": cta,
            "quality_score": int(quality_score) if quality_score else None,
            "why_posted": why_posted.strip() or None,
            "hypothesis": hypothesis.strip() or None,
            "expected_signal": expected_signal.strip() or None,
        }
        try:
            new_id = submit_classification(
                conn, payload, allow_overwrite=overwrite
            )
        except FormError as exc:
            st.error(str(exc))
            for field, msg in exc.field_errors.items():
                st.caption(f"• {field}: {msg}")
            return
        st.success(f"Classification #{new_id} saved.")
