"""Repetition guard banner (§28.13).

Yellow banner rendered above an agent draft when the §28.13 guard
labeled it `near_duplicate` or `close_echo`. Shows the nearest post's
excerpt + the cosine score; no banner for `distinct` or `None`.

The "intentional / let me rewrite" affordance is purely informational
in the spec — clicking either choice does not write to the DB. The
banner is a thinking-prompt, not a state machine.
"""

from __future__ import annotations

import html
import json

from app._optional_streamlit import st

from app.components.theme import PALETTE

_LABEL_PRESENTATION = {
    "near_duplicate": (
        "NEAR DUPLICATE",
        "You've shipped almost exactly this idea before. Decide consciously.",
    ),
    "close_echo": (
        "CLOSE ECHO",
        "Similar to a recent post. Worth a glance before publishing.",
    ),
}


def repetition_banner(similarity_warning_json: str | dict | None) -> None:
    """Render the banner. Accepts the raw JSON string or a parsed dict.

    Returns silently on None, on parse error, or on label='distinct'.
    """
    if not similarity_warning_json:
        return
    if isinstance(similarity_warning_json, str):
        try:
            warning = json.loads(similarity_warning_json)
        except (json.JSONDecodeError, TypeError):
            return
    else:
        warning = similarity_warning_json
    if not isinstance(warning, dict):
        return
    label = str(warning.get("label", "")).lower()
    if label not in _LABEL_PRESENTATION:
        return
    chip_label, summary = _LABEL_PRESENTATION[label]
    cosine = warning.get("max_cosine")
    nearest_id = warning.get("nearest_post_id")
    excerpt = html.escape(str(warning.get("nearest_text_excerpt", "")))
    cosine_str = f"{float(cosine):.2f}" if isinstance(cosine, (int, float)) else "—"

    st.markdown(
        f"<div style='border-left: 3px solid {PALETTE['warn_amber']}; "
        f"background: {PALETTE['surface_raised']}; "
        f"padding: 0.5rem 0.8rem; margin: 0.4rem 0;'>"
        f"<div class='numeric' style='font-size: 0.7rem; letter-spacing: 0.08em; "
        f"color: {PALETTE['warn_amber']}; text-transform: uppercase;'>"
        f"REPETITION GUARD · {chip_label} · COSINE {cosine_str}"
        f"</div>"
        f"<div style='font-size: 0.85rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.3rem;'>{summary}</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 0.95rem; "
        f"color: {PALETTE['bone_dim']}; margin-top: 0.4rem; "
        f"font-style: italic;'>"
        f"Nearest post #{int(nearest_id) if nearest_id else '?'}: "
        f"{excerpt}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
