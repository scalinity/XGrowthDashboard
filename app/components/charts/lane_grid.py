"""Lane-performance grid — §14.4 graduated confidence rendering.

Reads ``v_lane_performance`` rows and lays them out as a table that obeys
the §14.4 / §11 rules:

- Insufficient sample (n<5 OR days<3) → median is "—", not a number.
- Directional (n 5-14) → median + IQR shown, no ranking allowed.
- Tentative (n 15-29) → median + IQR + ranking allowed.
- Confident (n ≥ 30, days ≥ 14) → highest confidence label.

The component never sorts/ranks lanes itself — the caller decides whether
the count of "tentative+" lanes is high enough to surface a "best lane"
callout (the §14.4 anti-overfitting gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import streamlit as st

from app.components.badges import (
    UI_LABEL_PRESENTATION,
    confidence_badge,
    sample_size_badge,
    ui_label_for_db_label,
)


@dataclass(frozen=True)
class LaneRow:
    pillar: str
    audience: str
    cta: str
    post_count: int
    days_covered: int
    median_impressions: float | None
    iqr_low: float | None
    iqr_high: float | None
    total_bookmarks: int
    total_replies: int
    stir_signal_count: int
    db_confidence_label: str


def lane_rows_from_sql(rows: list[Any]) -> list[LaneRow]:
    """Adapter for sqlite3.Row sequences returned by v_lane_performance."""
    out: list[LaneRow] = []
    for r in rows:
        out.append(
            LaneRow(
                pillar=r["pillar"],
                audience=r["audience"],
                cta=r["cta"],
                post_count=int(r["post_count"]),
                days_covered=int(r["days_covered"]),
                median_impressions=r["median_impressions"],
                iqr_low=r["iqr_impressions_low"],
                iqr_high=r["iqr_impressions_high"],
                total_bookmarks=int(r["total_bookmarks"] or 0),
                total_replies=int(r["total_replies"] or 0),
                stir_signal_count=int(r["stir_signal_count"] or 0),
                db_confidence_label=r["confidence_label"],
            )
        )
    return out


def _format_median_with_iqr(row: LaneRow) -> str:
    """Render median (IQR low–high) or "—" if insufficient."""
    ui_label = ui_label_for_db_label(row.db_confidence_label)
    if ui_label == "insufficient":
        return "—"
    if row.median_impressions is None:
        return "—"
    if row.iqr_low is None or row.iqr_high is None:
        return f"{int(round(row.median_impressions)):,}"
    return (
        f"{int(round(row.median_impressions)):,} "
        f"[{int(round(row.iqr_low)):,}–{int(round(row.iqr_high)):,}]"
    )


def lane_performance_grid(lane_rows: list[LaneRow]) -> None:
    """Render the lane grid. No ranking is performed here — the caller decides."""
    if not lane_rows:
        st.info(
            "No classified posts yet. Classify a few from **Manual entry → "
            "Needs tagging** to populate this grid."
        )
        return

    # Header row.
    header_cols = st.columns([2, 1, 1, 2, 2, 1, 1, 1, 2])
    header_cols[0].markdown("**Lane**")
    header_cols[1].markdown("**Posts**")
    header_cols[2].markdown("**Days**")
    header_cols[3].markdown("**Median impressions (IQR)**")
    header_cols[4].markdown("**Median engagement (IQR)**")
    header_cols[5].markdown("**Bookmarks**")
    header_cols[6].markdown("**Replies**")
    header_cols[7].markdown("**Stir**")
    header_cols[8].markdown("**Confidence**")

    for row in lane_rows:
        cols = st.columns([2, 1, 1, 2, 2, 1, 1, 1, 2])
        cols[0].write(f"`{row.pillar}` · `{row.audience}` · `{row.cta}`")
        with cols[1]:
            sample_size_badge(row.post_count)
        cols[2].write(str(row.days_covered))
        cols[3].write(_format_median_with_iqr(row))
        # Engagement-rate display intentionally omitted at MVP — the spec
        # surfaces it in the per-post table, not the lane grid (the grid is
        # impressions-led to keep the eye on volume vs. resonance).
        ui_label = ui_label_for_db_label(row.db_confidence_label)
        if ui_label == "insufficient":
            cols[4].write("—")
        else:
            cols[4].write("(see scatter)")
        cols[5].write(f"{row.total_bookmarks:,}")
        cols[6].write(f"{row.total_replies:,}")
        cols[7].write(f"{row.stir_signal_count:,}")
        with cols[8]:
            confidence_badge(row.db_confidence_label, post_count=row.post_count)


def count_rankable_lanes(lane_rows: list[LaneRow]) -> int:
    """Lanes at 'tentative' or higher — used to gate the best-lane callout (§14.4)."""
    return sum(
        1
        for r in lane_rows
        if ui_label_for_db_label(r.db_confidence_label) in {"tentative", "confident"}
    )


def confidence_color_for_ui_label(ui_label: str) -> str:
    """Public accessor so scatter/legend colors stay consistent."""
    return UI_LABEL_PRESENTATION[ui_label].background_hex
