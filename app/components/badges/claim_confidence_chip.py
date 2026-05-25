"""Claim-confidence chip (§28.14) — distinct from confidence_label.py.

`confidence_label.py` renders the §11 lane-performance confidence badge
(insufficient / directional / tentative / confident — sample-size
discipline).

This module renders the §28.14 agent-claim chip: green `fact`, blue
`inference`, yellow `speculation`, gray `mixed`. The two badge systems
share the colorblind palette tokens but carry different meanings; they
should never appear in the same row without context.
"""

from __future__ import annotations

from dataclasses import dataclass

from app._optional_streamlit import st

from app.components.theme import PALETTE


@dataclass(frozen=True)
class _ConfChipPresentation:
    ui_label: str
    fg: str
    bg: str
    description: str


CONFIDENCE_CHIP_PRESENTATION: dict[str, _ConfChipPresentation] = {
    "fact": _ConfChipPresentation(
        ui_label="fact",
        fg=PALETTE["confidence_confident_fg"],
        bg=PALETTE["confidence_confident_bg"],
        description="Directly from a tool result the agent just received.",
    ),
    "inference": _ConfChipPresentation(
        ui_label="inference",
        fg=PALETTE["confidence_tentative_fg"],
        bg=PALETTE["confidence_tentative_bg"],
        description="Drawn from data but involves judgment.",
    ),
    "speculation": _ConfChipPresentation(
        ui_label="speculation",
        fg=PALETTE["confidence_directional_fg"],
        bg=PALETTE["confidence_directional_bg"],
        description="The agent has no data and is guessing.",
    ),
    "mixed": _ConfChipPresentation(
        ui_label="mixed",
        fg=PALETTE["confidence_insufficient_fg"],
        bg=PALETTE["confidence_insufficient_bg"],
        description="Combines factual citation with inference.",
    ),
}


def claim_confidence_chip(label: str | None) -> None:
    """Render the agent-claim confidence chip inline. None → nothing."""
    if label is None:
        return
    key = str(label).strip().lower()
    if key not in CONFIDENCE_CHIP_PRESENTATION:
        return
    pres = CONFIDENCE_CHIP_PRESENTATION[key]
    title_text = f"§28.14 confidence label: {pres.description}"
    st.markdown(
        f"""<span title="{title_text}" style="
            display: inline-block;
            padding: 2px 10px;
            border-radius: 2px;
            background-color: {pres.bg};
            color: {pres.fg};
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.74em;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-weight: 600;
            ">CLAIM · {pres.ui_label}</span>""",
        unsafe_allow_html=True,
        help=pres.description,
    )
