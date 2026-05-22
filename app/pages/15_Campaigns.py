"""Campaigns — spec.md §14.12 (Phase 5.11).

Multi-week themed pushes. Four status sections (Active, Planning,
Completed, Abandoned); active campaigns expanded by default, others
collapsed. Each campaign carries: hypothesis, success criteria (with
actuals if completed), item list (with status chips), per-campaign
progress bar reading ``v_campaign_progress``.

Load-bearing rule (§28.26): the "+ new campaign" form will refuse to
save without at least one distribution metric AND at least one
validation metric — the schema validation lives in
``app/agent/campaigns.py::create_campaign``. The form here surfaces the
error inline so Daniel knows why a single-stream campaign was rejected.

Side-effects discipline (CLAUDE.md): mutate ``st.session_state`` only
via explicit callbacks (form submit, button on_click). Render flow
stays a pure derivation from DB + session state.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import campaigns as _campaigns
from app.components.theme import apply_theme, hairline, kicker
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state bootstrap (CLAUDE.md side-effects rule).
# ---------------------------------------------------------------------------
def _init_session_state() -> None:
    defaults = {
        "campaigns_new_form_error": None,
        "campaigns_complete_target": None,
        "campaigns_abandon_target": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ---------------------------------------------------------------------------
# Pure helpers (no Streamlit / session-state mutation).
# ---------------------------------------------------------------------------
def _format_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(value * 100))}%"


def _success_criteria_lines(camp: _campaigns.Campaign) -> list[str]:
    lines: list[str] = []
    for stream in ("distribution", "validation"):
        for entry in camp.success_criteria.get(stream, []):
            actual = entry.get("actual")
            target = entry.get("target")
            metric = entry.get("metric")
            actual_str = (
                str(actual)
                if actual is not None and str(actual).strip()
                else "—"
            )
            lines.append(
                f"[{stream}] {metric}: target {target} · actual {actual_str}"
            )
    return lines


# ---------------------------------------------------------------------------
# Callbacks — mutate session_state explicitly.
# ---------------------------------------------------------------------------
def _create_campaign_cb() -> None:
    name = st.session_state.get("new_campaign_name", "").strip()
    theme = st.session_state.get("new_campaign_theme", "").strip()
    hypothesis = st.session_state.get("new_campaign_hypothesis", "").strip()
    start = st.session_state.get(
        "new_campaign_start", date.today()
    )
    end = st.session_state.get(
        "new_campaign_end", date.today() + timedelta(days=14)
    )
    dist_metric = st.session_state.get("new_campaign_dist_metric", "").strip()
    dist_target = st.session_state.get("new_campaign_dist_target", "").strip()
    val_metric = st.session_state.get("new_campaign_val_metric", "").strip()
    val_target = st.session_state.get("new_campaign_val_target", "").strip()

    if not name:
        st.session_state["campaigns_new_form_error"] = "name is required."
        return
    if not dist_metric or not dist_target or not val_metric or not val_target:
        st.session_state["campaigns_new_form_error"] = (
            "dual-stream success criteria required: one distribution metric "
            "AND one validation metric per §28.26."
        )
        return

    success_criteria = {
        "distribution": [{"metric": dist_metric, "target": dist_target}],
        "validation": [{"metric": val_metric, "target": val_target}],
    }
    try:
        with open_connection() as conn:
            _campaigns.create_campaign(
                conn,
                name=name,
                theme=theme or None,
                hypothesis=hypothesis or None,
                start_date=start,
                end_date=end,
                success_criteria=success_criteria,
            )
        st.session_state["campaigns_new_form_error"] = None
        # Reset the form inputs.
        for key in (
            "new_campaign_name",
            "new_campaign_theme",
            "new_campaign_hypothesis",
            "new_campaign_dist_metric",
            "new_campaign_dist_target",
            "new_campaign_val_metric",
            "new_campaign_val_target",
        ):
            if key in st.session_state:
                st.session_state[key] = ""
    except _campaigns.CampaignError as exc:
        st.session_state["campaigns_new_form_error"] = str(exc)


def _activate_campaign_cb(*, campaign_id: int) -> None:
    with open_connection() as conn:
        _campaigns.activate_campaign(conn, campaign_id=campaign_id)


def _add_item_cb(*, campaign_id: int) -> None:
    item_type = st.session_state.get(
        f"new_item_type_{campaign_id}", "post"
    )
    planned_text = st.session_state.get(
        f"new_item_planned_text_{campaign_id}", ""
    ).strip()
    planned_date = st.session_state.get(
        f"new_item_planned_date_{campaign_id}", None
    )
    with open_connection() as conn:
        _campaigns.add_item(
            conn,
            campaign_id=campaign_id,
            item_type=item_type,
            planned_for_date=planned_date,
            planned_text=planned_text or None,
        )
    if f"new_item_planned_text_{campaign_id}" in st.session_state:
        st.session_state[f"new_item_planned_text_{campaign_id}"] = ""


def _transition_item_cb(*, item_id: int, new_status: str) -> None:
    with open_connection() as conn:
        try:
            _campaigns.transition_item_status(
                conn, item_id=item_id, new_status=new_status
            )
        except _campaigns.InvalidStatusTransitionError as exc:
            st.toast(f"transition refused: {exc}", icon="⚠")


# ---------------------------------------------------------------------------
# Render — per-campaign card.
# ---------------------------------------------------------------------------
def _render_campaign_card(camp: _campaigns.Campaign) -> None:
    with open_connection() as conn:
        items = _campaigns.list_items(conn, campaign_id=camp.id)
        progress_row = conn.execute(
            """
            SELECT items_total, items_shipped, percent_shipped,
                   days_until_end
            FROM v_campaign_progress WHERE campaign_id = ?
            """,
            (camp.id,),
        ).fetchone()

    pct = (
        progress_row["percent_shipped"]
        if progress_row and progress_row["percent_shipped"] is not None
        else None
    )
    items_shipped = int(progress_row["items_shipped"] or 0) if progress_row else 0
    items_total = int(progress_row["items_total"] or 0) if progress_row else 0
    days_until_end = (
        int(progress_row["days_until_end"])
        if progress_row and progress_row["days_until_end"] is not None
        else None
    )

    header_lines = [
        f"**{camp.name}**",
        f"{camp.start_date} — {camp.end_date} · "
        f"shipped {items_shipped}/{items_total} ({_format_percent(pct)})",
    ]
    if camp.status == "active" and days_until_end is not None:
        if days_until_end < 0:
            header_lines.append(
                f"⚠ ended {-days_until_end} day(s) ago — complete now or extend?"
            )
        else:
            header_lines.append(f"{days_until_end} day(s) remaining")

    is_active_status = camp.status == "active"
    with st.expander("\n\n".join(header_lines), expanded=is_active_status):
        if camp.hypothesis:
            st.markdown(f"_Hypothesis:_ {camp.hypothesis}")
        if camp.theme:
            st.markdown(f"_Theme:_ {camp.theme}")
        if camp.pillar or camp.content_type:
            st.markdown(
                f"_Pillar:_ {camp.pillar or '—'} · "
                f"_Content type:_ {camp.content_type or '—'}"
            )

        kicker("Success criteria")
        for line in _success_criteria_lines(camp):
            st.markdown(f"- {line}")

        # Item management.
        kicker("Items")
        if not items:
            st.markdown("_no items planned yet._")
        for it in items:
            it_cols = st.columns([3, 1, 1, 1])
            with it_cols[0]:
                planned_for = it.planned_for_date or "—"
                label = (
                    f"`{it.status}` · {it.item_type} · "
                    f"planned {planned_for}"
                )
                if it.planned_text:
                    label += f"\n\n{it.planned_text[:200]}"
                st.markdown(label)
            valid_transitions = {
                "planned": ("drafted", "shipped", "skipped"),
                "drafted": ("shipped", "skipped"),
                "shipped": (),
                "skipped": (),
            }.get(it.status, ())
            with it_cols[1]:
                if "drafted" in valid_transitions:
                    st.button(
                        "mark drafted",
                        key=f"camp_{camp.id}_item_{it.id}_drafted",
                        on_click=_transition_item_cb,
                        kwargs={"item_id": it.id, "new_status": "drafted"},
                    )
            with it_cols[2]:
                if "shipped" in valid_transitions:
                    st.button(
                        "mark shipped",
                        key=f"camp_{camp.id}_item_{it.id}_shipped",
                        on_click=_transition_item_cb,
                        kwargs={"item_id": it.id, "new_status": "shipped"},
                    )
            with it_cols[3]:
                if "skipped" in valid_transitions:
                    st.button(
                        "skip",
                        key=f"camp_{camp.id}_item_{it.id}_skipped",
                        on_click=_transition_item_cb,
                        kwargs={"item_id": it.id, "new_status": "skipped"},
                    )

        # Add-item form, only when not a terminal-state campaign.
        if camp.status in ("planning", "active"):
            with st.form(key=f"add_item_form_{camp.id}"):
                st.selectbox(
                    "item type",
                    options=sorted(_campaigns.VALID_ITEM_TYPES),
                    key=f"new_item_type_{camp.id}",
                )
                st.date_input(
                    "planned for",
                    key=f"new_item_planned_date_{camp.id}",
                    value=date.today(),
                )
                st.text_area(
                    "planned text (optional)",
                    key=f"new_item_planned_text_{camp.id}",
                    height=80,
                )
                st.form_submit_button(
                    "add item",
                    on_click=_add_item_cb,
                    kwargs={"campaign_id": camp.id},
                )

        # Lifecycle controls.
        if camp.status == "planning":
            st.button(
                "activate campaign",
                key=f"activate_{camp.id}",
                on_click=_activate_campaign_cb,
                kwargs={"campaign_id": camp.id},
            )
        if camp.status == "completed":
            kicker("Retro")
            if camp.lesson:
                st.markdown(f"_Lesson:_ {camp.lesson}")
            if camp.counterfactual_note:
                st.markdown(f"_Counterfactual:_ {camp.counterfactual_note}")
        if camp.status == "abandoned" and camp.abandon_reason:
            st.markdown(f"_Abandon reason:_ {camp.abandon_reason}")


# ---------------------------------------------------------------------------
# Main render.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_session_state()
    st.title("Campaigns")
    st.markdown(
        "Multi-week themed pushes. Hypothesis + dual-stream success criteria "
        "+ items + retro. See spec §14.12 / §28.26."
    )

    with open_connection() as conn:
        all_campaigns = _campaigns.list_campaigns(conn)

    by_status: dict[str, list[_campaigns.Campaign]] = {
        s: [] for s in ("active", "planning", "completed", "abandoned")
    }
    for c in all_campaigns:
        by_status[c.status].append(c)

    # Top summary strip.
    summary = "  ·  ".join(
        f"{s.title()}: {len(by_status[s])}"
        for s in ("active", "planning", "completed", "abandoned")
    )
    st.markdown(f"<div class='kicker'>{summary}</div>", unsafe_allow_html=True)
    hairline()

    # + new campaign form.
    with st.expander("+ new campaign", expanded=not all_campaigns):
        if st.session_state.get("campaigns_new_form_error"):
            st.error(st.session_state["campaigns_new_form_error"])
        with st.form(key="new_campaign_form"):
            st.text_input("name", key="new_campaign_name")
            st.text_area("theme", key="new_campaign_theme", height=70)
            st.text_area(
                "hypothesis", key="new_campaign_hypothesis", height=70
            )
            cols = st.columns(2)
            with cols[0]:
                st.date_input(
                    "start date",
                    key="new_campaign_start",
                    value=date.today(),
                )
            with cols[1]:
                st.date_input(
                    "end date",
                    key="new_campaign_end",
                    value=date.today() + timedelta(days=14),
                )
            st.markdown(
                "**Success criteria — dual-stream required (§28.26).**"
            )
            sc_cols = st.columns(2)
            with sc_cols[0]:
                st.text_input(
                    "distribution metric",
                    key="new_campaign_dist_metric",
                    placeholder="impressions",
                )
                st.text_input(
                    "distribution target",
                    key="new_campaign_dist_target",
                    placeholder="10000",
                )
            with sc_cols[1]:
                st.text_input(
                    "validation metric",
                    key="new_campaign_val_metric",
                    placeholder="downloads",
                )
                st.text_input(
                    "validation target",
                    key="new_campaign_val_target",
                    placeholder="5",
                )
            st.form_submit_button(
                "create campaign", on_click=_create_campaign_cb
            )

    # Status sections.
    section_order = ("active", "planning", "completed", "abandoned")
    for status in section_order:
        rows = by_status[status]
        st.subheader(f"{status.title()} ({len(rows)})")
        if not rows:
            st.markdown(
                f"<div class='dim'>no {status} campaigns.</div>",
                unsafe_allow_html=True,
            )
            continue
        for camp in rows:
            _render_campaign_card(camp)


main()
