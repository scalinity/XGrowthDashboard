"""Blog Editor — spec.md §14.15 (Phase 6).

Three-panel layout: outline (left), body (center), agent + version
history + linked posts (right). Status selector enforces legal
transitions; agent actions write versions; revert creates forward-
moving history; export writes a file + DB row + audit; repurpose-to-X
sub-menu calls into Phase 5.8 drafts pipeline via blog_repurposing.

The identity readout in the agent panel is LIVE-bound to fresh DB
reads each rerun — no caching in session state for niche / voice
profile / lore. Voice-profile regen in Settings shows here on next
rerun (§14.15 acceptance criterion).

Streamlit side-effects discipline (CLAUDE.md): every mutation lives
in an explicit callback (`on_click` / `on_change`); render flow only
reads.
"""

from __future__ import annotations

import difflib
import json
import sys
from html import escape as _h
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import blog_drafting as _bd
from app.agent import blog_exports as _be
from app.agent import blog_repurposing as _br
from app.agent import blogs as _blogs
from app.agent import niche as _niche
from app.agent import personality_lore as _personality_lore
from app.agent import voice_profile as _voice_profile
from app.components.theme import apply_theme, hairline, kicker
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap.
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults = {
        "editor_blog_id": None,
        "editor_save_error": None,
        "editor_agent_error": None,
        "editor_agent_result": None,
        "editor_pending_suggestions": [],
        "editor_export_error": None,
        "editor_export_success": None,
        "editor_export_warning": None,
        "editor_auto_open_export": False,
        "editor_repurpose_error": None,
        "editor_repurpose_result": None,
        "editor_repurpose_blocked": None,
        "editor_show_revert_for": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Callbacks.
# ---------------------------------------------------------------------------
def _save_blog_cb(*, blog_id: int) -> None:
    body = st.session_state.get(f"editor_body_{blog_id}", "")
    outline = st.session_state.get(f"editor_outline_{blog_id}", "")
    title = st.session_state.get(f"editor_title_{blog_id}", "").strip()
    if not title:
        st.session_state["editor_save_error"] = "title is required."
        return
    try:
        with open_connection() as conn:
            _blogs.save_blog(
                conn,
                blog_id,
                body_markdown=body,
                outline_markdown=outline,
                title=title,
                created_by="daniel",
            )
        st.session_state["editor_save_error"] = None
    except _blogs.BlogError as exc:
        st.session_state["editor_save_error"] = str(exc)


def _transition_status_cb(*, blog_id: int) -> None:
    new_status = st.session_state.get(f"editor_status_{blog_id}", "")
    external_url = st.session_state.get(f"editor_external_url_{blog_id}", "").strip() or None
    try:
        with open_connection() as conn:
            current = _blogs.get_blog(conn, blog_id).status
            if new_status == current:
                return  # no-op
            _blogs.transition_status(
                conn, blog_id, new_status, external_url=external_url
            )
        st.session_state["editor_save_error"] = None
    except _blogs.BlogError as exc:
        st.session_state["editor_save_error"] = str(exc)
    # P6R-12: clear the persisted selectbox value so it re-initializes
    # from the legal-next-statuses list on the next render. Without
    # this, navigating to a different blog whose legal next-status list
    # doesn't include the persisted value raises StreamlitAPIException.
    if f"editor_status_{blog_id}" in st.session_state:
        del st.session_state[f"editor_status_{blog_id}"]


def _agent_outline_cb(*, blog_id: int) -> None:
    notes = st.session_state.get(f"editor_agent_notes_{blog_id}", "").strip() or None
    try:
        with open_connection() as conn:
            _bd.outline_blog(conn, blog_id=blog_id, daniel_notes=notes)
        st.session_state["editor_agent_error"] = None
        st.session_state["editor_agent_result"] = "outline written"
    except _bd.BlogDraftingError as exc:
        st.session_state["editor_agent_error"] = str(exc)


def _agent_draft_cb(*, blog_id: int) -> None:
    try:
        with open_connection() as conn:
            _bd.draft_blog(conn, blog_id=blog_id)
        st.session_state["editor_agent_error"] = None
        st.session_state["editor_agent_result"] = "draft written"
    except _bd.BlogDraftingError as exc:
        st.session_state["editor_agent_error"] = str(exc)


def _agent_suggest_edits_cb(*, blog_id: int) -> None:
    try:
        with open_connection() as conn:
            result = _bd.suggest_blog_edits(conn, blog_id=blog_id)
        st.session_state["editor_agent_error"] = None
        st.session_state["editor_pending_suggestions"] = [
            {
                "anchor": s.paragraph_anchor,
                "replacement": s.suggested_replacement,
                "rationale": s.rationale,
                "confidence_label": s.confidence_label,
            }
            for s in result.suggestions
        ]
    except _bd.BlogDraftingError as exc:
        st.session_state["editor_agent_error"] = str(exc)


def _agent_seo_cb(*, blog_id: int) -> None:
    try:
        with open_connection() as conn:
            _bd.generate_blog_seo_metadata(conn, blog_id=blog_id)
        st.session_state["editor_agent_error"] = None
        st.session_state["editor_agent_result"] = "SEO metadata written"
    except _bd.BlogDraftingError as exc:
        st.session_state["editor_agent_error"] = str(exc)


def _accept_suggestion_cb(*, blog_id: int, index: int) -> None:
    suggestions = list(st.session_state.get("editor_pending_suggestions", []))
    if index >= len(suggestions):
        return
    sug = suggestions[index]
    try:
        with open_connection() as conn:
            blog = _blogs.get_blog(conn, blog_id)
            body = blog.current_body_markdown or ""
            anchor = sug["anchor"]
            replacement = sug["replacement"]
            # Substring replace on the anchor — UI surfaces the
            # suggestion with the exact anchor the model emitted.
            occurrences = body.count(anchor)
            if occurrences == 0:
                st.session_state["editor_save_error"] = (
                    f"suggestion anchor not found in body: {anchor[:40]}…"
                )
                return
            # P6R-9: if the anchor matches multiple paragraphs (common
            # when headings repeat across sections), reject rather than
            # silently rewriting the FIRST occurrence and surprising
            # Daniel. Surface the ambiguity so he can manually pick
            # which paragraph to rewrite (or re-prompt for a more
            # specific anchor).
            if occurrences > 1:
                st.session_state["editor_save_error"] = (
                    f"suggestion anchor matches {occurrences} paragraphs in the body — "
                    "ambiguous; rewrite the matching paragraph manually or re-prompt "
                    "the agent for a more-specific anchor."
                )
                return
            new_body = body.replace(anchor, replacement, 1)
            _blogs.save_blog(
                conn,
                blog_id,
                body_markdown=new_body,
                created_by="agent",
                agent_action="edit_suggestion_applied",
                confidence_label_at_version=sug.get("confidence_label", "inference"),
            )
        del suggestions[index]
        st.session_state["editor_pending_suggestions"] = suggestions
    except _blogs.BlogError as exc:
        st.session_state["editor_save_error"] = str(exc)


def _reject_suggestion_cb(*, index: int) -> None:
    suggestions = list(st.session_state.get("editor_pending_suggestions", []))
    if 0 <= index < len(suggestions):
        del suggestions[index]
    st.session_state["editor_pending_suggestions"] = suggestions


def _export_cb(*, blog_id: int) -> None:
    fmt = st.session_state.get(f"editor_export_format_{blog_id}", "markdown")
    target = st.session_state.get(f"editor_export_path_{blog_id}", "").strip()
    if not target:
        st.session_state["editor_export_error"] = "target path is required."
        return
    include_seo = bool(st.session_state.get(f"editor_export_seo_{blog_id}", True))
    include_links = bool(st.session_state.get(f"editor_export_links_{blog_id}", False))
    try:
        with open_connection() as conn:
            result = _be.export(
                conn,
                blog_id=blog_id,
                format=fmt,  # type: ignore[arg-type]
                target_path=target,
                include_seo_metadata=include_seo,
                include_repurposing_links=include_links,
            )
        st.session_state["editor_export_error"] = None
        st.session_state["editor_export_success"] = (
            f"exported to {result.target_path} ({result.file_size_bytes} bytes)"
        )
        # P6R-8: surface ready→exported transition failure as a yellow
        # warning. status_transitioned is None ONLY when the transition
        # was attempted (blog was 'ready') AND it failed AFTER the
        # export row landed. The export itself is good; the status just
        # didn't move — Daniel can re-trigger from the status selector.
        if result.status_transitioned is None:
            st.session_state["editor_export_warning"] = (
                f"export succeeded but the ready→exported status transition "
                f"failed. The file at {result.target_path} is valid; "
                "re-trigger the transition manually from the status selector."
            )
        else:
            st.session_state["editor_export_warning"] = None
    except _be.ExportRecordFailedError as exc:
        st.session_state["editor_export_error"] = (
            f"file written to {exc.target_path} BUT export record failed: "
            f"{exc.original}. Mark resolved manually."
        )
    except _be.BlogExportError as exc:
        st.session_state["editor_export_error"] = str(exc)


def _repurpose_cb(*, blog_id: int, mode: str, override: bool = False) -> None:
    try:
        with open_connection() as conn:
            result = _br.repurpose_blog_to_x(
                conn,
                blog_id=blog_id,
                mode=mode,  # type: ignore[arg-type]
                override_plagiarism=override,
            )
        st.session_state["editor_repurpose_error"] = None
        st.session_state["editor_repurpose_blocked"] = None
        st.session_state["editor_repurpose_result"] = (
            f"{len(result.drafts)} draft(s) created in mode={mode}; "
            f"head over to Agent Chat or Manual Entry to review."
        )
    except _br.PlagiarismBlockedError as exc:
        st.session_state["editor_repurpose_blocked"] = {
            "mode": mode,
            "blocked_outputs": exc.blocked_outputs,
        }
        st.session_state["editor_repurpose_error"] = None
        st.session_state["editor_repurpose_result"] = None
    except _br.BlogRepurposingError as exc:
        st.session_state["editor_repurpose_error"] = str(exc)
        # P6R-13: clear the blocked-by-plagiarism banner when a
        # non-plagiarism failure happens on a re-run (e.g. niche became
        # undefined between blocks). Pre-fix the stale blocked banner
        # stayed visible alongside the new error and confused Daniel.
        st.session_state["editor_repurpose_blocked"] = None
        st.session_state["editor_repurpose_result"] = None


def _revert_cb(*, blog_id: int, version_id: int) -> None:
    note = st.session_state.get(f"editor_revert_note_{version_id}", "").strip() or None
    try:
        with open_connection() as conn:
            _blogs.revert_to_version(
                conn, blog_id, version_id, daniel_revision_note=note
            )
        st.session_state["editor_save_error"] = None
        st.session_state["editor_show_revert_for"] = None
    except _blogs.BlogError as exc:
        st.session_state["editor_save_error"] = str(exc)


def _show_revert_cb(*, version_id: int | None) -> None:
    st.session_state["editor_show_revert_for"] = version_id


def _back_to_blogs_cb() -> None:
    try:
        st.switch_page("pages/17_Blogs.py")
    except (AttributeError, st.errors.StreamlitAPIException):  # type: ignore[attr-defined]
        pass


# ---------------------------------------------------------------------------
# Render helpers.
# ---------------------------------------------------------------------------
_STATUS_TONE = {
    "idea":                 ("#3a73e0", "#ffffff"),
    "outlining":            ("#5fb3a1", "#0e1116"),
    "drafting":             ("#5fb3a1", "#0e1116"),
    "editing":              ("#c98b16", "#0e1116"),
    "ready":                ("#2da564", "#0e1116"),
    "exported":             ("#2da564", "#0e1116"),
    "published_externally": ("#7c8aff", "#0e1116"),
    "archived":             ("#2a2f37", "#a8a39a"),
}


def _status_chip(status: str) -> str:
    bg, fg = _STATUS_TONE.get(status, ("#2a2f37", "#a8a39a"))
    return (
        f"<span style='background:{bg};color:{fg};"
        "padding:0.1rem 0.55rem;border-radius:0.35rem;"
        "font-size:0.72rem;font-weight:600;letter-spacing:0.04em;"
        "text-transform:uppercase;'>"
        f"{status.replace('_', ' ')}</span>"
    )


def _confidence_chip(label: str | None) -> str:
    if not label:
        return ""
    palette = {
        "fact":        ("#2da564", "#0e1116"),
        "inference":   ("#5fb3a1", "#0e1116"),
        "speculation": ("#c98b16", "#0e1116"),
        "mixed":       ("#a8a39a", "#0e1116"),
    }
    bg, fg = palette.get(label, ("#2a2f37", "#a8a39a"))
    return (
        f"<span style='background:{bg};color:{fg};"
        "padding:0.08rem 0.45rem;border-radius:0.35rem;"
        "font-size:0.68rem;font-weight:600;'>"
        f"{label}</span>"
    )


def _legal_next_statuses(current: str) -> list[str]:
    """Return statuses reachable from ``current`` via the §28.31 state machine."""
    return [
        target for target in _blogs.VALID_STATUSES
        if _blogs.is_legal_transition(current, target)
    ]


def _diff_lines(old: str, new: str) -> str:
    diff = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile="prior",
            tofile="current",
            lineterm="",
        )
    )
    return "\n".join(diff) if diff else "(no textual diff)"


