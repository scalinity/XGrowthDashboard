"""Pre-publish composite-label chip (§28.11).

Three labels (`weak | viable | strong`) mapped to the existing
confidence-badge palette so the dashboard has one and only one
color-coding rule across surfaces:

  * `strong`  → green (`confidence_confident_bg`)
  * `viable`  → blue  (`confidence_tentative_bg`)
  * `weak`    → amber (`confidence_directional_bg`)

Never red — same rule as `confidence_label`: this is a question about
evidence, not a verdict on the draft. The label is informational; the
publish flow does not consult it.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from app.components.theme import PALETTE


@dataclass(frozen=True)
class _ChipPresentation:
    ui_label: str
    color_hex: str
    background_hex: str
    description: str


COMPOSITE_LABEL_PRESENTATION: dict[str, _ChipPresentation] = {
    "weak": _ChipPresentation(
        ui_label="weak",
        color_hex=PALETTE["confidence_directional_fg"],
        background_hex=PALETTE["confidence_directional_bg"],
        description=(
            "Pre-publish scorer flagged ≥1 dimension at zero, or too few "
            "dimensions at 2+. Read the score breakdown before shipping. "
            "(Soft signal — never blocks publish.)"
        ),
    ),
    "viable": _ChipPresentation(
        ui_label="viable",
        color_hex=PALETTE["confidence_tentative_fg"],
        background_hex=PALETTE["confidence_tentative_bg"],
        description=(
            "Most dimensions are 2+, no zeros. The draft would land but "
            "isn't a polished standalone. Worth a re-read."
        ),
    ),
    "strong": _ChipPresentation(
        ui_label="strong",
        color_hex=PALETTE["confidence_confident_fg"],
        background_hex=PALETTE["confidence_confident_bg"],
        description=(
            "Six+ dimensions at 2+, two+ at 3, no zeros. "
            "Ready to ship — still your call."
        ),
    ),
}

CHIP_TOOLTIP = (
    "Pre-publish scorer (§28.11): deterministic 9-dimension read. "
    "Click the row for the full score breakdown. "
    "Informational — never gates publish."
)


def prepublish_chip(label: str | None) -> None:
    """Inline pill rendered at the call-site. `label` is the
    `prepublish_scores.composite_label` value (`weak | viable | strong`).
    `None` renders nothing.
    """
    if label is None:
        return
    key = str(label).strip().lower()
    if key not in COMPOSITE_LABEL_PRESENTATION:
        return
    pres = COMPOSITE_LABEL_PRESENTATION[key]
    title_text = (pres.description + " " + CHIP_TOOLTIP).replace("\n", " ")
    st.markdown(
        f"""<span title="{title_text}" style="
            display: inline-block;
            padding: 2px 10px;
            border-radius: 2px;
            background-color: {pres.background_hex};
            color: {pres.color_hex};
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.74em;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-weight: 600;
            ">PRE-PUBLISH · {pres.ui_label}</span>""",
        unsafe_allow_html=True,
        help=pres.description + "\n\n" + CHIP_TOOLTIP,
    )


def render_score_panel(score_row: dict) -> None:
    """Click-to-reveal panel showing the per-dimension 0-3 scores.

    `score_row` is the dict returned by
    `app.agent.prepublish_scorer.get_score_for_draft`. Renders one row
    per dimension with the score and the human-readable warnings list.
    """
    if not score_row:
        return
    dims = [
        ("clarity", "Clarity"),
        ("hook_strength", "Hook strength"),
        ("specificity", "Specificity"),
        ("length_fit", "Length fit"),
        ("format_fit", "Format fit"),
        ("topic_fit", "Topic fit"),
        ("reply_substance", "Reply substance"),
        ("cta_strength", "CTA strength"),
        ("voice_fit", "Voice fit"),
    ]
    rows: list[str] = []
    for key, label in dims:
        col = key + "_score"
        val = score_row.get(col)
        if val is None:
            display = "—"
            color = PALETTE["bone_faint"]
        else:
            display = str(int(val))
            if val == 0:
                color = PALETTE["confidence_directional_bg"]
            elif val == 3:
                color = PALETTE["confidence_confident_bg"]
            else:
                color = PALETTE["bone"]
        rows.append(
            f"<tr><td style='padding: 0.18rem 0.6rem 0.18rem 0; "
            f"color: {PALETTE['bone_dim']}; font-size: 0.82rem;'>{label}</td>"
            f"<td class='numeric' style='padding: 0.18rem 0; color: {color}; "
            f"font-weight: 600;'>{display}</td></tr>"
        )
    st.markdown(
        "<table style='border-collapse: collapse;'>"
        + "".join(rows)
        + "</table>",
        unsafe_allow_html=True,
    )
    warnings_json = score_row.get("warnings_json")
    if warnings_json:
        import json as _json
        try:
            warnings = _json.loads(warnings_json)
        except (_json.JSONDecodeError, TypeError):
            warnings = []
        for w in warnings:
            st.markdown(
                f"<div style='color: {PALETTE['confidence_directional_bg']}; "
                f"font-size: 0.82rem; padding: 0.2rem 0;'>• {w}</div>",
                unsafe_allow_html=True,
            )
