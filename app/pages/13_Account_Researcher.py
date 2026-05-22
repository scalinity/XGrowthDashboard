"""Account Researcher — spec.md §29.7 / §28.24.

Strategic analysis of a target X account: posting patterns,
positioning, reply-strategy entry points, niche alignment with Daniel.
Manual-paste workflow for MVP (V1.1+ adds X API auto-pull).

Spec note: §25 calls for this surface as a "tab inside §29.7 Reply
Target Queue." We ship it as a sibling Streamlit page instead — same
position in the sidebar nav, but without restructuring the 615-line
queue page to host an st.tabs() container. The §29.7 ↔ §28.24 link
remains intact via the bidirectional ``account_research_reports.
linked_reply_target_id`` column: "Generate reply target" on a report
inserts a row into the Queue and stamps the back-reference. Daniel
can navigate either direction from either page.

Aesthetic direction (frontend-design skill): "research dossier."
Past-handles rail on the left (one entry per handle, latest-report
date + count); main panel either an empty paste form OR the
report-detail view with four labeled sub-cards mapping to the
analysis_json schema. The compare-to-previous diff appears below the
detail when ≥2 reports exist for the same handle. The "Generate
reply target" button is the only mutator on this surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import account_research as _ar
from app.agent import niche as _niche
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    hairline,
    kicker,
    specimen_block,
    status_chip,
)
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap.
# ---------------------------------------------------------------------------
def _init_state() -> None:
    st.session_state.setdefault("ar_active_handle", None)
    st.session_state.setdefault("ar_active_report_id", None)
    st.session_state.setdefault("ar_form_handle", "")
    st.session_state.setdefault("ar_form_bio", "")
    st.session_state.setdefault("ar_form_recent", "")
    st.session_state.setdefault("ar_form_url", "")
    st.session_state.setdefault("ar_form_display_name", "")
    st.session_state.setdefault("ar_processing_error", None)
    st.session_state.setdefault("ar_last_generated_reply_target", None)


# ---------------------------------------------------------------------------
# Handlers.
# ---------------------------------------------------------------------------
def _handle_run_analysis() -> None:
    handle = st.session_state.get("ar_form_handle", "").strip()
    bio = st.session_state.get("ar_form_bio", "")
    recent = st.session_state.get("ar_form_recent", "")
    url = st.session_state.get("ar_form_url", "").strip() or None
    display_name = st.session_state.get("ar_form_display_name", "").strip() or None

    if not handle:
        st.session_state["ar_processing_error"] = "target handle is required"
        return
    if not recent.strip():
        st.session_state["ar_processing_error"] = (
            "paste at least one recent post (one per `---` separator)"
        )
        return

    st.session_state["ar_processing_error"] = None

    with open_connection() as conn:
        niche = _niche.get_niche(conn)
        try:
            analysis = _ar.analyze(
                target_handle=handle,
                target_bio_text=bio,
                target_recent_posts_text=recent,
                daniel_niche_problem=niche.problem,
                daniel_niche_person=niche.person,
                target_url=url,
                target_display_name=display_name,
            )
        except _ar.AccountResearchError as exc:
            st.session_state["ar_processing_error"] = str(exc)
            return
        report_id = _ar.save(
            conn,
            analysis=analysis,
            target_bio_snapshot=bio,
            target_recent_posts_text=recent,
            target_url=url,
            target_display_name=display_name,
        )
        st.session_state["ar_active_handle"] = analysis.target_handle
        st.session_state["ar_active_report_id"] = report_id
        # Clear the form so the next analysis starts clean.
        st.session_state["ar_form_handle"] = ""
        st.session_state["ar_form_bio"] = ""
        st.session_state["ar_form_recent"] = ""
        st.session_state["ar_form_url"] = ""
        st.session_state["ar_form_display_name"] = ""


def _handle_select_handle(handle: str) -> None:
    """Select a handle from the rail — load its latest report."""
    with open_connection() as conn:
        reports = _ar.list_reports_for_handle(conn, handle, limit=1)
    st.session_state["ar_active_handle"] = handle
    st.session_state["ar_active_report_id"] = reports[0]["id"] if reports else None
    st.session_state["ar_processing_error"] = None


def _handle_select_report(report_id: int) -> None:
    st.session_state["ar_active_report_id"] = int(report_id)
    st.session_state["ar_processing_error"] = None


def _handle_clear_selection() -> None:
    st.session_state["ar_active_handle"] = None
    st.session_state["ar_active_report_id"] = None
    st.session_state["ar_processing_error"] = None


def _handle_generate_reply_target(report_id: int) -> None:
    with open_connection() as conn:
        try:
            rt_id = _ar.generate_reply_target(conn, report_id=report_id)
        except Exception as exc:  # noqa: BLE001
            st.session_state["ar_processing_error"] = (
                f"reply target generation failed: {type(exc).__name__}: {exc}"
            )
            return
    st.session_state["ar_last_generated_reply_target"] = rt_id


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------
def _overlap_chip(score: int) -> str:
    """Return a status chip whose tone reflects the 0-3 overlap score."""
    if score >= 3:
        return status_chip("overlap 3 / 3", tone="done")
    if score == 2:
        return status_chip("overlap 2 / 3", tone="active")
    if score == 1:
        return status_chip("overlap 1 / 3", tone="neutral")
    return status_chip("overlap 0 / 3", tone="neutral")


def _render_section_card(label: str, body_html: str) -> None:
    """Render one labeled sub-card inside the report detail panel."""
    st.markdown(
        f"""<div style='padding:0.7rem 0.9rem; margin:0.4rem 0 0.7rem 0;
                        background:{PALETTE['surface']};
                        border-left:2px solid {PALETTE['phosphor_dim']};
                        border-radius:2px;'>
            <div class='kicker' style='color:{PALETTE['phosphor']};
                                          margin-bottom:0.3rem;'>{label}</div>
            <div style='color:{PALETTE['bone']}; line-height:1.5;
                         font-size:0.93rem;'>{body_html}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bullets(items: list) -> str:
    if not items:
        return "<span class='faint'>(none)</span>"
    return "<ul style='margin:0.1rem 0 0 1rem; padding:0;'>" + "".join(
        f"<li style='margin:0.15rem 0;'>{_escape(str(i))}</li>" for i in items
    ) + "</ul>"


