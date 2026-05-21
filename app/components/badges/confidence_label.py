"""Confidence-label badge per spec.md §14.4 / §11 boundaries.

`v_lane_performance.confidence_label` returns one of four spec strings
("insufficient sample" / "low — show scatter, do not rank" / "moderate" /
"stronger"). The Phase 3 prompt asks the *user-facing* labels to read
"insufficient" / "directional" / "tentative" / "confident" with colorblind-
friendly badge colors. The DB stays source-of-truth; this module is the
single place the mapping lives.

Colors:
  insufficient  — gray   (#6c757d)
  directional   — yellow (#d9a300)
  tentative     — blue   (#1f6feb)
  confident     — green  (#1f8a3a)

Never red. Sample-size labels frame a question about evidence, not failure.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class _Presentation:
    ui_label: str
    color_hex: str
    background_hex: str
    description: str


UI_LABEL_PRESENTATION: dict[str, _Presentation] = {
    "insufficient": _Presentation(
        ui_label="insufficient",
        color_hex="#1c1f23",
        background_hex="#dde1e6",
        description="n < 5 OR days_covered < 3 — show scatter, no medians.",
    ),
    "directional": _Presentation(
        ui_label="directional",
        color_hex="#1c1f23",
        background_hex="#ffe39a",
        description="n is 5–14 — medians + IQR shown; no ordinal ranking.",
    ),
    "tentative": _Presentation(
        ui_label="tentative",
        color_hex="#ffffff",
        background_hex="#1f6feb",
        description="n is 15–29 with 7+ days — ranking allowed.",
    ),
    "confident": _Presentation(
        ui_label="confident",
        color_hex="#ffffff",
        background_hex="#1f8a3a",
        description="n ≥ 30 with 14+ days — ranking with confidence.",
    ),
}

DB_LABEL_TO_UI: dict[str, str] = {
    "insufficient sample": "insufficient",
    "low — show scatter, do not rank": "directional",
    "moderate": "tentative",
    "stronger": "confident",
}


# Plain-text tooltip used by every aggregated metric in the dashboard.
SAMPLE_SIZE_TOOLTIP = (
    "Confidence label boundaries: "
    "n<4 insufficient · n[4-14] directional · n[15-29] tentative · "
    "n≥30 confident. Days covered must also be ≥3 for any label above "
    "insufficient, ≥7 for tentative, ≥14 for confident."
)


def ui_label_for_db_label(db_label: str | None) -> str:
    """Translate the DB string → the four user-facing labels.

    Unknown labels fall back to 'insufficient' so a future DB-side change
    does not silently produce a confidently-ranked lane the UI cannot trust.
    """
    if db_label is None:
        return "insufficient"
    return DB_LABEL_TO_UI.get(db_label.strip(), "insufficient")


def confidence_badge(db_label: str | None, *, post_count: int | None = None) -> None:
    """Render an inline confidence pill at the call-site.

    `post_count` is optional and only affects the hover tooltip — the badge
    color/label are driven entirely by `db_label` (i.e. by
    v_lane_performance.confidence_label, which already encodes the §11 rule).
    """
    ui_label = ui_label_for_db_label(db_label)
    pres = UI_LABEL_PRESENTATION[ui_label]

    if post_count is None:
        help_text = pres.description + "\n\n" + SAMPLE_SIZE_TOOLTIP
    else:
        help_text = (
            f"Based on n={post_count} posts. {pres.description}\n\n"
            f"{SAMPLE_SIZE_TOOLTIP}"
        )

    st.markdown(
        f"""<span title="{help_text}" style="
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            background-color: {pres.background_hex};
            color: {pres.color_hex};
            font-size: 0.85em;
            font-weight: 600;
            ">{pres.ui_label}</span>""",
        unsafe_allow_html=True,
        help=help_text,
    )
