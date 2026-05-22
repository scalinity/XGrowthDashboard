"""Inspiration Library — spec.md §14.13 (Phase 5.11).

Capture-then-remix workflow for external X content. Daniel saves posts
he liked (paste-driven; no scraping) and runs transform modes against
them — structure / hook_pattern / counterpoint / original_version /
voice_profile_version / expand / compress. Each transform produces
text + a deterministic plagiarism risk read.

Load-bearing UI rule (§28.29): a transform with
``plagiarism_risk_label == 'high'`` has its "Send to drafts" affordance
DISABLED until Daniel checks an "I've reviewed the overlap, intentional"
box. Checking that box logs an ``inspiration_plagiarism_override``
audit row carrying the reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import inspiration as _ins
from app.components.theme import apply_theme, hairline, kicker
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap.
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults = {
        "inspiration_selected_id": None,
        "inspiration_save_error": None,
        "inspiration_transform_error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Callbacks.
# ---------------------------------------------------------------------------
def _save_inspiration_cb() -> None:
    text = st.session_state.get("ins_save_text", "").strip()
    url = st.session_state.get("ins_save_url", "").strip() or None
    author = st.session_state.get("ins_save_author", "").strip() or None
    tags_raw = st.session_state.get("ins_save_tags", "").strip()
    notes = st.session_state.get("ins_save_notes", "").strip() or None
    if not text:
        st.session_state["inspiration_save_error"] = (
            "source post text is required."
        )
        return
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] or None
    try:
        with open_connection() as conn:
            new_id = _ins.save_inspiration(
                conn,
                source_post_text=text,
                source_url=url,
                source_author=author,
                tags=tags,
                notes=notes,
            )
        st.session_state["inspiration_save_error"] = None
        st.session_state["inspiration_selected_id"] = new_id
        # Reset the form inputs.
        for k in (
            "ins_save_text",
            "ins_save_url",
            "ins_save_author",
            "ins_save_tags",
            "ins_save_notes",
        ):
            if k in st.session_state:
                st.session_state[k] = ""
    except _ins.DuplicateInspirationError as exc:
        st.session_state["inspiration_save_error"] = str(exc)
    except _ins.InspirationError as exc:
        st.session_state["inspiration_save_error"] = str(exc)


def _run_transform_cb(*, saved_inspiration_id: int, mode: str) -> None:
    try:
        with open_connection() as conn:
            _ins.transform(
                conn,
                saved_inspiration_id=saved_inspiration_id,
                mode=mode,  # type: ignore[arg-type]
            )
        st.session_state["inspiration_transform_error"] = None
    except (_ins.InspirationError, _ins.TransformError) as exc:
        st.session_state["inspiration_transform_error"] = str(exc)


def _select_inspiration_cb(*, inspiration_id: int) -> None:
    st.session_state["inspiration_selected_id"] = inspiration_id


def _archive_inspiration_cb(*, inspiration_id: int) -> None:
    with open_connection() as conn:
        _ins.archive_inspiration(conn, inspiration_id=inspiration_id)
    if st.session_state.get("inspiration_selected_id") == inspiration_id:
        st.session_state["inspiration_selected_id"] = None


def _override_high_risk_cb(*, transform_id: int) -> None:
    reason = st.session_state.get(
        f"override_reason_{transform_id}", ""
    ).strip()
    if not reason:
        st.toast("override reason is required.", icon="⚠")
        return
    with open_connection() as conn:
        _ins.record_plagiarism_override(
            conn, transform_id=transform_id, reason=reason
        )
    st.toast("override audit-logged.")


# ---------------------------------------------------------------------------
# Render helpers.
# ---------------------------------------------------------------------------
def _risk_chip(label: str) -> str:
    color = {"low": "#7cc88a", "medium": "#d8c46b", "high": "#d97e7e"}.get(
        label, "#9b9b9b"
    )
    return (
        f"<span style='background:{color};color:#0f0f0f;"
        "padding:0.1rem 0.5rem;border-radius:0.4rem;"
        "font-size:0.78rem;font-weight:600;'>"
        f"risk: {label}</span>"
    )


# ---------------------------------------------------------------------------
# Main render.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_session_state()
    st.title("Inspiration Library")
    st.markdown(
        "Save external posts; run transform modes against them. "
        "Plagiarism guard is deterministic-first — the AI cannot "
        "underreport (§28.29 load-bearing rule). See §14.13."
    )

    with open_connection() as conn:
        inspirations = _ins.list_inspirations(conn, status="active")

    summary_cols = st.columns(3)
    summary_cols[0].metric("saved", str(len(inspirations)))
    selected_id = st.session_state.get("inspiration_selected_id")

    # ---- Save form ----
    with st.expander("+ save inspiration", expanded=not inspirations):
        if st.session_state.get("inspiration_save_error"):
            st.error(st.session_state["inspiration_save_error"])
        with st.form("ins_save_form"):
            st.text_area(
                "source post text (required)",
                key="ins_save_text",
                height=120,
            )
            st.text_input("source url", key="ins_save_url")
            st.text_input("source author handle", key="ins_save_author")
            st.text_input(
                "tags (comma-separated)",
                key="ins_save_tags",
                placeholder="hook, self-deprecation, neuro",
            )
            st.text_area("why you saved it", key="ins_save_notes", height=60)
            st.form_submit_button(
                "save inspiration", on_click=_save_inspiration_cb
            )

    hairline()

    if not inspirations:
        st.markdown(
            "<div class='dim'>no saved inspirations yet — "
            "save your first above.</div>",
            unsafe_allow_html=True,
        )
        return

    # ---- Two-column layout: sidebar + main ----
    side_col, main_col = st.columns([1, 2])

    with side_col:
        kicker("saved")
        for entry in inspirations:
            label = entry["source_author"] or "(no author)"
            preview = (entry["source_post_text"] or "")[:60]
            button_label = f"{label}\n\n{preview}…"
            st.button(
                button_label,
                key=f"select_ins_{entry['id']}",
                on_click=_select_inspiration_cb,
                kwargs={"inspiration_id": entry["id"]},
            )

    with main_col:
        if selected_id is None:
            st.markdown(
                "<div class='dim'>pick an inspiration on the left.</div>",
                unsafe_allow_html=True,
            )
            return

        selected = next(
            (e for e in inspirations if e["id"] == selected_id), None
        )
        if selected is None:
            st.markdown(
                "<div class='dim'>selected inspiration not found.</div>",
                unsafe_allow_html=True,
            )
            return

        kicker(f"#{selected['id']} · {selected['source_author'] or 'no author'}")
        st.markdown(
            f"_saved {selected['saved_at_utc']}_"
            + (
                f" · [link]({selected['source_url']})"
                if selected["source_url"]
                else ""
            )
        )
        if selected["tags"]:
            st.markdown(
                "tags: " + " ".join(f"`{t}`" for t in selected["tags"])
            )
        st.text_area(
            "source post text",
            value=selected["source_post_text"],
            disabled=True,
            height=120,
            key=f"source_text_{selected['id']}",
        )
        if selected["notes"]:
            st.markdown(f"_notes:_ {selected['notes']}")

        st.button(
            "archive",
            key=f"archive_{selected['id']}",
            on_click=_archive_inspiration_cb,
            kwargs={"inspiration_id": selected["id"]},
        )

        hairline()
        kicker("Transforms")
        if st.session_state.get("inspiration_transform_error"):
            st.error(st.session_state["inspiration_transform_error"])

        mode_cols = st.columns(4)
        for i, mode in enumerate(_ins.TRANSFORM_MODES):
            with mode_cols[i % 4]:
                st.button(
                    f"run {mode}",
                    key=f"run_transform_{selected['id']}_{mode}",
                    on_click=_run_transform_cb,
                    kwargs={
                        "saved_inspiration_id": selected["id"],
                        "mode": mode,
                    },
                )

        with open_connection() as conn:
            transforms = _ins.list_transforms(
                conn, saved_inspiration_id=selected["id"]
            )
            # P511R-5: read override state once for every high-risk
            # transform so the disabled-flag check below has fresh data
            # after each rerun. has_been_overridden goes against audit_
            # logs (server-side; agent has no access per §28.30).
            override_state: dict[int, bool] = {
                int(t["id"]): _ins.has_been_overridden(
                    conn, transform_id=int(t["id"])
                )
                for t in transforms
                if t["plagiarism_risk_label"] == "high"
            }
        if not transforms:
            st.markdown(
                "<div class='dim'>no transforms yet for this inspiration.</div>",
                unsafe_allow_html=True,
            )
        for t in transforms:
            risk = t["plagiarism_risk_label"]
            with st.expander(
                f"#{t['id']} · {t['transform_mode']} · "
                f"jaccard {t['jaccard_similarity']:.2f} · "
                f"ngram {t['longest_shared_ngram_length']}"
            ):
                st.markdown(
                    f"{_risk_chip(risk)} &nbsp; "
                    f"<span class='dim'>"
                    f"ai-reported: {t['ai_reported_risk_label']}"
                    f"</span>",
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "output text",
                    value=t["output_text"],
                    disabled=True,
                    height=140,
                    key=f"output_text_{t['id']}",
                )
                if risk == "low":
                    st.button(
                        "send to drafts",
                        key=f"send_drafts_{t['id']}",
                        help="Promotes this transform to agent_drafts (downstream pipeline).",
                    )
                elif risk == "medium":
                    st.warning(
                        "Medium overlap with source. Review carefully before "
                        "shipping."
                    )
                    st.button(
                        "send to drafts",
                        key=f"send_drafts_{t['id']}",
                    )
                else:
                    # P511R-5: high-risk gate flips off once Daniel
                    # records an override (audit-logged). has_been_
                    # overridden was just read above against audit_logs.
                    is_overridden = override_state.get(int(t["id"]), False)
                    if is_overridden:
                        st.warning(
                            "HIGH plagiarism risk — you acknowledged the "
                            "overlap, so 'Send to drafts' is enabled. The "
                            "override is recorded in the audit log."
                        )
                        st.button(
                            "send to drafts",
                            key=f"send_drafts_{t['id']}",
                            help="High-risk gate lifted by your prior override.",
                        )
                    else:
                        st.error(
                            "HIGH plagiarism risk — deterministic Jaccard/n-gram "
                            "exceeded threshold (§28.29). 'Send to drafts' is "
                            "disabled until you acknowledge the overlap below."
                        )
                        st.text_input(
                            "override reason (required to acknowledge)",
                            key=f"override_reason_{t['id']}",
                        )
                        st.button(
                            "acknowledge high-risk override",
                            key=f"override_{t['id']}",
                            on_click=_override_high_risk_cb,
                            kwargs={"transform_id": t["id"]},
                        )
                        st.button(
                            "send to drafts",
                            key=f"send_drafts_{t['id']}",
                            disabled=True,
                            help=(
                                "Disabled by §28.29 high-risk gate. Override + "
                                "audit-log first."
                            ),
                        )


main()
