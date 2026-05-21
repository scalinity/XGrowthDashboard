"""Follower-count line chart with the §13 noise-floor band overlay.

§13 rule 6 says velocity is suppressed when ``|delta_7d| < 10``, and §12's
"noise floor for follower count is ±2/day baseline" sets the band drawn
around yesterday's value. Days that fall inside the band should *not*
display a trend arrow — that's a UI rule the page enforces; this chart
visualises the band so the eye trains itself to read change against it.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go


@dataclass(frozen=True)
class FollowerPoint:
    snapshot_date: str
    followers_count: int


def follower_trend_chart(
    points: list[FollowerPoint],
    *,
    noise_floor_per_day: int = 2,
    rolling_window: int = 7,
) -> go.Figure:
    """Build the canonical follower-trend figure.

    - Raw daily followers (solid line).
    - Rolling mean over ``rolling_window`` days (dashed line).
    - Shaded band of ±noise_floor_per_day around the rolling mean so the eye
      can immediately tell whether today's value is "actually different" or
      noise per §12.

    Returns an empty (but valid) figure when ``points`` is empty so the
    caller can still render it without branching.
    """
    fig = go.Figure()
    if not points:
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Followers",
            annotations=[{
                "text": "No follower snapshots yet — log one from the Today view.",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "showarrow": False,
            }],
            template="simple_white",
        )
        return fig

    dates = [p.snapshot_date for p in points]
    values = [p.followers_count for p in points]

    rolling: list[float] = []
    for i in range(len(values)):
        window_start = max(0, i - rolling_window + 1)
        window = values[window_start : i + 1]
        rolling.append(sum(window) / len(window))

    upper = [v + noise_floor_per_day for v in rolling]
    lower = [v - noise_floor_per_day for v in rolling]

    # Band first (so it sits behind the lines).
    fig.add_trace(
        go.Scatter(
            x=dates + dates[::-1],
            y=upper + lower[::-1],
            fill="toself",
            fillcolor="rgba(31, 111, 235, 0.10)",
            line={"color": "rgba(0,0,0,0)"},
            name=f"Noise floor (±{noise_floor_per_day}/day)",
            hoverinfo="skip",
            showlegend=True,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=rolling,
            mode="lines",
            line={"dash": "dash", "color": "#1f6feb"},
            name=f"{rolling_window}-day rolling mean",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines+markers",
            line={"color": "#1c1f23"},
            name="Followers (raw)",
        )
    )
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Followers",
        legend={"orientation": "h", "y": -0.2},
        margin={"t": 20, "b": 60, "l": 60, "r": 20},
        template="simple_white",
        hovermode="x unified",
    )
    return fig