def _render_rail(all_handles: list[dict]) -> None:
    st.markdown("### researched handles")
    st.button(
        "+ new analysis",
        key="ar_new_btn",
        on_click=_handle_clear_selection,
        type="primary" if st.session_state.get("ar_active_handle") is None else "secondary",
        use_container_width=True,
    )
    hairline()

    if not all_handles:
        st.markdown(
            "<div class='faint'>no analyses yet</div>",
            unsafe_allow_html=True,
        )
        return

    active_handle = st.session_state.get("ar_active_handle")
    for h in all_handles:
        handle = h["target_handle"]
        is_active = handle == active_handle
        border = PALETTE["phosphor"] if is_active else PALETTE["hairline"]
        st.markdown(
            f"""<div style='border-left:2px solid {border};
                            padding:0.35rem 0.6rem; margin:0.25rem 0;'>
                <div style='font-family: JetBrains Mono, monospace;
                             font-size:0.86rem;
                             color:{PALETTE['bone'] if is_active else PALETTE['bone_dim']};'>
                    {_escape(handle)}
                </div>
                <div class='faint' style='font-size:0.72rem; margin-top:0.15rem;'>
                    <span class='numeric'>{h['report_count']}</span> report{'s' if h['report_count'] != 1 else ''}
                    · last
                    <span class='numeric'>{h['last_researched_utc'][:10]}</span>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.button(
            "open",
            key=f"ar_open_{handle}",
            on_click=_handle_select_handle,
            args=(handle,),
            use_container_width=True,
        )


def _render_form() -> None:
    kicker("NEW ANALYSIS")
    st.markdown(
        f"<div style='font-family: Fraunces, IBM Plex Serif, Georgia, serif; "
        f"font-style: italic; font-size: 1.05rem; "
        f"color:{PALETTE['bone_dim']}; margin: -0.2rem 0 1rem 0;'>"
        "Should I be in this account's orbit at all, and how?</div>",
        unsafe_allow_html=True,
    )

    with st.form("ar_form", clear_on_submit=False, border=False):
        h_col, u_col = st.columns([2, 3])
        h_col.text_input(
            "target handle",
            key="ar_form_handle",
            placeholder="@some_user",
        )
        u_col.text_input(
            "target URL (optional)",
            key="ar_form_url",
            placeholder="https://x.com/some_user",
        )
        st.text_input(
            "display name (optional)",
            key="ar_form_display_name",
        )
        st.text_area(
            "target bio (optional but recommended)",
            key="ar_form_bio",
            height=80,
            placeholder="paste bio text verbatim — wrapped as untrusted data per §28.2",
        )
        st.text_area(
            "recent posts (required) — one post per `---` separator",
            key="ar_form_recent",
            height=240,
            placeholder=(
                "first post body here\n"
                "---\n"
                "second post body here\n"
                "---\n"
                "third post body here"
            ),
        )
        st.form_submit_button(
            "run analysis",
            type="primary",
            on_click=_handle_run_analysis,
        )

    err = st.session_state.get("ar_processing_error")
    if err:
        st.markdown(
            f"<div style='padding:0.6rem 0.9rem; margin-top:0.5rem;"
            f"background:{PALETTE['surface']};"
            f"border-left:2px solid {PALETTE['warn_amber']};'>"
            f"<span style='color:{PALETTE['warn_amber']}; font-weight:500;'>"
            f"analysis error</span> "
            f"<span class='dim'>{_escape(err)}</span></div>",
            unsafe_allow_html=True,
        )


def _render_report_detail(report: dict, history: list[dict]) -> None:
    analysis = report.get("analysis") or {}
    if not analysis:
        st.warning("report's analysis_json failed to parse — open the row in sqlite-utils.")
        return

    kicker(f"REPORT #{report['id']} · {report['target_handle']}")
    overlap = analysis.get("niche_alignment_with_daniel", {}).get("overlap_score", 0)
    meta = (
        f"<div style='margin: 0.1rem 0 1rem 0;'>"
        f"{_overlap_chip(int(overlap))}"
        f"<span class='dim' style='margin-left:0.6rem; font-size:0.82rem;'>"
        f"{report['created_at_utc']} · "
        f"<span class='numeric'>{report.get('tokens_used') or 0}</span> tokens · "
        f"<em>{_escape(report.get('model_used') or '')}</em></span>"
        f"</div>"
    )
    st.markdown(meta, unsafe_allow_html=True)

    pp = analysis.get("posting_patterns", {})
    _render_section_card(
        "POSTING PATTERNS",
        f"<div><strong>cadence:</strong> {_escape(pp.get('cadence', ''))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>topics:</strong>{_bullets(pp.get('topics', []))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>common hooks:</strong>{_bullets(pp.get('common_hooks', []))}</div>",
    )
    pos = analysis.get("positioning", {})
    _render_section_card(
        "POSITIONING",
        f"<div><strong>primary audience:</strong> {_escape(pos.get('primary_audience', ''))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>value proposition:</strong> {_escape(pos.get('value_proposition', ''))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>voice markers:</strong>{_bullets(pos.get('voice_markers', []))}</div>",
    )
    rs = analysis.get("reply_strategy", {})
    _render_section_card(
        "REPLY STRATEGY",
        f"<div><strong>best entry topics:</strong>{_bullets(rs.get('best_entry_topics', []))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>tone to match:</strong> {_escape(rs.get('tone_to_match', ''))}</div>"
        f"<div style='margin-top:0.35rem;'><strong>what to avoid:</strong>{_bullets(rs.get('what_to_avoid', []))}</div>",
    )
    na = analysis.get("niche_alignment_with_daniel", {})
    _render_section_card(
        "NICHE ALIGNMENT WITH DANIEL",
        f"<div><strong>overlap score:</strong> "
        f"<span class='numeric'>{int(na.get('overlap_score', 0))} / 3</span></div>"
        f"<div style='margin-top:0.35rem; font-style: italic; "
        f"font-family: Fraunces, IBM Plex Serif, Georgia, serif;'>"
        f"{_escape(na.get('rationale', ''))}</div>",
    )

    # Generate reply target — the only mutator on this surface.
    last_gen = st.session_state.get("ar_last_generated_reply_target")
    if report.get("linked_reply_target_id"):
        st.markdown(
            f"<div class='callout'>This research already generated reply target "
            f"<span class='numeric'>#{report['linked_reply_target_id']}</span> — "
            f"see <strong>Reply Target Queue</strong> in the sidebar.</div>",
            unsafe_allow_html=True,
        )
    else:
        if last_gen:
            st.markdown(
                f"<div class='callout'>Generated reply target "
                f"<span class='numeric'>#{last_gen}</span>. Open the "
                f"<strong>Reply Target Queue</strong> to score and draft.</div>",
                unsafe_allow_html=True,
            )
        st.button(
            "generate reply target from this research",
            key=f"ar_gen_rt_{report['id']}",
            type="primary",
            on_click=_handle_generate_reply_target,
            args=(report["id"],),
        )

    # Specimen card: the recent posts text Daniel pasted, immutable.
    if report.get("target_recent_posts_text"):
        hairline()
        st.markdown("##### pasted recent posts")
        specimen_block(report["target_recent_posts_text"], max_height_rem=12.0)

    # Compare-to-previous — when there's a prior report for the same
    # handle, show a side-by-side overlap-score diff at minimum.
    if len(history) >= 2:
        hairline()
        st.markdown("##### compare to previous")
        try:
            current_idx = next(i for i, h in enumerate(history) if h["id"] == report["id"])
        except StopIteration:
            current_idx = 0
        prev = history[current_idx + 1] if current_idx + 1 < len(history) else None
        if prev:
            _render_compare_view(report, prev)


def _render_compare_view(current: dict, previous: dict) -> None:
    """Side-by-side score + topics + what-to-avoid diff for the same handle."""
    cur_a = current.get("analysis") or {}
    prev_a = previous.get("analysis") or {}
    cur_score = int(cur_a.get("niche_alignment_with_daniel", {}).get("overlap_score", 0))
    prev_score = int(prev_a.get("niche_alignment_with_daniel", {}).get("overlap_score", 0))
    delta = cur_score - prev_score
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    col_cur, col_prev = st.columns(2)
    with col_cur:
        st.markdown(
            f"<div class='kicker'>CURRENT · {current['created_at_utc'][:10]}</div>"
            f"<div class='numeric' style='font-size:1.6rem;'>overlap "
            f"<span style='color:{PALETTE['phosphor']};'>{cur_score}</span> / 3</div>",
            unsafe_allow_html=True,
        )
    with col_prev:
        st.markdown(
            f"<div class='kicker'>PREVIOUS · {previous['created_at_utc'][:10]}</div>"
            f"<div class='numeric' style='font-size:1.6rem; color:{PALETTE['bone_dim']};'>"
            f"overlap {prev_score} / 3</div>"
            f"<div class='faint' style='font-size:0.78rem; margin-top:0.2rem;'>"
            f"delta: <span class='numeric'>{delta_str}</span></div>",
            unsafe_allow_html=True,
        )

    # Topic diff — sets, not ordered lists, since the model may re-order.
    cur_topics = set(cur_a.get("posting_patterns", {}).get("topics", []))
    prev_topics = set(prev_a.get("posting_patterns", {}).get("topics", []))
    new_topics = cur_topics - prev_topics
    gone_topics = prev_topics - cur_topics
    if new_topics or gone_topics:
        st.markdown(
            "<div class='kicker' style='margin-top:0.6rem;'>TOPIC SHIFTS</div>",
            unsafe_allow_html=True,
        )
        bits: list[str] = []
        if new_topics:
            bits.append(
                f"<div><strong style='color:{PALETTE['phosphor']};'>new:</strong> "
                f"{_escape(', '.join(sorted(new_topics)))}</div>"
            )
        if gone_topics:
            bits.append(
                f"<div><strong class='dim'>no longer mentioned:</strong> "
                f"{_escape(', '.join(sorted(gone_topics)))}</div>"
            )
        st.markdown("".join(bits), unsafe_allow_html=True)


def _render_history_list(history: list[dict], active_report_id: int | None) -> None:
    if len(history) <= 1:
        return
    hairline()
    st.markdown(
        "<div class='kicker'>REPORT HISTORY</div>",
        unsafe_allow_html=True,
    )
    for h in history:
        is_active = h["id"] == active_report_id
        border = PALETTE["phosphor"] if is_active else PALETTE["hairline"]
        score = (
            int((h.get("analysis") or {})
                .get("niche_alignment_with_daniel", {})
                .get("overlap_score", 0))
            if h.get("analysis")
            else 0
        )
        st.markdown(
            f"<div style='border-left:2px solid {border}; padding:0.25rem 0.6rem;"
            f"margin:0.2rem 0;'>"
            f"<span class='numeric' style='font-size:0.76rem;'>#{h['id']}</span> "
            f"<span class='dim'>{h['created_at_utc'][:10]}</span> "
            f"<span class='numeric'>· overlap {score} / 3</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if not is_active:
            st.button(
                "open this report",
                key=f"ar_open_report_{h['id']}",
                on_click=_handle_select_report,
                args=(h["id"],),
            )


def _render_niche_note(niche_defined: bool) -> None:
    if niche_defined:
        return
    callout(
        "<em>niche is not yet defined.</em> The Account Researcher still "
        "runs without it, but <strong>niche_alignment_with_daniel</strong> "
        "will read '(not yet defined)' instead of comparing against your "
        "structural niche. Open <strong>Settings → Growth Agent → Niche</strong>."
    )


# ---------------------------------------------------------------------------
# Page entrypoint.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_state()

    with open_connection() as conn:
        niche_defined = _niche.is_niche_defined(conn)
        all_handles = _ar.list_all_handles(conn)
        active_handle = st.session_state.get("ar_active_handle")
        history: list[dict] = []
        active_report: dict | None = None
        if active_handle:
            history = _ar.list_reports_for_handle(conn, active_handle, limit=20)
            active_report_id = st.session_state.get("ar_active_report_id")
            if active_report_id is None and history:
                active_report_id = history[0]["id"]
            if active_report_id is not None:
                try:
                    active_report = _ar.get_report(conn, active_report_id)
                except _ar.AccountResearchError:
                    active_report = None

    kicker("§29 · ACCOUNT RESEARCHER")
    st.title("account researcher")
    st.caption(
        "Strategic read on a target X account: posting patterns, "
        "positioning, reply-strategy entry points, niche alignment. "
        "Different question from §28.20 replier-pool — that one says "
        "*who's worth replying to within this thread*; this one says "
        "*should I be in this account's orbit at all, and how?*"
    )

    _render_niche_note(niche_defined)

    rail, main_col = st.columns([1, 3], gap="large")
    with rail:
        _render_rail(all_handles)
    with main_col:
        if active_report is not None:
            _render_report_detail(active_report, history)
            _render_history_list(
                history, st.session_state.get("ar_active_report_id")
            )
        else:
            _render_form()


main()