# ---------------------------------------------------------------------------
# Identity readout (live-bound per §14.15 acceptance criterion).
# ---------------------------------------------------------------------------
def _render_identity_panel(conn) -> None:
    kicker("identity")
    nd = _niche.get_niche(conn)
    if nd.is_defined():
        # P6R-2: niche_person and niche_problem are Daniel-controlled but
        # Settings → Growth Agent → Niche → text input takes free text,
        # so escape defensively before splicing into unsafe_allow_html.
        st.markdown(
            f"<div style='color:#e6e1d8;font-size:0.85rem;'>"
            f"<b>niche:</b> {_h(nd.person)} × {_h(nd.problem)}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.warning(
            "niche is undefined — agent drafting is BLOCKED. "
            "Open Settings → Growth Agent → Niche."
        )

    profile = _voice_profile.get_active(conn)
    if profile is not None:
        desc = profile.self_description() or "(no self-description)"
        truncated = desc[:140] + ("…" if len(desc) > 140 else "")
        st.markdown(
            f"<div style='color:#a8a39a;font-size:0.78rem;margin-top:0.4rem;'>"
            f"<b>voice profile:</b> id={int(profile.id)}, "
            f"window={int(profile.source_post_window_days)}d, "
            f"posts={int(profile.source_post_count)}<br>"
            f"<i>{_h(truncated)}</i></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='dim'>no active voice profile — generate "
            "one in Settings.</div>",
            unsafe_allow_html=True,
        )

    splice_n = _personality_lore.get_splice_count(conn)
    active_lore = _personality_lore.list_active(conn, limit=splice_n)
    st.markdown(
        f"<div style='color:#a8a39a;font-size:0.78rem;'>"
        f"<b>active personality lore:</b> {len(active_lore)} row(s)</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main render.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_session_state()

    st.button("← Back to Blogs", on_click=_back_to_blogs_cb)

    blog_id = st.session_state.get("editor_blog_id")
    if blog_id is None:
        st.title("Blog Editor")
        st.info(
            "No blog selected. Open one from the Blogs index, or create a "
            "new blog there."
        )
        return

    with open_connection() as conn:
        try:
            blog = _blogs.get_blog(conn, blog_id)
        except _blogs.BlogNotFoundError:
            st.error(f"blog #{blog_id} not found.")
            return
        versions = _blogs.list_versions(conn, blog_id)

        # ---- Header ----
        # P6R-2: st.title escapes by default. The unsafe_allow_html markdown
        # below splices blog.slug — slug is _normalize_slug-sanitized to
        # [a-z0-9-] so it's already safe, but escape defensively so a
        # future relaxation of _normalize_slug doesn't open a vector.
        st.title(blog.title)
        header_cols = st.columns([3, 1])
        with header_cols[0]:
            st.markdown(
                f"slug: <code>{_h(blog.slug)}</code> · {_status_chip(blog.status)} "
                f"· {int(blog.actual_length_words)} words"
                + (f" / target {int(blog.target_length_words)}" if blog.target_length_words else "")
                + f" · v.{int(versions[0].version_number) if versions else 0}",
                unsafe_allow_html=True,
            )
        with header_cols[1]:
            # Status selector — bound to legal next statuses.
            legal = [blog.status] + _legal_next_statuses(blog.status)
            st.selectbox(
                "status",
                options=legal,
                index=0,
                key=f"editor_status_{blog_id}",
                on_change=_transition_status_cb,
                kwargs={"blog_id": blog_id},
                label_visibility="collapsed",
            )

        if blog.status == "exported" or blog.status == "published_externally":
            st.text_input(
                "external_url",
                value=conn.execute(
                    "SELECT external_url FROM blogs WHERE id = ?", (blog_id,)
                ).fetchone()[0] or "",
                key=f"editor_external_url_{blog_id}",
            )

        if st.session_state.get("editor_save_error"):
            st.error(st.session_state["editor_save_error"])

        hairline()

        # ---- Three-panel layout ----
        outline_col, body_col, agent_col = st.columns([2, 4, 3])

        with outline_col:
            kicker("outline")
            st.text_input(
                "title",
                value=blog.title,
                key=f"editor_title_{blog_id}",
            )
            st.text_area(
                "outline (Markdown)",
                value=blog.outline_markdown or "",
                key=f"editor_outline_{blog_id}",
                height=400,
            )

        with body_col:
            kicker("body")
            st.text_area(
                "body (Markdown)",
                value=blog.current_body_markdown or "",
                key=f"editor_body_{blog_id}",
                height=480,
            )

        with agent_col:
            _render_identity_panel(conn)
            hairline()

            kicker("agent")
            if st.session_state.get("editor_agent_error"):
                st.error(st.session_state["editor_agent_error"])
            if st.session_state.get("editor_agent_result"):
                st.success(st.session_state["editor_agent_result"])

            st.text_area(
                "your notes for the agent (used by outline)",
                key=f"editor_agent_notes_{blog_id}",
                height=80,
            )
            ag_cols = st.columns(2)
            ag_cols[0].button(
                "outline",
                key=f"agent_outline_btn_{blog_id}",
                on_click=_agent_outline_cb,
                kwargs={"blog_id": blog_id},
            )
            ag_cols[1].button(
                "draft",
                key=f"agent_draft_btn_{blog_id}",
                on_click=_agent_draft_cb,
                kwargs={"blog_id": blog_id},
            )
            ag_cols2 = st.columns(2)
            ag_cols2[0].button(
                "suggest edits",
                key=f"agent_suggest_btn_{blog_id}",
                on_click=_agent_suggest_edits_cb,
                kwargs={"blog_id": blog_id},
            )
            ag_cols2[1].button(
                "SEO",
                key=f"agent_seo_btn_{blog_id}",
                on_click=_agent_seo_cb,
                kwargs={"blog_id": blog_id},
            )

            pending = list(st.session_state.get("editor_pending_suggestions", []))
            if pending:
                hairline()
                kicker(f"pending suggestions ({len(pending)})")
                for i, sug in enumerate(pending):
                    # P6R-2: sug['anchor'] and sug['rationale'] are
                    # model-generated text from the suggest_blog_edits
                    # Claude call — escape before rendering with
                    # unsafe_allow_html. CWE-79.
                    st.markdown(
                        f"<div style='background:#1c2128;padding:0.5rem;"
                        "border-radius:0.35rem;margin-bottom:0.5rem;'>"
                        f"<b>anchor:</b> <code>{_h(sug['anchor'][:60])}…</code><br>"
                        f"<b>rationale:</b> {_h(sug['rationale'])}<br>"
                        f"{_confidence_chip(sug.get('confidence_label'))}</div>",
                        unsafe_allow_html=True,
                    )
                    sug_cols = st.columns(2)
                    sug_cols[0].button(
                        "accept",
                        key=f"sug_accept_{i}",
                        on_click=_accept_suggestion_cb,
                        kwargs={"blog_id": blog_id, "index": i},
                    )
                    sug_cols[1].button(
                        "reject",
                        key=f"sug_reject_{i}",
                        on_click=_reject_suggestion_cb,
                        kwargs={"index": i},
                    )

            # ---- Versions ----
            hairline()
            kicker("versions")
            show_revert_for = st.session_state.get("editor_show_revert_for")
            for v in versions[:10]:
                chip = ""
                if v.confidence_label_at_version:
                    chip = _confidence_chip(v.confidence_label_at_version)
                # P6R-2: version_number/created_by/agent_action/created_at_utc
                # are CHECK-constrained enums or schema-generated values
                # (never free-text) — but escape defensively.
                st.markdown(
                    f"<div style='color:#e6e1d8;font-size:0.82rem;'>"
                    f"v.{int(v.version_number)} · ●{_h(v.created_by)}"
                    + (f" · {_h(v.agent_action)}" if v.agent_action else "")
                    + f" · {_h(v.created_at_utc)} {chip}</div>",
                    unsafe_allow_html=True,
                )
                if not v.is_current_for_blog:
                    if show_revert_for == v.id:
                        st.text_input(
                            "revert note (optional)",
                            key=f"editor_revert_note_{v.id}",
                            placeholder="why are you reverting?",
                        )
                        rcols = st.columns(2)
                        rcols[0].button(
                            "confirm revert",
                            key=f"revert_confirm_{v.id}",
                            on_click=_revert_cb,
                            kwargs={"blog_id": blog_id, "version_id": v.id},
                        )
                        rcols[1].button(
                            "cancel",
                            key=f"revert_cancel_{v.id}",
                            on_click=_show_revert_cb,
                            kwargs={"version_id": None},
                        )
                    else:
                        st.button(
                            "revert here",
                            key=f"revert_show_{v.id}",
                            on_click=_show_revert_cb,
                            kwargs={"version_id": v.id},
                        )

            # ---- Linked posts ----
            linked = conn.execute(
                """
                SELECT btpl.direction, btpl.relationship_kind, p.id AS pid,
                       p.text
                FROM blog_to_post_links btpl
                JOIN posts p ON p.id = btpl.post_id
                WHERE btpl.blog_id = ?
                ORDER BY btpl.created_at_utc DESC
                """,
                (blog_id,),
            ).fetchall()
            if linked:
                hairline()
                kicker(f"linked posts ({len(linked)})")
                for row in linked:
                    # P6R-2: row['text'] is posts.text — saved X content
                    # that can carry arbitrary tweet text including
                    # <script>...</script>. Escape before splicing into
                    # unsafe_allow_html. CWE-79.
                    txt = _h((row["text"] or "")[:80])
                    direction = _h(row["direction"].replace("_", " "))
                    kind = _h(row["relationship_kind"].replace("_", " "))
                    st.markdown(
                        f"<div style='color:#a8a39a;font-size:0.78rem;'>"
                        f"#{int(row['pid'])} · {direction} · "
                        f"{kind}<br>"
                        f"<i>{txt}…</i></div>",
                        unsafe_allow_html=True,
                    )

        hairline()

        # ---- Footer actions ----
        kicker("actions")
        action_cols = st.columns([1, 1, 4, 4])
        action_cols[0].button(
            "save",
            key=f"editor_save_btn_{blog_id}",
            on_click=_save_blog_cb,
            kwargs={"blog_id": blog_id},
        )

        with action_cols[2]:
            # P6R-16: if Daniel clicked the per-row Export button on the
            # Blogs index, auto-open the expander on arrival. One-shot
            # flag — consume it so the expander goes back to closed-by-
            # default on the next render.
            auto_open = bool(st.session_state.get("editor_auto_open_export"))
            if auto_open:
                st.session_state["editor_auto_open_export"] = False
            with st.expander("export ▾", expanded=auto_open):
                _render_export_dialog(blog_id)

        with action_cols[3]:
            with st.expander("repurpose to X ▾", expanded=False):
                _render_repurpose_dialog(blog_id)


def _render_export_dialog(blog_id: int) -> None:
    with open_connection() as conn:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'blog_export_default_directory'"
        ).fetchone()
        # P6R-20: parse defensively — only accept a non-empty JSON
        # string value. Pre-fix, a setting stored as a list/int would
        # parse cleanly and then crash on .rstrip('/'). Type-check
        # after json.loads.
        default_dir = "data/blog_exports/"
        if row is not None and row[0]:
            try:
                parsed = json.loads(row[0])
                if isinstance(parsed, str) and parsed.strip():
                    default_dir = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        blog = _blogs.get_blog(conn, blog_id)

    st.selectbox(
        "format",
        options=("markdown", "html", "json", "mdx"),
        key=f"editor_export_format_{blog_id}",
    )
    fmt = st.session_state.get(f"editor_export_format_{blog_id}", "markdown")
    suffix = {"markdown": ".md", "html": ".html", "json": ".json", "mdx": ".mdx"}[fmt]
    default_path = f"{default_dir.rstrip('/')}/{blog.slug}{suffix}"
    st.text_input(
        "target path",
        value=default_path,
        key=f"editor_export_path_{blog_id}",
    )
    st.checkbox(
        "include SEO metadata",
        value=True,
        key=f"editor_export_seo_{blog_id}",
    )
    st.checkbox(
        "include repurposing notes footer",
        value=False,
        key=f"editor_export_links_{blog_id}",
    )

    if st.session_state.get("editor_export_error"):
        st.error(st.session_state["editor_export_error"])
    if st.session_state.get("editor_export_warning"):
        st.warning(st.session_state["editor_export_warning"])
    if st.session_state.get("editor_export_success"):
        st.success(st.session_state["editor_export_success"])

    st.button(
        "write export",
        key=f"editor_export_confirm_{blog_id}",
        on_click=_export_cb,
        kwargs={"blog_id": blog_id},
    )


