"""Blogs index — spec.md §14.14 (Phase 6).

Entry point into long-form authoring. Lists every blog with its
pipeline state (status, length-vs-target, version + author, stale-
state highlight, latest confidence chip). Click "Open" → Blog Editor
(§14.15). The actual writing happens in the Editor; this view is
nav + triage.

Unified identity reminder: the agent's niche, voice profile, voice
samples, and personality lore feed blog drafting exactly as they
feed X drafting — the point of putting blogs in XGrowth instead of
a separate tool is precisely this unified identity surface (§28.31).
"""

from __future__ import annotations

import sys
from html import escape as _h
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import blogs as _blogs
from app.components.theme import apply_theme, hairline, kicker, numeric
from app.pages import open_connection


VALID_SORT_KEYS: tuple[str, ...] = ("last_edited", "stale_longest", "length_gap", "pillar")
STATUS_ORDER: tuple[str, ...] = (
    "idea", "outlining", "drafting", "editing",
    "ready", "exported", "published_externally", "archived",
)


# ---------------------------------------------------------------------------
# Session-state bootstrap (Streamlit side-effects discipline — §CLAUDE.md).
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults = {
        "blogs_filter": list(STATUS_ORDER[:5]),  # idea..ready by default
        "blogs_sort": "last_edited",
        "blogs_create_error": None,
        "blogs_navigate_to_editor": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Callbacks (every mutation lives here — never in render flow).
# ---------------------------------------------------------------------------
def _create_blog_cb() -> None:
    title = st.session_state.get("blogs_new_title", "").strip()
    if not title:
        st.session_state["blogs_create_error"] = "title is required."
        return
    pillar = st.session_state.get("blogs_new_pillar", "").strip() or None
    audience = st.session_state.get("blogs_new_audience", "").strip() or None
    tlw_raw = st.session_state.get("blogs_new_target_length", 0) or 0
    target_length = int(tlw_raw) if tlw_raw > 0 else None
    try:
        with open_connection() as conn:
            blog = _blogs.create_blog(
                conn,
                title=title,
                pillar=pillar,
                audience=audience,
                target_length_words=target_length,
            )
        st.session_state["blogs_create_error"] = None
        st.session_state["blogs_navigate_to_editor"] = blog.id
        # Reset form.
        for k in ("blogs_new_title", "blogs_new_pillar",
                  "blogs_new_audience", "blogs_new_target_length"):
            if k in st.session_state:
                del st.session_state[k]
    except _blogs.InvalidBlogFieldError as exc:
        st.session_state["blogs_create_error"] = str(exc)


def _set_filter_cb(*, status: str) -> None:
    current = list(st.session_state.get("blogs_filter", []))
    if status in current:
        current.remove(status)
    else:
        current.append(status)
    st.session_state["blogs_filter"] = current


def _clear_filter_cb() -> None:
    st.session_state["blogs_filter"] = list(STATUS_ORDER)


def _set_sort_cb() -> None:
    """Bound to the sort selectbox via on_change — value mirrors `blogs_sort_sel`."""
    st.session_state["blogs_sort"] = st.session_state.get(
        "blogs_sort_sel", "last_edited"
    )


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


def _stale_threshold(conn) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'blog_stale_status_warning_days'"
    ).fetchone()
    try:
        import json as _json
        return int(_json.loads(row[0])) if row else 21
    except (TypeError, ValueError):
        return 21


def _apply_sort(rows: list[dict], sort_key: str) -> list[dict]:
    if sort_key == "stale_longest":
        return sorted(
            rows,
            key=lambda r: (
                -(r.get("days_in_current_status") or -1),
                r.get("last_edited_at_utc") or "",
            ),
        )
    if sort_key == "length_gap":
        return sorted(
            rows,
            key=lambda r: (
                -abs(r.get("length_gap_words") or 0),
                r.get("last_edited_at_utc") or "",
            ),
        )
    if sort_key == "pillar":
        return sorted(
            rows,
            key=lambda r: (
                r.get("pillar") or "~",  # NULLs last
                r.get("last_edited_at_utc") or "",
            ),
        )
    # last_edited (default)
    return sorted(
        rows,
        key=lambda r: r.get("last_edited_at_utc") or "",
        reverse=True,
    )


def _navigate_to_editor(blog_id: int) -> None:
    """Set the editor target and switch pages."""
    st.session_state["editor_blog_id"] = blog_id
    try:
        st.switch_page("pages/18_Blog_Editor.py")
    except (AttributeError, st.errors.StreamlitAPIException):  # type: ignore[attr-defined]
        # Older Streamlit / test contexts: leave the marker so the
        # next-rerun of the editor page picks it up.
        pass


def _open_blog_cb(*, blog_id: int) -> None:
    _navigate_to_editor(blog_id)


