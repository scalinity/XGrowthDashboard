"""Content Calendar — spec.md §14.11 (Phase 5.11).

Visual upcoming + recent posts in a calendar grid. Distinct cognitive
mode from §14.1 Today / §14.2 Next Rep: this view answers "what does
my distribution surface look like over the next 2 weeks and the past 2
weeks?" — planning vs. doing.

Reads four provenances via :func:`app.agent.calendar.get_calendar_window`:
POSTED, DRAFTED-FOR-FUTURE, AGENT-DRAFTED, PLANNED. The "+ schedule
slot" inline form below the grid routes to either
``campaign_items`` (campaign-scoped path) or a standalone draft
``posts`` row (ad-hoc path) — Daniel picks at submission. Both paths
write-through to ``audit_logs`` via the underlying modules.

Anti-feature note: this calendar shows schedules; it does NOT publish.
§19 item 11's scheduled-drafts flow still requires fresh confirmation
at publish time (§28.10 contract).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import audit_log as _audit_log
from app.agent import calendar as _calendar
from app.agent import campaigns as _campaigns
from app.components.theme import apply_theme, hairline, kicker
from app.db import transaction
from app.forms import get_setting
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap (CLAUDE.md side-effects rule).
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    with open_connection() as conn:
        default_view = get_setting(conn, "calendar_default_view", "week")
    defaults = {
        "calendar_view": default_view if default_view in {"week", "two_weeks", "month"} else "week",
        "calendar_anchor_date": date.today(),
        "calendar_filter_pillar": "all",
        "calendar_filter_content_type": "all",
        "calendar_filter_campaign_id": "all",
        "calendar_slot_form_kind": "ad-hoc",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------
def _window_for(view: str, anchor: date) -> tuple[date, date]:
    """Return [start_date, end_date] inclusive for the chosen view."""
    monday = anchor - timedelta(days=anchor.weekday())
    if view == "week":
        return monday, monday + timedelta(days=6)
    if view == "two_weeks":
        return monday, monday + timedelta(days=13)
    if view == "month":
        # Month rooted at anchor's first-of-month → last-of-month.
        first = anchor.replace(day=1)
        if anchor.month == 12:
            next_first = anchor.replace(year=anchor.year + 1, month=1, day=1)
        else:
            next_first = anchor.replace(month=anchor.month + 1, day=1)
        last = next_first - timedelta(days=1)
        return first, last
    raise ValueError(f"unknown view: {view!r}")


def _provenance_chip(p: _calendar.Provenance) -> str:
    return {
        "posted": "POSTED",
        "drafted_for_future": "DRAFTED",
        "agent_drafted": "DRAFTED",
        "planned": "PLANNED",
    }[p]


# ---------------------------------------------------------------------------
# Callbacks.
# ---------------------------------------------------------------------------
def _shift_anchor_cb(*, days: int) -> None:
    st.session_state["calendar_anchor_date"] = (
        st.session_state["calendar_anchor_date"] + timedelta(days=days)
    )


def _reset_anchor_cb() -> None:
    st.session_state["calendar_anchor_date"] = date.today()


def _schedule_slot_cb() -> None:
    kind = st.session_state["calendar_slot_form_kind"]
    planned_date = st.session_state.get("schedule_slot_date", date.today())
    text = st.session_state.get("schedule_slot_text", "").strip()
    if not text:
        st.toast("planned text is required.", icon="⚠")
        return
    with open_connection() as conn:
        if kind == "campaign-scoped":
            cid = st.session_state.get("schedule_slot_campaign_id")
            if not cid:
                st.toast("pick a campaign first.", icon="⚠")
                return
            try:
                _campaigns.add_item(
                    conn,
                    campaign_id=int(cid),
                    item_type="post",
                    planned_for_date=planned_date,
                    planned_text=text,
                )
            except _campaigns.CampaignError as exc:
                st.toast(f"schedule refused: {exc}", icon="⚠")
                return
        else:
            # Ad-hoc path: insert a posts row with draft confirmation
            # status and created_in_app_at = noon-of-day so the AM/PM
            # bucketing reads as PM by default (Daniel can edit later).
            #
            # P511R-3: §28.30 contract requires every state-changing
            # path to write through audit_logs. The campaign-scoped
            # branch above goes via _campaigns.add_item which audit-
            # logs internally; this ad-hoc branch was bypassing the
            # audit floor. INSERT + audit are now wrapped in a single
            # transaction so they commit atomically.
            with transaction(conn):
                cur = conn.execute(
                    """
                    INSERT INTO posts
                      (created_date, text, type, posted_via,
                       manual_confirmation_status, created_in_app_at)
                    VALUES (?, ?, 'standalone', 'manual', 'draft', ?)
                    RETURNING id
                    """,
                    (
                        planned_date.isoformat(),
                        text,
                        f"{planned_date.isoformat()}T12:00:00",
                    ),
                )
                new_post_id = int(cur.fetchone()[0])
                _audit_log.log(
                    conn,
                    event_category="data",
                    event_type="post_drafted_via_calendar_slot",
                    target_type="post",
                    target_id=new_post_id,
                    details={
                        "planned_date": planned_date.isoformat(),
                        "via": "calendar_ad_hoc",
                    },
                )
    if "schedule_slot_text" in st.session_state:
        st.session_state["schedule_slot_text"] = ""
    st.toast("slot scheduled.")


# ---------------------------------------------------------------------------
# Render.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_session_state()
    st.title("Content Calendar")
    st.markdown(
        "Visual planning grid — POSTED + DRAFTED + PLANNED across "
        "the chosen window. See spec §14.11 / §28.28."
    )

    # ----- Toolbar -----
    cols = st.columns([1, 1, 1, 2, 1])
    with cols[0]:
        st.button("← prev", on_click=_shift_anchor_cb, kwargs={"days": -7})
    with cols[1]:
        st.button("today", on_click=_reset_anchor_cb)
    with cols[2]:
        st.button("next →", on_click=_shift_anchor_cb, kwargs={"days": 7})
    with cols[3]:
        st.selectbox(
            "view",
            options=("week", "two_weeks", "month"),
            key="calendar_view",
        )
    with cols[4]:
        st.write("")  # spacer

    view = st.session_state["calendar_view"]
    anchor = st.session_state["calendar_anchor_date"]
    window_start, window_end = _window_for(view, anchor)
    st.markdown(
        f"<div class='kicker'>Window: "
        f"{window_start.isoformat()} — {window_end.isoformat()}</div>",
        unsafe_allow_html=True,
    )

    # ----- Filters -----
    with open_connection() as conn:
        all_campaigns = _campaigns.list_campaigns(conn)
    pillar_options = ["all", "build", "stir", "self"]
    content_type_options = ["all", "value", "growth", "personality", "proof"]
    campaign_options: list[tuple[str, str]] = [("all", "all campaigns")] + [
        (str(c.id), c.name) for c in all_campaigns
    ]

    fcols = st.columns(3)
    with fcols[0]:
        st.selectbox(
            "pillar",
            options=pillar_options,
            key="calendar_filter_pillar",
        )
    with fcols[1]:
        st.selectbox(
            "content type",
            options=content_type_options,
            key="calendar_filter_content_type",
        )
    with fcols[2]:
        st.selectbox(
            "campaign",
            options=[k for k, _ in campaign_options],
            format_func=lambda v: dict(campaign_options).get(v, v),
            key="calendar_filter_campaign_id",
        )

    pillar_filter = (
        None
        if st.session_state["calendar_filter_pillar"] == "all"
        else st.session_state["calendar_filter_pillar"]
    )
    content_type_filter = (
        None
        if st.session_state["calendar_filter_content_type"] == "all"
        else st.session_state["calendar_filter_content_type"]
    )
    campaign_filter = (
        None
        if st.session_state["calendar_filter_campaign_id"] == "all"
        else int(st.session_state["calendar_filter_campaign_id"])
    )

    # ----- Grid -----
    with open_connection() as conn:
        cells = _calendar.get_calendar_window(
            conn,
            start_date=window_start,
            end_date=window_end,
            pillar=pillar_filter,
            content_type=content_type_filter,
            campaign_id=campaign_filter,
        )
        active_campaigns = _calendar.get_active_campaigns_in_window(
            conn, start_date=window_start, end_date=window_end
        )

    # Bucket cells by (date, slot).
    grid: dict[tuple[str, str], list[_calendar.CalendarCell]] = {}
    for cell in cells:
        grid.setdefault((cell.date, cell.slot), []).append(cell)

    days = [
        window_start + timedelta(days=i)
        for i in range((window_end - window_start).days + 1)
    ]
    # Render one row per day for narrow Streamlit columns; AM and PM
    # cells side-by-side. (A true wide-grid layout would need a custom
    # CSS table; this side-by-side keeps the cells readable on the
    # default Streamlit page width.)
    for day in days:
        st.markdown(
            f"<div class='kicker'>{day.strftime('%a %b %d')}</div>",
            unsafe_allow_html=True,
        )
        c_am, c_pm = st.columns(2)
        with c_am:
            st.markdown("**AM**")
            for cell in grid.get((day.isoformat(), "am"), []):
                st.markdown(
                    f"- `{_provenance_chip(cell.provenance)}` "
                    f"{cell.pillar or '—'} · {cell.content_type or '—'} · "
                    f"{cell.title}"
                )
            if not grid.get((day.isoformat(), "am")):
                st.markdown("<span class='dim'>—</span>", unsafe_allow_html=True)
        with c_pm:
            st.markdown("**PM**")
            for cell in grid.get((day.isoformat(), "pm"), []):
                st.markdown(
                    f"- `{_provenance_chip(cell.provenance)}` "
                    f"{cell.pillar or '—'} · {cell.content_type or '—'} · "
                    f"{cell.title}"
                )
            if not grid.get((day.isoformat(), "pm")):
                st.markdown("<span class='dim'>—</span>", unsafe_allow_html=True)

    hairline()

    # ----- Active campaigns strip -----
    kicker("Active campaigns running through this window")
    if not active_campaigns:
        st.markdown(
            "<div class='dim'>no active campaigns overlap this window.</div>",
            unsafe_allow_html=True,
        )
    for ac in active_campaigns:
        st.markdown(
            f"- **{ac['name']}** ({ac['start_date']} — {ac['end_date']}) · "
            f"shipped {ac['items_shipped']} · planned {ac['items_planned']}"
        )

    hairline()

    # ----- + schedule slot inline form -----
    st.subheader("+ schedule slot")
    st.radio(
        "kind",
        options=("ad-hoc", "campaign-scoped"),
        horizontal=True,
        key="calendar_slot_form_kind",
    )
    with st.form("schedule_slot_form"):
        st.date_input(
            "planned date",
            value=date.today(),
            key="schedule_slot_date",
        )
        st.text_area(
            "planned text",
            key="schedule_slot_text",
            height=80,
            placeholder="What goes in this slot? (free-text or eventual draft)",
        )
        if st.session_state["calendar_slot_form_kind"] == "campaign-scoped":
            st.selectbox(
                "campaign",
                options=[c.id for c in all_campaigns],
                format_func=lambda i: next(
                    (c.name for c in all_campaigns if c.id == i), str(i)
                ),
                key="schedule_slot_campaign_id",
            )
        st.form_submit_button("schedule slot", on_click=_schedule_slot_cb)

    st.markdown(
        "<div class='dim' style='margin-top:0.6rem;font-size:0.86rem;'>"
        "This calendar shows schedules; it does not publish. Section 28.10's "
        "two-step confirmation still gates the publish moment.</div>",
        unsafe_allow_html=True,
    )


main()
