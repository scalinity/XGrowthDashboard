"""Content Performance — spec.md §14.4.

The honesty-load-bearing view. Two non-negotiables baked in:

1. No point-estimate ranking below the §14.4 sample-size threshold. Lanes
   with ``confidence_label = 'insufficient sample'`` show "—", not numbers.
   The "best lane" callout only renders when at least 3 lanes are at
   ``tentative`` or higher (UI labels).
2. IQR is shown alongside every median so spread is visible. A lane with
   median 245 and IQR 110-620 is visibly *not* the same as median 245 with
   IQR 230-260.

The scatter plot below the grid surfaces raw evidence for every classified
post in the last 30 days — even when the aggregate sample size is below
threshold the eye can still read patterns.
"""

from __future__ import annotations

import sys
from datetime import date as _date_t
from datetime import timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import plotly.graph_objects as go
import streamlit as st

from app.components.charts.lane_grid import (
    LaneRow,
    confidence_color_for_ui_label,
    count_rankable_lanes,
    lane_performance_grid,
    lane_rows_from_sql,
)
from app.components.theme import (
    LANE_SCATTER_COLORS,
    PALETTE,
    apply_theme,
    callout,
    hairline,
    kicker,
)
from app.components.badges.confidence_label import ui_label_for_db_label
from app.pages import open_connection


def _lane_rows(conn) -> list[LaneRow]:
    rows = conn.execute(
        "SELECT * FROM v_lane_performance ORDER BY post_count DESC"
    ).fetchall()
    return lane_rows_from_sql(rows)


def _recent_post_scatter(conn, days_back: int = 30):
    cutoff = (_date_t.today() - timedelta(days=days_back)).isoformat()
    return conn.execute(
        """
        SELECT
            plm.post_id,
            p.created_date,
            plm.impressions,
            plm.pillar,
            plm.audience,
            plm.cta,
            plm.engagement_rate
        FROM v_post_latest_metrics plm
        JOIN posts p ON p.id = plm.post_id
        WHERE p.created_date >= ?
          AND plm.pillar IS NOT NULL
          AND plm.impressions IS NOT NULL
        ORDER BY p.created_date ASC
        """,
        (cutoff,),
    ).fetchall()


def _best_lane(lane_rows: list[LaneRow]) -> LaneRow | None:
    """Highest median-impressions lane among rankable (tentative+) lanes."""
    rankable = [
        r for r in lane_rows
        if ui_label_for_db_label(r.db_confidence_label) in {"tentative", "confident"}
        and r.median_impressions is not None
    ]
    if not rankable:
        return None
    return max(rankable, key=lambda r: r.median_impressions or 0)