# ---------------------------------------------------------------------------
# Main render.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_session_state()
    st.title("Blogs")
    st.markdown(
        "Long-form authoring surface. Same identity stack as X "
        "drafting — niche, voice profile, voice samples, lore "
        "(§28.31). The app NEVER publishes externally; exports "
        "land as files on disk."
    )

    # Auto-navigate after create.
    pending_nav = st.session_state.get("blogs_navigate_to_editor")
    if pending_nav is not None:
        st.session_state["blogs_navigate_to_editor"] = None
        _navigate_to_editor(int(pending_nav))

    with open_connection() as conn:
        all_rows = _blogs.list_blogs(conn)
        stale_threshold = _stale_threshold(conn)

    # ---- Status counters strip ----
    counters: dict[str, int] = {s: 0 for s in STATUS_ORDER}
    for r in all_rows:
        if r["status"] in counters:
            counters[r["status"]] += 1
    cols = st.columns(8)
    for i, s in enumerate(STATUS_ORDER):
        cols[i].metric(s.replace("_", " "), counters[s])

    hairline()

    # ---- Filter + sort row ----
    filter_col, sort_col, _ = st.columns([3, 2, 1])
    with filter_col:
        kicker("filter")
        chip_cols = st.columns(len(STATUS_ORDER) + 1)
        active_filter = set(st.session_state.get("blogs_filter", []))
        for i, s in enumerate(STATUS_ORDER):
            label = s.replace("_", " ")
            if s in active_filter:
                label = f"● {label}"
            chip_cols[i].button(
                label,
                key=f"filter_chip_{s}",
                on_click=_set_filter_cb,
                kwargs={"status": s},
            )
        chip_cols[-1].button(
            "all",
            key="filter_all",
            on_click=_clear_filter_cb,
        )
    with sort_col:
        kicker("sort")
        st.selectbox(
            "sort by",
            options=VALID_SORT_KEYS,
            key="blogs_sort_sel",
            index=VALID_SORT_KEYS.index(st.session_state["blogs_sort"]),
            on_change=_set_sort_cb,
            label_visibility="collapsed",
        )

    hairline()

    # ---- "+ new blog" inline form ----
    with st.expander("+ new blog", expanded=not all_rows):
        if st.session_state.get("blogs_create_error"):
            st.error(st.session_state["blogs_create_error"])
        with st.form("blogs_new_form"):
            st.text_input("title (required)", key="blogs_new_title")
            cols2 = st.columns(3)
            with cols2[0]:
                st.text_input(
                    "pillar (optional)",
                    key="blogs_new_pillar",
                    placeholder="stir / self / build",
                )
            with cols2[1]:
                st.text_input(
                    "audience (optional)",
                    key="blogs_new_audience",
                    placeholder="icp / builder / general",
                )
            with cols2[2]:
                st.number_input(
                    "target length (words, optional)",
                    key="blogs_new_target_length",
                    min_value=0,
                    step=100,
                    format="%d",
                )
            st.form_submit_button(
                "create blog",
                on_click=_create_blog_cb,
            )

    hairline()

    # ---- Filtered + sorted list ----
    statuses_filter = list(st.session_state["blogs_filter"])
    if statuses_filter:
        rows = [r for r in all_rows if r["status"] in statuses_filter]
    else:
        rows = list(all_rows)
    rows = _apply_sort(rows, st.session_state["blogs_sort"])

    if not rows:
        st.markdown(
            "<div class='dim'>no blogs match the current filter.</div>",
            unsafe_allow_html=True,
        )
        return

    kicker(f"showing {len(rows)} of {len(all_rows)}")
    for r in rows:
        is_stale = (r.get("days_in_current_status") or 0) > stale_threshold
        keyline = "#c98b16" if is_stale else "#2a2f37"
        chips = [_status_chip(r["status"])]
        if r.get("latest_confidence_label"):
            chips.append(_confidence_chip(r["latest_confidence_label"]))
        chip_html = " ".join(chips)

        lane_bits: list[str] = []
        if r.get("pillar"):
            lane_bits.append(r["pillar"])
        if r.get("audience"):
            lane_bits.append(r["audience"])
        lane = _h(" × ".join(lane_bits) or "(unclassified)")

        actual = int(r.get("actual_length_words") or 0)
        target = r.get("target_length_words")
        if target is not None:
            length_label = f"{actual} / {int(target)} words"
            gap = int(r.get("length_gap_words") or 0)
            gap_label = (f"+{gap}" if gap > 0 else str(gap)) if gap else "±0"
        else:
            length_label = f"{actual} words"
            gap_label = ""

        author = _h(str(r.get("last_edited_by") or "—"))
        last_edited = _h(str(r.get("last_edited_at_utc") or "never"))

        with st.container():
            # P6R-2: escape every user/agent-controlled field before
            # interpolating into the unsafe_allow_html=True markdown.
            # title is free-text on create AND agent-generated via
            # repurpose_x_to_blog_idea — never trust either source.
            st.markdown(
                f"<div style='border-left: 3px solid {keyline}; "
                "padding: 0.4rem 0.8rem; margin-bottom: 0.5rem; "
                "background: #161a20; border-radius: 0 0.35rem 0.35rem 0;'>"
                f"<div style='font-family:\"Fraunces\",serif;font-size:1.1rem;"
                f"color:#e6e1d8;font-weight:500;'>{_h(r['title'])}</div>"
                f"<div style='color:#a8a39a;font-size:0.85rem;margin:0.25rem 0;'>"
                f"{lane} · {chip_html} · "
                f"<span class='numeric'>{numeric(length_label)} {numeric(gap_label)}</span> · "
                f"v.{r.get('current_version_number') or 0} ●{author} · {last_edited}"
                "</div></div>",
                unsafe_allow_html=True,
            )
            action_cols = st.columns([1, 1, 6])
            action_cols[0].button(
                "open",
                key=f"open_blog_{r['blog_id']}",
                on_click=_open_blog_cb,
                kwargs={"blog_id": int(r["blog_id"])},
            )
            if r["status"] in ("ready", "exported", "published_externally"):
                action_cols[1].button(
                    "export",
                    key=f"export_quick_{r['blog_id']}",
                    on_click=_open_blog_cb,
                    kwargs={"blog_id": int(r["blog_id"])},
                )


main()
