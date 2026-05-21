"""Funnel chart — §14.5 App-Store attribution gap rendered as a visible break.

The most important property of this component is that *no conversion rate
ever spans the App-Store gap*. Self-reported app-store clicks and
downloads are different epistemic categories (§13). The funnel shows
clicks above and downloads below, with a clearly marked dashed separator
labelled with the broken-chain icon and a §14.5 reference.

The component is intentionally *not* a stock Plotly Funnel: those compute
between-stage conversion automatically. We render horizontal bars
manually so the gap row carries no numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go


@dataclass(frozen=True)
class FunnelStage:
    label: str
    value: int
    note: str
    is_gap: bool = False


# The label exposed in tests and in the rendered funnel. Keep stable —
# tests look for this exact substring to confirm the gap is visible.
APP_STORE_GAP_LABEL = "App Store gap — see §14.5"
APP_STORE_GAP_ICON = "🔗❌"


def build_funnel_stages(
    *,
    impressions: int,
    profile_visits_self_reported: int,
    app_store_clicks_self_reported: int,
    downloads: int,
    icp_testers_self_reported: int,
) -> list[FunnelStage]:
    """Assemble the five real stages + the dashed gap separator between
    app-store-clicks and downloads.
    """
    return [
        FunnelStage(
            label="X impressions",
            value=impressions,
            note="Estimate when unconfirmed at post level; exact when API/manual.",
        ),
        FunnelStage(
            label="Profile-click events (self-reported)",
            value=profile_visits_self_reported,
            note="From `stir_conversion_events` where event_type='profile_visit'.",
        ),
        FunnelStage(
            label="App-store-click events (self-reported)",
            value=app_store_clicks_self_reported,
            note="From `stir_conversion_events` where event_type='link_click'.",
        ),
        FunnelStage(
            label=f"{APP_STORE_GAP_ICON}  {APP_STORE_GAP_LABEL}",
            value=0,
            note="App Store does not report click→download attribution to us. "
                 "No conversion rate is computed across this row.",
            is_gap=True,
        ),
        FunnelStage(
            label="Downloads (self-reported / manual)",
            value=downloads,
            note="From `stir_conversion_events` where event_type='download'.",
        ),
        FunnelStage(
            label="Qualified ICP testers (self-reported)",
            value=icp_testers_self_reported,
            note="Only counted when attribution_method='self_reported' (§18 rule 11).",
        ),
    ]


def funnel_chart(stages: list[FunnelStage]) -> go.Figure:
    """Render the funnel as a horizontal-bar chart with a visible gap row."""
    fig = go.Figure()
    y_labels = [s.label for s in stages]
    bar_values = [s.value if not s.is_gap else 0 for s in stages]
    colors = [
        "#dde1e6" if s.is_gap else "#1f6feb"
        for s in stages
    ]
    # Make the gap row visually obvious even without numbers.
    pattern_shape = [
        "/" if s.is_gap else ""
        for s in stages
    ]

    fig.add_trace(
        go.Bar(
            y=y_labels,
            x=bar_values,
            orientation="h",
            marker={
                "color": colors,
                "line": {
                    "color": ["#6c757d" if s.is_gap else "#1c1f23" for s in stages],
                    "width": [2 if s.is_gap else 0 for s in stages],
                },
                "pattern": {"shape": pattern_shape},
            },
            text=[
                "(no conversion across this row)" if s.is_gap else f"{s.value:,}"
                for s in stages
            ],
            textposition="auto",
            hovertext=[s.note for s in stages],
            hoverinfo="text+x",
        )
    )
    # Keep insertion order top-to-bottom.
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        xaxis_title="Events",
        showlegend=False,
        margin={"t": 20, "b": 60, "l": 220, "r": 40},
        template="simple_white",
        height=420,
    )
    # Dashed horizontal line at the gap row makes the break unmissable even
    # on monochrome printouts.
    for idx, stage in enumerate(stages):
        if stage.is_gap:
            fig.add_hline(
                y=idx,
                line_dash="dash",
                line_color="#6c757d",
                line_width=2,
                annotation_text=stage.label,
                annotation_position="top right",
                annotation_font={"size": 11, "color": "#6c757d"},
            )
    return fig


WHAT_WE_KNOW_TABLE_ROWS: list[tuple[str, str]] = [
    ("X impressions per post",
     "Exact when API/manual import; estimate otherwise (§13 rule 7)."),
    ("Profile-visit / link-click events",
     "Logged manually as `stir_conversion_events` — self-reported."),
    ("Downloads",
     "Counted; **never inferred from clicks** (§14.5 / §18 rule 11)."),
    ("App-store clicks → downloads",
     "Apple does not report this. No conversion rate is calculated."),
    ("Working-parent / home-cook ICP status",
     "Self-reported only (§18 rule 11). Inferred values are forbidden."),
    ("Activation events (kitchen scan, plausible dinners, Cook Mode)",
     "Manual log when seen; never assumed from prior steps."),
]
