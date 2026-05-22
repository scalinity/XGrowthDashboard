"""Funnel — spec.md §14.5.

Tracks whether X growth converts into Stir validation. The view's most
important property: the App Store attribution gap is rendered as a visible
break, with no conversion rate ever computed across it (§13 hard rule 11).
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

from app.components.charts.funnel import (
    APP_STORE_GAP_LABEL,
    WHAT_WE_KNOW_TABLE_ROWS,
    build_funnel_stages,
    funnel_chart,
)
from app.components.theme import PALETTE, apply_theme, callout, hairline, kicker
from app.pages import open_connection


def _aggregate_funnel(conn, days_back: int = 30) -> dict[str, int]:
    """Sum the v_funnel_daily rows over the last N days."""
    cutoff = (_date_t.today() - timedelta(days=days_back)).isoformat()
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(x_impressions_estimate), 0)        AS x_impressions_estimate,
            COALESCE(SUM(profile_visits), 0)                AS profile_visits,
            COALESCE(SUM(link_clicks), 0)                   AS link_clicks,
            COALESCE(SUM(getstir_visits), 0)                AS getstir_visits,
            COALESCE(SUM(downloads), 0)                     AS downloads,
            COALESCE(SUM(qualified_icp_testers), 0)         AS qualified_icp_testers,
            COALESCE(SUM(working_parent_home_cook_testers), 0) AS working_parent_home_cook_testers
        FROM v_funnel_daily
        WHERE event_date >= ?
        """,
        (cutoff,),
    ).fetchone()
    return {k: int(row[k] or 0) for k in row.keys()}


def _daily_breakdown(conn, days_back: int = 30):
    cutoff = (_date_t.today() - timedelta(days=days_back)).isoformat()
    return conn.execute(
        """
        SELECT
            event_date, x_impressions_estimate, profile_visits, link_clicks,
            getstir_visits, downloads, qualified_icp_testers,
            working_parent_home_cook_testers
        FROM v_funnel_daily
        WHERE event_date >= ?
        ORDER BY event_date ASC
        """,
        (cutoff,),
    ).fetchall()


def _build_daily_stacked(rows) -> go.Figure:
    fig = go.Figure()
    if not rows:
        fig.update_layout(
            paper_bgcolor=PALETTE["ink"],
            plot_bgcolor=PALETTE["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
            annotations=[{
                "text": "No funnel events in the last 30 days.",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "showarrow": False,
                "font": {"family": "Fraunces, serif", "color": PALETTE["bone_dim"]},
            }],
            margin={"t": 20, "b": 60, "l": 60, "r": 20},
        )
        return fig
    dates = [r["event_date"] for r in rows]
    stages = [
        ("Profile visits", "profile_visits", PALETTE["confidence_directional_bg"]),
        ("Link clicks", "link_clicks", PALETTE["confidence_tentative_bg"]),
        ("getstir.app visits", "getstir_visits", PALETTE["phosphor"]),
        ("Downloads", "downloads", PALETTE["confidence_confident_bg"]),
    ]
    for label, key, color in stages:
        fig.add_trace(go.Bar(
            x=dates,
            y=[int(r[key] or 0) for r in rows],
            name=label,
            marker={"color": color},
        ))
    fig.update_layout(
        barmode="stack",
        paper_bgcolor=PALETTE["ink"],
        plot_bgcolor=PALETTE["ink"],
        font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
        xaxis={
            "title": "Date",
            "gridcolor": PALETTE["hairline"],
            "tickfont": {"family": "JetBrains Mono, monospace", "color": PALETTE["bone_dim"]},
        },
        yaxis={
            "title": "Events",
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
        height=360,
    )
    return fig


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

kicker("X → STIR · §14.5")
st.title("Funnel")
st.caption(
    "Distribution signal (top of funnel) is one epistemic category; "
    "validation signal (downloads, testers) is another. There is no "
    "click-to-download conversion rate — the App Store does not report it."
)

agg = _aggregate_funnel(conn)

# Funnel chart — top section.
st.markdown("## Last 30 days")
stages = build_funnel_stages(
    impressions=agg["x_impressions_estimate"],
    profile_visits_self_reported=agg["profile_visits"],
    app_store_clicks_self_reported=agg["link_clicks"],
    downloads=agg["downloads"],
    icp_testers_self_reported=agg["qualified_icp_testers"],
)
fig = funnel_chart(stages)
st.plotly_chart(fig, width="stretch")

callout(
    f"<em>{APP_STORE_GAP_LABEL}.</em> Apple does not provide click-to-download "
    "attribution to publishers. Self-reported app-store clicks and "
    "downloads sit on either side of the gap; treating them as parts of "
    "a single conversion rate would invent a number the data cannot "
    "support."
)

hairline()

# What we know / What we don't.
st.markdown("## What we know · what we don't")
st.markdown(
    "<p class='faint'>The §13 hard rules made visible. Hover any row in "
    "the funnel above to see the source of that stage's number.</p>",
    unsafe_allow_html=True,
)
st.markdown(
    f"""<table style='width:100%; border-collapse:collapse;
                        font-family:IBM Plex Sans,sans-serif; font-size:0.92rem;'>
        <thead>
            <tr style='border-bottom: 1px solid {PALETTE['hairline']};'>
                <th style='text-align:left; padding:0.4rem 0; color:{PALETTE['bone_dim']};
                            font-family:JetBrains Mono,monospace; font-size:0.74rem;
                            letter-spacing:0.08em; text-transform:uppercase;'>Topic</th>
                <th style='text-align:left; padding:0.4rem 0; color:{PALETTE['bone_dim']};
                            font-family:JetBrains Mono,monospace; font-size:0.74rem;
                            letter-spacing:0.08em; text-transform:uppercase;'>Rule</th>
            </tr>
        </thead>
        <tbody>
        {"".join(
            f"<tr style='border-bottom: 1px solid {PALETTE['hairline']};'>"
            f"<td style='padding:0.5rem 0; color:{PALETTE['bone']};'>{topic}</td>"
            f"<td style='padding:0.5rem 0; color:{PALETTE['bone_dim']};'>{rule}</td>"
            f"</tr>"
            for topic, rule in WHAT_WE_KNOW_TABLE_ROWS
        )}
        </tbody>
    </table>""",
    unsafe_allow_html=True,
)

hairline()

# Daily breakdown.
st.markdown("## Daily breakdown")
fig2 = _build_daily_stacked(_daily_breakdown(conn))
st.plotly_chart(fig2, width="stretch")
st.markdown(
    "<p class='faint'>Stacked: profile visits, link clicks, getstir.app "
    "visits, downloads. The four series are independent — they do not "
    "compose into a single funnel because the App Store gap separates "
    "intent (clicks) from outcome (downloads).</p>",
    unsafe_allow_html=True,
)
