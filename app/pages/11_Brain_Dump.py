"""Brain Dump — spec.md §14.9.

Capture-first surface, distinct from §14.8 Agent Chat. Daniel pastes raw
thinking; the agent processes it into clarifying questions + ≤N
structured candidate drafts. The page's cognitive contract — *capture
before evaluating* — is reinforced by the layout: a generous workspace
textarea at the top, the original paste preserved as a dashed-keyline
"specimen" block after processing (signaling "this is preserved, not a
field"), and candidates rendered as phosphor-keyline cards beneath.
Promotion to ``agent_drafts`` is an explicit per-candidate click that
invokes ``_save_draft_post`` and runs the full Phase 5.8 pipeline
downstream (§28.22 contract).

Side-effects discipline (CLAUDE.md): mutate ``st.session_state`` only in
explicit ``on_click`` / ``on_submit`` callbacks; render flow stays a
pure derivation from session state + DB reads. The "active dump"
identity is the single piece of session state this page owns; everything
else round-trips through ``brain_dumps`` / ``agent_drafts``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import brain_dump as _brain_dump
from app.agent import niche as _niche
from app.agent import tools as _tools
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    candidate_card,
    hairline,
    kicker,
    specimen_block,
    status_chip,
)
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap (CLAUDE.md Streamlit side-effects rule).
# ---------------------------------------------------------------------------
def _init_state() -> None:
    """Initialize the page's session-state keys exactly once per session.

    The page owns three keys:

    * ``brain_dump_active_id`` — the dump currently loaded into the main
      panel. ``None`` means "new dump editor" is open instead.
    * ``brain_dump_textarea`` — the new-dump editor's current draft text.
      Cleared on successful save so the textarea doesn't keep stale
      content across reruns.
    * ``brain_dump_sent_candidates`` — a dict of
      ``{dump_id: set_of_indices}`` tracking which candidates Daniel has
      already promoted, so the per-candidate button toggles to a "sent"
      chip instead of disappearing. Survives reruns within the session.
    """
    st.session_state.setdefault("brain_dump_active_id", None)
    st.session_state.setdefault("brain_dump_textarea", "")
    st.session_state.setdefault("brain_dump_sent_candidates", {})
    st.session_state.setdefault("brain_dump_processing_error", None)


# ---------------------------------------------------------------------------
# Explicit on_click handlers — every mutation lives here.
# ---------------------------------------------------------------------------
def _handle_create_and_process() -> None:
    """Insert a new brain_dumps row + immediately run processing.

    Runs inside an ``on_click`` so the Streamlit rerun cycle sees a
    consistent state (new id committed before the page re-renders).
    """
    raw_text = st.session_state.get("brain_dump_textarea", "").strip()
    if not raw_text:
        st.session_state["brain_dump_processing_error"] = "raw text is empty"
        return

    with open_connection() as conn:
        try:
            dump_id = _brain_dump.create_dump(conn, raw_text=raw_text)
        except _brain_dump.BrainDumpError as exc:
            st.session_state["brain_dump_processing_error"] = str(exc)
            return

        st.session_state["brain_dump_active_id"] = dump_id
        st.session_state["brain_dump_textarea"] = ""
        st.session_state["brain_dump_processing_error"] = None
        try:
            _brain_dump.process(conn, dump_id)
        except _brain_dump.BrainDumpError as exc:
            # The row's status is already 'failed' by the time the
            # exception bubbles. Carry the message so the view can
            # show the retry banner.
            st.session_state["brain_dump_processing_error"] = str(exc)


def _handle_retry_processing(dump_id: int) -> None:
    """Retry a failed dump — reuses the same row (no duplicates per §28.22)."""
    st.session_state["brain_dump_processing_error"] = None
    with open_connection() as conn:
        try:
            _brain_dump.process(conn, dump_id)
        except _brain_dump.BrainDumpError as exc:
            st.session_state["brain_dump_processing_error"] = str(exc)


def _handle_promote_candidate(dump_id: int, candidate_idx: int) -> None:
    """Promote one candidate to agent_drafts via _save_draft_post.

    Full Phase 5.8 pipeline runs downstream (IWH preflight, content-type
    validation, pre-publish scorer, repetition guard) per §28.22.
    """
    sent = st.session_state.setdefault("brain_dump_sent_candidates", {})
    already_sent = sent.get(dump_id, set())
    if candidate_idx in already_sent:
        return

    with open_connection() as conn:
        dump = _brain_dump.get_dump(conn, dump_id)
        candidates = dump.get("candidate_drafts", [])
        if candidate_idx >= len(candidates):
            st.session_state["brain_dump_processing_error"] = (
                f"candidate index {candidate_idx} out of range"
            )
            return
        c = candidates[candidate_idx]
        try:
            _tools._save_draft_post(  # noqa: SLF001 — same-package call
                conn,
                text=c["text"],
                pillar=c["pillar"],
                audience=c["audience"],
                cta=c["cta"],
                content_type=c["content_type"],
                agent_reasoning=(
                    f"promoted from brain_dump #{dump_id} candidate "
                    f"{candidate_idx} — rationale: {c.get('rationale','')}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — surface any pipeline failure
            st.session_state["brain_dump_processing_error"] = (
                f"promotion failed: {type(exc).__name__}: {exc}"
            )
            return

    sent.setdefault(dump_id, set()).add(candidate_idx)
    st.session_state["brain_dump_sent_candidates"] = sent


def _handle_load_dump(dump_id: int) -> None:
    st.session_state["brain_dump_active_id"] = dump_id
    st.session_state["brain_dump_processing_error"] = None


def _handle_new_dump() -> None:
    st.session_state["brain_dump_active_id"] = None
    st.session_state["brain_dump_processing_error"] = None


def _handle_save_notes(dump_id: int, notes_key: str) -> None:
    notes = st.session_state.get(notes_key, "")
    with open_connection() as conn:
        _brain_dump.update_notes(conn, dump_id, notes=notes)


# ---------------------------------------------------------------------------
# Render helpers.
# ---------------------------------------------------------------------------
def _render_status_line(dump: dict) -> None:
    """Render the "Status: …" line above the main panel."""
    status = dump["status"]
    tones = {
        "unprocessed": ("unprocessed", "neutral"),
        "processing":  ("processing…", "active"),
        "processed":   ("processed",   "done"),
        "failed":      ("failed",      "failed"),
    }
    label, tone = tones.get(status, (status, "neutral"))
    when = dump.get("processed_at_utc") or dump.get("created_at_utc") or ""
    tokens = dump.get("tokens_used") or 0
    meta_html = (
        f"<span class='dim' style='margin-left:0.5rem; font-size:0.82rem;'>"
        f"<span class='numeric'>{tokens}</span> tokens · {when}</span>"
        if status == "processed"
        else f"<span class='dim' style='margin-left:0.5rem; font-size:0.82rem;'>{when}</span>"
    )
    st.markdown(
        f"<div style='margin-bottom:0.7rem;'>{status_chip(label, tone=tone)}"
        f"{meta_html}</div>",
        unsafe_allow_html=True,
    )


def _render_sidebar(dumps: list[dict]) -> None:
    """Past-dumps sidebar — newest first."""
    st.markdown("### past dumps")
    if not dumps:
        st.markdown(
            "<div class='faint'>nothing captured yet</div>",
            unsafe_allow_html=True,
        )
        return

    active_id = st.session_state.get("brain_dump_active_id")

    # "New dump" pinned at the top of the rail.
    st.button(
        "+ new dump",
        key="brain_dump_new",
        on_click=_handle_new_dump,
        type="primary" if active_id is None else "secondary",
        use_container_width=True,
    )
    st.markdown("<hr class='hairline' style='margin:0.6rem 0;' />", unsafe_allow_html=True)

    for d in dumps:
        is_active = d["id"] == active_id
        first_line = (d["raw_text"] or "").splitlines()[0] if d["raw_text"] else ""
        truncated = first_line[:64] + ("…" if len(first_line) > 64 else "")
        tone = {
            "unprocessed": "neutral",
            "processing": "active",
            "processed": "done",
            "failed": "failed",
        }.get(d["status"], "neutral")
        chip_html = status_chip(d["status"], tone=tone)
        border = PALETTE["phosphor"] if is_active else PALETTE["hairline"]
        title_color = PALETTE["bone"] if is_active else PALETTE["bone_dim"]
        st.markdown(
            f"""<div style='border-left:2px solid {border};
                            padding:0.35rem 0.6rem; margin:0.25rem 0;'>
                <div style='display:flex; justify-content:space-between;
                             align-items:center; gap:0.4rem;'>
                    <span class='numeric' style='font-size:0.72rem;
                                                   color:{PALETTE['bone_faint']};'>
                        #{d['id']}
                    </span>
                    {chip_html}
                </div>
                <div style='font-size:0.86rem; color:{title_color};
                             margin-top:0.2rem;
                             overflow:hidden; text-overflow:ellipsis;
                             white-space:nowrap;'>{truncated or '(empty)'}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.button(
            "load",
            key=f"brain_dump_load_{d['id']}",
            on_click=_handle_load_dump,
            args=(d["id"],),
            use_container_width=True,
        )


