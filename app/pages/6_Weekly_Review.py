"""Weekly review — spec.md §14.6 + §15.5 (+ Monthly tab, Phase 5.11 §28.27).

Phase 2 wired the form; Phase 3 added the auto-filled quantitative
summary above it, the counterfactual-gated export button, and the
history list below. Phase 5.11 adds a Weekly / Monthly cadence toggle
at the top; switching toggles the underlying table
(weekly_reviews / monthly_reviews) while sharing the page shell.
Both cadences share the same export-blocker rules (counterfactual
required, speculation blocks export).
"""

from __future__ import annotations

import sys
from datetime import date as _date_t
from datetime import timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import monthly_review as _monthly_review
from app.components.badges.confidence_label import ui_label_for_db_label
from app.components.theme import apply_theme, callout, hairline, kicker
from app.forms import weekly_review
from app.pages import open_connection
from app.read_models.weekly_review import build_weekly_review_read_model


def _previous_monday(d: _date_t) -> _date_t:
    return d - timedelta(days=d.weekday())


def _summary_for_week(conn, week_start: _date_t, week_end: _date_t) -> dict:
    """Compute the auto-fillable numbers for the week per §14.6.

    Both followers_start and followers_end use a tolerant lookup: start
    is the first snapshot at-or-after Monday; end is the last snapshot
    at-or-before Sunday. This handles the common case of a missed Monday
    snapshot without silently blanking the "Δ followers" card.
    """
    out: dict = {}
    row = conn.execute(
        """
        SELECT
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date >= ? AND snapshot_date <= ?
              ORDER BY snapshot_date ASC LIMIT 1) AS followers_start,
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date <= ? AND snapshot_date >= ?
              ORDER BY snapshot_date DESC LIMIT 1) AS followers_end,
            (SELECT snapshot_date FROM v_account_daily
              WHERE snapshot_date >= ? AND snapshot_date <= ?
              ORDER BY snapshot_date ASC LIMIT 1) AS followers_start_date,
            (SELECT snapshot_date FROM v_account_daily
              WHERE snapshot_date <= ? AND snapshot_date >= ?
              ORDER BY snapshot_date DESC LIMIT 1) AS followers_end_date
        """,
        (
            week_start.isoformat(), week_end.isoformat(),
            week_end.isoformat(),   week_start.isoformat(),
            week_start.isoformat(), week_end.isoformat(),
            week_end.isoformat(),   week_start.isoformat(),
        ),
    ).fetchone()
    out["followers_start"] = row["followers_start"]
    out["followers_end"] = row["followers_end"]
    out["followers_start_date"] = row["followers_start_date"]
    out["followers_end_date"] = row["followers_end_date"]
    out["follower_delta"] = (
        (out["followers_end"] or 0) - (out["followers_start"] or 0)
        if out["followers_start"] is not None and out["followers_end"] is not None
        else None
    )

    reps = conn.execute(
        """
        SELECT
            COALESCE(SUM(posts_shipped), 0)            AS posts_shipped,
            COALESCE(SUM(replies_shipped), 0)          AS replies_shipped,
            COALESCE(SUM(reply_sessions_completed), 0) AS reply_sessions_completed,
            COALESCE(SUM(minimum_reps_completed), 0)   AS daily_reps_days_completed
        FROM v_daily_reps
        WHERE activity_date BETWEEN ? AND ?
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchone()
    out["posts_shipped"] = int(reps["posts_shipped"])
    out["replies_shipped"] = int(reps["replies_shipped"])
    out["reply_sessions_completed"] = int(reps["reply_sessions_completed"])
    out["daily_reps_days_completed"] = int(reps["daily_reps_days_completed"])

    funnel = conn.execute(
        """
        SELECT
            COALESCE(SUM(downloads), 0)             AS downloads,
            COALESCE(SUM(qualified_icp_testers), 0) AS qualified_icp_testers
        FROM v_funnel_daily
        WHERE event_date BETWEEN ? AND ?
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchone()
    out["downloads"] = int(funnel["downloads"])
    out["qualified_icp_testers"] = int(funnel["qualified_icp_testers"])

    # Strongest pillar candidate — only suggested if there's at least one
    # tentative+ lane; otherwise we explicitly say "no candidate".
    lanes = conn.execute(
        """
        SELECT pillar, median_impressions, confidence_label
        FROM v_lane_performance
        ORDER BY median_impressions DESC NULLS LAST
        """
    ).fetchall()
    eligible = [
        r for r in lanes
        if ui_label_for_db_label(r["confidence_label"]) in {"tentative", "confident"}
    ]
    out["strongest_pillar_candidate"] = (
        f"`{eligible[0]['pillar']}` ({eligible[0]['confidence_label']})"
        if eligible
        else None
    )
    return out


def _existing_review(conn, week_start: _date_t):
    return conn.execute(
        "SELECT * FROM weekly_reviews WHERE week_start_date = ?",
        (week_start.isoformat(),),
    ).fetchone()


def _all_reviews(conn):
    return conn.execute(
        """
        SELECT
            id, week_start_date, week_end_date, follower_delta,
            posts_shipped, replies_shipped, downloads,
            counterfactual_note, lesson
        FROM weekly_reviews
        ORDER BY week_start_date DESC
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()
weekly_read_model = build_weekly_review_read_model(conn)

today = _date_t.today()
week_start = _previous_monday(today)
week_end = week_start + timedelta(days=6)
counterfactual_required = bool(weekly_read_model["counterfactual_required"])

# Phase 5.11 §28.27: cadence toggle at the top. Persisted in session
# state so navigating away and back keeps the chosen cadence. Defaults
# to weekly — Daniel's primary cadence per §14.6.
if "review_cadence" not in st.session_state:
    st.session_state["review_cadence"] = "Weekly"
cadence = st.radio(
    "Cadence",
    options=("Weekly", "Monthly"),
    horizontal=True,
    key="review_cadence",
)

if cadence == "Monthly":
    iso_month = _monthly_review.iso_month_of(today)
    kicker(f"MONTH OF {iso_month}")
    st.title("Monthly review")
    st.caption(
        "Cadence companion to weekly (§28.27). Same export-blocker rules: "
        "counterfactual_note required, speculation blocks export. Adds the "
        "content-type axis + a campaigns retrospective from "
        "campaigns_completed_json."
    )

    auto = _monthly_review.compute_auto_filled_fields(conn, iso_month)

    st.markdown("## This month — at a glance")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Followers Δ",
        f"{auto.follower_delta:+d}" if auto.follower_delta is not None else "—",
    )
    c2.metric("Posts shipped", str(auto.posts_shipped))
    c3.metric("Replies shipped", str(auto.replies_shipped))
    c4.metric("Stir downloads", str(auto.downloads))

    c5, c6, c7 = st.columns(3)
    c5.metric("Reply sessions", str(auto.reply_sessions_completed))
    c6.metric(
        "Rep-complete days",
        f"{auto.daily_reps_days_completed} / 30",
    )
    c7.metric("Qualified ICP testers", str(auto.qualified_icp_testers))

    if auto.strongest_pillar_candidate:
        callout(
            f"<em>Strongest-pillar candidate:</em> "
            f"{auto.strongest_pillar_candidate}"
        )
    if auto.strongest_content_type:
        callout(
            f"<em>Strongest content-type (§28.17):</em> "
            f"{auto.strongest_content_type}"
        )
    if auto.weakest_content_type:
        callout(
            f"<em>Weakest content-type (§28.17):</em> "
            f"{auto.weakest_content_type}"
        )

    import json as _json
    campaigns_completed = _json.loads(auto.campaigns_completed_json or "[]")
    if campaigns_completed:
        kicker("Campaigns completed this month")
        for entry in campaigns_completed:
            line = f"- **{entry['name']}** (id {entry['campaign_id']})"
            if entry.get("lesson"):
                line += f" — _{entry['lesson']}_"
            st.markdown(line)
    else:
        st.markdown(
            "<p class='faint'>No campaigns completed this month.</p>",
            unsafe_allow_html=True,
        )

    hairline()
    st.markdown("## Write the monthly review")
    existing = _monthly_review.get_monthly_review(conn, iso_month=iso_month) or {}

    if "monthly_review_form_init" not in st.session_state:
        st.session_state["monthly_review_form_init"] = {}
    # Initialize widget keys from the existing row exactly once.
    for field in (
        "summary",
        "key_movements",
        "what_got_stuck",
        "stir_validation_summary",
        "lesson",
        "next_month_experiment",
        "counterfactual_note",
        "daniel_notes",
    ):
        widget_key = f"mr_{field}"
        if widget_key not in st.session_state:
            st.session_state[widget_key] = existing.get(field) or ""
    confidence_key = "mr_confidence_label"
    if confidence_key not in st.session_state:
        st.session_state[confidence_key] = (
            existing.get("confidence_label") or "inference"
        )

    def _save_monthly_cb() -> None:
        payload = {
            "summary": st.session_state["mr_summary"],
            "key_movements": st.session_state["mr_key_movements"],
            "what_got_stuck": st.session_state["mr_what_got_stuck"],
            "stir_validation_summary": st.session_state["mr_stir_validation_summary"],
            "lesson": st.session_state["mr_lesson"],
            "next_month_experiment": st.session_state["mr_next_month_experiment"],
            "counterfactual_note": st.session_state["mr_counterfactual_note"],
            "daniel_notes": st.session_state["mr_daniel_notes"],
            "confidence_label": st.session_state["mr_confidence_label"],
            # Auto-filled side-cars Daniel doesn't edit through this form.
            "follower_delta": auto.follower_delta,
            "strongest_content_type": auto.strongest_content_type,
            "weakest_content_type": auto.weakest_content_type,
            "campaigns_completed_json": auto.campaigns_completed_json,
        }
        with open_connection() as save_conn:
            _monthly_review.upsert_monthly_review(
                save_conn, iso_month=iso_month, fields=payload
            )
        st.toast("monthly review saved.")

    with st.form(key="monthly_review_form"):
        st.text_area("summary", key="mr_summary", height=80)
        st.text_area("key_movements", key="mr_key_movements", height=80)
        st.text_area("what got stuck", key="mr_what_got_stuck", height=80)
        st.text_area(
            "stir validation summary", key="mr_stir_validation_summary", height=80
        )
        st.text_area("lesson", key="mr_lesson", height=80)
        st.text_area(
            "next month experiment", key="mr_next_month_experiment", height=80
        )
        st.text_area(
            "counterfactual note (REQUIRED for export)",
            key="mr_counterfactual_note",
            height=100,
        )
        st.selectbox(
            "confidence label",
            options=("fact", "inference", "speculation", "mixed"),
            key="mr_confidence_label",
        )
        st.text_area("daniel's notes", key="mr_daniel_notes", height=60)
        st.form_submit_button("save monthly review", on_click=_save_monthly_cb)

    hairline()
    st.markdown("## Export")
    refreshed = _monthly_review.get_monthly_review(conn, iso_month=iso_month)
    blocked = _monthly_review.export_blocked_reason(refreshed)
    if blocked:
        st.button(
            "Export monthly report (Markdown)",
            disabled=True,
            help=blocked,
            width="content",
        )
        st.markdown(
            f"<p class='faint'>{blocked}</p>", unsafe_allow_html=True
        )
    else:
        st.button(
            "Export monthly report (Markdown)",
            help="Export logic lands when scripts/export_monthly_review.py ships.",
            width="content",
        )
    # Monthly cadence path stops here — the rest of the page is weekly-specific.
    st.stop()

kicker(f"WEEK OF {week_start.strftime('%b %-d').upper()} – {week_end.strftime('%b %-d, %Y').upper()}")
st.title("Weekly review")
st.caption(
    "Turn raw activity into learning. Auto-filled numbers sit above the "
    "form as prompts — Daniel writes the interpretation. The counterfactual "
    "note (§14.6) is the keystone: without it, export is disabled."
)

summary = _summary_for_week(conn, week_start, week_end)

# Auto-fill summary cards.
st.markdown("## This week — at a glance")
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Followers Δ",
    f"{summary['follower_delta']:+d}" if summary["follower_delta"] is not None else "—",
    delta=(
        f"{summary['followers_start']:,} ({summary['followers_start_date']}) "
        f"→ {summary['followers_end']:,} ({summary['followers_end_date']})"
        if summary["followers_start"] is not None and summary["followers_end"] is not None
        else "no snapshot range"
    ),
)
c2.metric("Posts shipped", str(summary["posts_shipped"]))
c3.metric("Replies shipped", str(summary["replies_shipped"]))
c4.metric("Stir downloads", str(summary["downloads"]))

c5, c6, c7 = st.columns(3)
c5.metric("Reply sessions", str(summary["reply_sessions_completed"]))
c6.metric("Rep-complete days", f"{summary['daily_reps_days_completed']} / 7")
c7.metric("Qualified ICP testers", str(summary["qualified_icp_testers"]))

if summary["strongest_pillar_candidate"]:
    callout(
        f"<em>Strongest-pillar candidate (provisional):</em> "
        f"{summary['strongest_pillar_candidate']}. "
        "This is a prompt, not a conclusion — Daniel writes the interpretation."
    )
else:
    callout(
        "<em>No strongest-pillar candidate this week.</em> No lane has reached "
        "<strong>tentative</strong> confidence yet. Read the Content "
        "Performance scatter for raw evidence."
    )

hairline()

# The form (Phase 2 fragment — render in-place).
st.markdown("## Write the review")
weekly_review.render(conn, key_prefix="weekly_review")

hairline()

# Export button — disabled until counterfactual is filled.
existing = _existing_review(conn, week_start)
has_counterfactual = bool(
    existing
    and existing["counterfactual_note"]
    and str(existing["counterfactual_note"]).strip()
)
st.markdown("## Export")
disabled_reason = (
    "Counterfactual note required (§14.6). Fill the form above and save first."
    if counterfactual_required and not has_counterfactual
    else "Export logic lands in Phase 5 — this button currently just verifies the gating works."
)
st.button(
    "Export weekly report (Markdown)",
    disabled=(counterfactual_required and not has_counterfactual),
    help=disabled_reason,
    width="content",
)
if counterfactual_required and not has_counterfactual:
    st.markdown(
        f"<p class='faint'>{disabled_reason}</p>",
        unsafe_allow_html=True,
    )

hairline()

# History.
st.markdown("## Past reviews")
history = _all_reviews(conn)
if not history:
    st.markdown(
        "<p class='faint'>No saved reviews yet.</p>",
        unsafe_allow_html=True,
    )
else:
    for row in history:
        with st.expander(
            f"Week of {row['week_start_date']} — Δ followers "
            f"{(row['follower_delta'] or 0):+d}, "
            f"posts {row['posts_shipped']}, replies {row['replies_shipped']}, "
            f"downloads {row['downloads']}",
            expanded=False,
        ):
            st.markdown(
                f"<p class='faint'>Period: {row['week_start_date']} → {row['week_end_date']}</p>",
                unsafe_allow_html=True,
            )
            if row["lesson"]:
                st.markdown(f"**Lesson:** {row['lesson']}")
            if row["counterfactual_note"]:
                callout(
                    f"<em>Counterfactual:</em> {row['counterfactual_note']}"
                )
            else:
                st.markdown(
                    "<p class='faint'>No counterfactual recorded — this review predates the §14.6 enforcement.</p>",
                    unsafe_allow_html=True,
                )

# Agent integration (§14.6 + §28.7).
hairline()
st.markdown("### Ask the agent")
ag_a, ag_b, ag_c = st.columns(3)
if ag_a.button("draft counterfactual →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "weekly_review_counterfactual"
    st.switch_page("pages/9_Agent_Chat.py")
if ag_b.button("draft interpretation →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "weekly_review_interpretation"
    st.switch_page("pages/9_Agent_Chat.py")
if ag_c.button("suggest next experiment →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "weekly_review_next_experiment"
    st.switch_page("pages/9_Agent_Chat.py")