def _render_repurpose_dialog(blog_id: int) -> None:
    if st.session_state.get("editor_repurpose_error"):
        st.error(st.session_state["editor_repurpose_error"])
    if st.session_state.get("editor_repurpose_result"):
        st.success(st.session_state["editor_repurpose_result"])

    blocked = st.session_state.get("editor_repurpose_blocked")
    if blocked:
        st.warning(
            f"plagiarism guard blocked {len(blocked['blocked_outputs'])} "
            f"output(s) in mode={blocked['mode']}. Review and override if "
            "you accept the overlap (audit-logged)."
        )
        for item in blocked["blocked_outputs"]:
            # P6R-2: text_excerpt is the agent's repurposed X output —
            # may contain HTML if the model emitted it. Escape.
            st.markdown(
                f"<div style='background:#2a2f37;padding:0.4rem;"
                "border-radius:0.35rem;margin:0.3rem 0;'>"
                f"jaccard={float(item['jaccard_similarity']):.2f} · "
                f"ngram={int(item['longest_shared_ngram_length'])}<br>"
                f"<i>{_h(item['text_excerpt'])}…</i></div>",
                unsafe_allow_html=True,
            )
        st.button(
            "I've reviewed — accept the overlap and re-run",
            key=f"repurpose_override_{blog_id}",
            on_click=_repurpose_cb,
            kwargs={
                "blog_id": blog_id,
                "mode": blocked["mode"],
                "override": True,
            },
        )

    cols = st.columns(3)
    cols[0].button(
        "thread from sections",
        key=f"repurpose_thread_{blog_id}",
        on_click=_repurpose_cb,
        kwargs={"blog_id": blog_id, "mode": "thread_from_sections"},
    )
    cols[1].button(
        "single post summary",
        key=f"repurpose_single_{blog_id}",
        on_click=_repurpose_cb,
        kwargs={"blog_id": blog_id, "mode": "single_post_summary"},
    )
    cols[2].button(
        "teaser + link",
        key=f"repurpose_teaser_{blog_id}",
        on_click=_repurpose_cb,
        kwargs={"blog_id": blog_id, "mode": "teaser_with_link"},
    )


main()