def _render_editor() -> None:
    """The "new dump" editor — large textarea + Process button."""
    kicker("CAPTURE")
    st.markdown(
        "<div style='font-family: Fraunces, IBM Plex Serif, Georgia, serif; "
        "font-style: italic; font-size: 1.05rem; "
        f"color:{PALETTE['bone_dim']}; margin: -0.2rem 0 1rem 0;'>"
        "Paste raw thinking. Process turns the mess into clarifying "
        "questions and candidate drafts — promotion is your call.</div>",
        unsafe_allow_html=True,
    )

    with st.form(key="brain_dump_form", clear_on_submit=False, border=False):
        st.text_area(
            label="raw text",
            key="brain_dump_textarea",
            height=320,
            label_visibility="collapsed",
            placeholder=(
                "kitchen-scanner missed the difference between ginger and "
                "soap again. third time this week. wondering if Cook Mode "
                "should get an 'is this what you meant?' pass…"
            ),
        )
        st.form_submit_button(
            "process this dump",
            type="primary",
            on_click=_handle_create_and_process,
        )

    err = st.session_state.get("brain_dump_processing_error")
    if err:
        st.markdown(
            f"<div style='padding:0.6rem 0.9rem; margin-top:0.5rem;"
            f"background:{PALETTE['surface']};"
            f"border-left:2px solid {PALETTE['warn_amber']};'>"
            f"<span style='color:{PALETTE['warn_amber']}; font-weight:500;'>"
            f"processing error</span> "
            f"<span class='dim' style='font-size:0.85rem;'>{err}</span></div>",
            unsafe_allow_html=True,
        )


