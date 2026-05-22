"""Weekly review — spec.md §14.6 + §15.5.

Phase 2 wired the form; Phase 3 adds the auto-filled quantitative summary
above it, the counterfactual-gated export button, and the history list
below. The "Export weekly report" button is intentionally **disabled**
until the most-recent matching `weekly_reviews` row carries a non-empty
``counterfactual_note`` — actual export logic lands in Phase 5.
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

from app.components.badges.confidence_label import ui_label_for_db_label
from app.components.theme import apply_theme, callout, hairline, kicker
from app.forms import get_setting, weekly_review
from app.pages import open_connection


def _previous_monday(d: _date_t) -> _date_t:
    return d - timedelta(days=d.weekday())


def _summary_for_week(conn, week_start: _date_t, week_end: _date_t) -> dict:
    """Compute the auto-fillable numbers for the week per §14.6."""
    out: dict = {}
    row = conn.execute(
        """
        SELECT
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date = ? LIMIT 1) AS followers_start,
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1) AS followers_end
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchone()
    out["followers_start"] = row["followers_start"]
    out["followers_end"] = row["followers_end"]
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

today = _date_t.today()
week_start = _previous_monday(today)
week_end = week_start + timedelta(days=6)
counterfactual_required = bool(get_setting(conn, "counterfactual_required", True))

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
        f"start {summary['followers_start']:,} → end {summary['followers_end']:,}"
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