def _build_scatter(rows) -> go.Figure:
    fig = go.Figure()
    if not rows:
        fig.update_layout(
            paper_bgcolor=PALETTE["ink"],
            plot_bgcolor=PALETTE["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
            annotations=[{
                "text": "No classified posts with impressions in the last 30 days.",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "showarrow": False,
                "font": {"family": "Fraunces, serif", "size": 14, "color": PALETTE["bone_dim"]},
            }],
            margin={"t": 20, "b": 60, "l": 60, "r": 20},
        )
        return fig
    # Group by lane so we can color-code per lane.
    by_lane: dict[tuple[str, str, str], list[tuple[str, int]]] = {}
    for r in rows:
        key = (r["pillar"], r["audience"], r["cta"])
        by_lane.setdefault(key, []).append((r["created_date"], int(r["impressions"])))

    # Lane palette is centrally enforced in theme.py to keep the
    # "no red anywhere, ever" rule in one place.
    for i, (lane, posts) in enumerate(by_lane.items()):
        dates_ = [p[0] for p in posts]
        imps = [p[1] for p in posts]
        fig.add_trace(
            go.Scatter(
                x=dates_,
                y=imps,
                mode="markers",
                marker={
                    "size": 10,
                    "color": LANE_SCATTER_COLORS[i % len(LANE_SCATTER_COLORS)],
                    "line": {"color": PALETTE["bone"], "width": 0.5},
                    "opacity": 0.85,
                },
                name=" · ".join(lane),
            )
        )
    fig.update_layout(
        paper_bgcolor=PALETTE["ink"],
        plot_bgcolor=PALETTE["ink"],
        font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
        xaxis={
            "title": "Date",
            "gridcolor": PALETTE["hairline"],
            "tickfont": {"family": "JetBrains Mono, monospace", "color": PALETTE["bone_dim"]},
        },
        yaxis={
            "title": "Impressions",
            "gridcolor": PALETTE["hairline"],
            "tickfont": {"family": "JetBrains Mono, monospace", "color": PALETTE["bone_dim"]},
        },
        legend={
            "orientation": "h",
            "y": -0.25,
            "font": {"family": "JetBrains Mono, monospace", "size": 10, "color": PALETTE["bone_dim"]},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"t": 20, "b": 80, "l": 60, "r": 20},
        height=440,
    )
    return fig


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

kicker("LANE ANALYSIS · §14.4 / §11")
st.title("Content performance")
st.caption(
    "Lanes are scored at four confidence tiers: "
    "**insufficient** (n<5 or days<3) · **directional** (n 5–14) · "
    "**tentative** (n 15–29, days ≥7) · **confident** (n ≥30, days ≥14). "
    "Ranking is only allowed at tentative or above."
)

lane_rows = _lane_rows(conn)
rankable_count = count_rankable_lanes(lane_rows)

# Best-lane callout — gated on the §14.4 anti-overfitting rule.
if rankable_count >= 3:
    best = _best_lane(lane_rows)
    if best is not None:
        ui_label = ui_label_for_db_label(best.db_confidence_label)
        chip_bg = confidence_color_for_ui_label(ui_label)
        callout(
            f"<em>Best lane (provisional):</em> "
            f"<span class='numeric'>{best.pillar} · {best.audience} · {best.cta}</span> "
            f"with median impressions "
            f"<span class='numeric'>{int(best.median_impressions or 0):,}</span> "
            f"(IQR <span class='numeric'>"
            f"{int(best.iqr_low or 0):,}–{int(best.iqr_high or 0):,}</span>). "
            f"<span style='background:{chip_bg}; padding:1px 6px; border-radius:2px;"
            f" font-family:JetBrains Mono,monospace; font-size:0.7rem;"
            f" letter-spacing:0.08em; text-transform:uppercase; color:#0e1116;'>"
            f"{ui_label}</span>"
        )
else:
    callout(
        "<em>No best-lane callout.</em> Fewer than 3 lanes are at "
        "<strong>tentative</strong> or above; ranking would be premature "
        "(§14.4 anti-overfitting rule). Read the grid and scatter below "
        "as evidence-in-progress, not a leaderboard."
    )

st.markdown("## Lane grid")
lane_performance_grid(lane_rows)

hairline()

# Scatter — raw evidence beneath the aggregate.
st.markdown("## Raw evidence — last 30 days")
st.markdown(
    "<p class='faint'>Every classified post in the last 30 days, colored by lane. "
    "When the lane grid is below threshold, this scatter is the honest read.</p>",
    unsafe_allow_html=True,
)
fig = _build_scatter(_recent_post_scatter(conn))
st.plotly_chart(fig, width="stretch")

hairline()

# Pre-publish scorer calibration (Phase 5.8 / §28.11). Joins shipped agent
# drafts to their pre-publish composite_label and the post's engagement;
# lets Daniel see whether "strong" labels actually outperformed "weak"
# over time. Empty rendering when no shipped agent drafts exist yet —
# Calibration view earns its place once Daniel has ≥10 shipped drafts.
_calibration_rows = conn.execute(
    """
    SELECT ps.composite_label,
           COUNT(*) AS n,
           AVG(plm.impressions) AS avg_impressions,
           AVG(plm.engagement_rate) AS avg_engagement_rate
    FROM agent_drafts ad
    JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
    JOIN posts p ON p.id = ad.final_post_id
    JOIN v_post_latest_metrics plm ON plm.post_id = p.id
    WHERE p.manual_confirmation_status = 'confirmed'
      AND plm.impressions IS NOT NULL
    GROUP BY ps.composite_label
    ORDER BY CASE ps.composite_label
      WHEN 'strong' THEN 0 WHEN 'viable' THEN 1 WHEN 'weak' THEN 2 ELSE 3 END
    """
).fetchall()
st.markdown("## Pre-publish scorer calibration")
st.caption(
    "Shipped agent drafts grouped by their §28.11 pre-publish "
    "composite_label, paired with what actually happened. The scorer is "
    "well-calibrated when 'strong' rows average above 'viable' above "
    "'weak'. When the order inverts, tune the score thresholds in "
    "`app/agent/prepublish_scorer.py` and bump SCORER_VERSION."
)
if not _calibration_rows:
    st.markdown(
        f"<div class='faint' style='font-size: 0.85rem; color: "
        f"{PALETTE['bone_dim']};'>"
        f"No shipped agent drafts with impressions yet. The calibration "
        f"table fills in as you ship agent-assisted posts."
        f"</div>",
        unsafe_allow_html=True,
    )
else:
    rows_html: list[str] = [
        f"<tr style='border-bottom: 1px solid {PALETTE['hairline']};'>"
        f"<th style='text-align:left; padding:0.4rem 0.6rem 0.4rem 0; "
        f"color:{PALETTE['bone_dim']}; font-weight: 500;'>label</th>"
        f"<th style='text-align:right; padding:0.4rem 0.6rem; "
        f"color:{PALETTE['bone_dim']}; font-weight: 500;'>n</th>"
        f"<th style='text-align:right; padding:0.4rem 0.6rem; "
        f"color:{PALETTE['bone_dim']}; font-weight: 500;'>avg impressions</th>"
        f"<th style='text-align:right; padding:0.4rem 0; "
        f"color:{PALETTE['bone_dim']}; font-weight: 500;'>avg engagement rate</th>"
        f"</tr>"
    ]
    for _r in _calibration_rows:
        _imp = f"{int(_r['avg_impressions']):,}" if _r["avg_impressions"] is not None else "—"
        _er = f"{_r['avg_engagement_rate']:.3f}" if _r["avg_engagement_rate"] is not None else "—"
        rows_html.append(
            f"<tr><td style='padding:0.3rem 0.6rem 0.3rem 0; color:{PALETTE['bone']};'>"
            f"{_r['composite_label']}</td>"
            f"<td class='numeric' style='text-align:right; padding:0.3rem 0.6rem; "
            f"color:{PALETTE['bone']};'>{int(_r['n'])}</td>"
            f"<td class='numeric' style='text-align:right; padding:0.3rem 0.6rem; "
            f"color:{PALETTE['bone']};'>{_imp}</td>"
            f"<td class='numeric' style='text-align:right; padding:0.3rem 0; "
            f"color:{PALETTE['bone']};'>{_er}</td></tr>"
        )
    st.markdown(
        "<table style='border-collapse: collapse; font-family: IBM Plex Sans, sans-serif;'>"
        + "".join(rows_html)
        + "</table>",
        unsafe_allow_html=True,
    )

hairline()

# What we can and can't learn — reinforces §13.
st.markdown("## What this view can and can't tell you")
st.markdown(
    f"""<table style='width:100%; border-collapse:collapse; font-family:IBM Plex Sans,sans-serif;'>
    <thead>
        <tr style='border-bottom: 1px solid {PALETTE['hairline']};'>
            <th style='text-align:left; padding:0.4rem 0; color:{PALETTE['bone_dim']};'>What it can</th>
            <th style='text-align:left; padding:0.4rem 0; color:{PALETTE['bone_dim']};'>What it can't</th>
        </tr>
    </thead>
    <tbody>
        <tr><td style='padding:0.4rem 0;'>Surface medians + IQR per lane once n≥5.</td>
            <td style='padding:0.4rem 0;'>Establish causation. Lanes correlate with outcomes; nothing here proves a lane <em>caused</em> a follower.</td></tr>
        <tr><td style='padding:0.4rem 0;'>Refuse to rank below the threshold.</td>
            <td style='padding:0.4rem 0;'>Tell you what to post next — only what category needs more data. See <strong>Next rep</strong>.</td></tr>
        <tr><td style='padding:0.4rem 0;'>Show outliers via IQR width.</td>
            <td style='padding:0.4rem 0;'>Adjust for time-of-day, platform algorithm shifts, cohort effects. Hence the Weekly Review counterfactual prompt.</td></tr>
    </tbody>
    </table>""",
    unsafe_allow_html=True,
)

# Agent integration (§14.4 + §28.7).
hairline()
st.markdown("### Ask the agent")
ag_a, ag_b = st.columns(2)
if ag_a.button("why is this lane underperforming? →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "content_perf_lane_analysis"
    st.switch_page("pages/9_Agent_Chat.py")
if ag_b.button("extract lesson from a post →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "content_perf_extract_lesson"
    st.switch_page("pages/9_Agent_Chat.py")