def _render_dump_detail(dump: dict) -> None:
    """Active dump's main panel — raw text + clarifying Qs + candidates."""
    kicker(f"DUMP #{dump['id']}")
    _render_status_line(dump)

    # The raw paste as an immutable specimen card — the dashed left
    # keyline visually signals "preserved, not editable" (§28.22).
    st.markdown("##### raw paste")
    specimen_block(dump["raw_text"], max_height_rem=12.0)

    status = dump["status"]
    if status == "unprocessed":
        callout(
            "<em>this dump hasn't been processed yet.</em> "
            "It was inserted directly via the agent tool or an earlier "
            "session — click below to run the structured-output pass."
        )
        st.button(
            "process now",
            key=f"brain_dump_process_now_{dump['id']}",
            type="primary",
            on_click=_handle_retry_processing,
            args=(dump["id"],),
        )
        return

    if status == "processing":
        # In practice the click-handler runs synchronously, so a row
        # rarely sits here — but if a future async path drops one,
        # render a phosphor-edged "in flight" banner.
        st.markdown(
            f"<div style='padding:0.6rem 0.9rem;"
            f"background:{PALETTE['surface']};"
            f"border-left:2px solid {PALETTE['phosphor']};'>"
            f"<span style='color:{PALETTE['phosphor']};'>processing…</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        return

    if status == "failed":
        err_msg = (dump.get("notes") or "processing failed").replace(
            "processing failed:", ""
        ).strip()
        st.markdown(
            f"<div style='padding:0.7rem 0.9rem; margin:0.5rem 0;"
            f"background:{PALETTE['surface']};"
            f"border-left:2px solid {PALETTE['warn_amber']};'>"
            f"<span style='color:{PALETTE['warn_amber']}; font-weight:500;'>"
            f"processing failed.</span> "
            f"<span class='dim' style='font-size:0.85rem;'>{err_msg}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.button(
            "retry processing",
            key=f"brain_dump_retry_{dump['id']}",
            type="primary",
            on_click=_handle_retry_processing,
            args=(dump["id"],),
        )
        return

    # status == 'processed'
    questions = dump.get("clarifying_questions") or []
    candidates = dump.get("candidate_drafts") or []
    sent_set = st.session_state.get("brain_dump_sent_candidates", {}).get(
        dump["id"], set()
    )

    st.markdown("##### clarifying questions")
    if not questions:
        st.markdown(
            "<div class='faint'>(none — the dump was clear enough)</div>",
            unsafe_allow_html=True,
        )
    else:
        for q in questions:
            safe_q = q.replace("<", "&lt;").replace(">", "&gt;")
            st.markdown(
                f"<div style='padding:0.3rem 0.7rem; margin:0.2rem 0;"
                f"border-left:2px solid {PALETTE['phosphor_dim']};"
                f"font-family: Fraunces, IBM Plex Serif, Georgia, serif;"
                f"font-size:0.98rem; color:{PALETTE['bone']};"
                f"font-style: italic;'>{safe_q}</div>",
                unsafe_allow_html=True,
            )

    st.markdown(f"##### candidate drafts ({len(candidates)})")
    if not candidates:
        st.markdown(
            "<div class='faint'>(no candidates — the dump was too thin "
            "to draft from. Add detail and re-run.)</div>",
            unsafe_allow_html=True,
        )
    else:
        for i, c in enumerate(candidates):
            status_label = "sent to drafts" if i in sent_set else None
            candidate_card(
                index=i + 1,
                text=c.get("text", ""),
                pillar=c.get("pillar", "—"),
                audience=c.get("audience", "—"),
                cta=c.get("cta", "—"),
                content_type=c.get("content_type", "—"),
                rationale=c.get("rationale", ""),
                status_label=status_label,
            )
            if i not in sent_set:
                st.button(
                    f"send candidate {i + 1} to drafts",
                    key=f"brain_dump_send_{dump['id']}_{i}",
                    type="primary",
                    on_click=_handle_promote_candidate,
                    args=(dump["id"], i),
                )

    hairline()
    notes_key = f"brain_dump_notes_{dump['id']}"
    # Initialize the textarea with the row's current notes EXACTLY ONCE
    # per dump load — re-initializing on every rerun would clobber
    # mid-edit input.
    if notes_key not in st.session_state:
        st.session_state[notes_key] = dump.get("notes") or ""
    st.markdown("##### notes")
    st.markdown(
        "<div class='faint' style='margin:-0.3rem 0 0.4rem 0;'>"
        "raw paste is immutable. Use notes to record what you acted on, "
        "what you deferred, or any retrospective annotation.</div>",
        unsafe_allow_html=True,
    )
    st.text_area(
        label="notes",
        key=notes_key,
        height=120,
        label_visibility="collapsed",
    )
    st.button(
        "save notes",
        key=f"brain_dump_save_notes_btn_{dump['id']}",
        on_click=_handle_save_notes,
        args=(dump["id"], notes_key),
    )


def _render_niche_warning() -> None:
    """If niche isn't defined, surface a phosphor-edged callout.

    The Brain Dump itself runs without niche set (the prompt accepts
    `(not yet defined)`), but the §14.9 → "Send to drafts" path then
    refuses on §28.2 rule #15 — Daniel would hit a downstream wall.
    Better to warn here before he invests in processing.
    """
    callout(
        "<em>niche is not yet defined</em> — drafting is blocked by "
        "§28.2 rule #15 until <strong>Settings → Growth Agent → "
        "Niche</strong> is filled. The Brain Dump will still process "
        "your text, but candidate promotion will fail."
    )


# ---------------------------------------------------------------------------
# Page entrypoint.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_state()

    st.title("brain dump")

    with open_connection() as conn:
        niche_defined = _niche.is_niche_defined(conn)
        dumps = _brain_dump.list_dumps(conn, limit=50)
        active_id = st.session_state.get("brain_dump_active_id")
        active_dump = (
            _brain_dump.get_dump(conn, active_id) if active_id is not None else None
        )

    if not niche_defined:
        _render_niche_warning()

    rail, main_col = st.columns([1, 3], gap="large")
    with rail:
        _render_sidebar(dumps)
    with main_col:
        if active_dump is None:
            _render_editor()
        else:
            _render_dump_detail(active_dump)


main()
