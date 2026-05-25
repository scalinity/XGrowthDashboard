"""Sample-size badge — n-of-N with the §14.4 boundary tooltip.

Used wherever a metric is computed from a sample (lane medians, weekly-
review summary cards, content-performance table). The tooltip text repeats
the four-tier graduated confidence rule exactly as it appears in §11 so
hovering over any aggregate is enough to know whether to trust the number.
"""

from __future__ import annotations

from app._optional_streamlit import st

from app.components.badges.confidence_label import SAMPLE_SIZE_TOOLTIP
from app.components.theme import PALETTE


def sample_size_badge(n: int, *, n_target: int | None = None) -> None:
    """Render an inline "n=X" pill.

    Hover surfaces the four-tier boundary rule. `n_target` is the threshold
    *the call-site* cares about (e.g. lane_sample_size_stronger = 30); when
    supplied it's added as a small "/ target" subscript.
    """
    label = f"n={n}"
    if n_target is not None:
        label += f" / {n_target}"

    st.markdown(
        f"""<span title="{SAMPLE_SIZE_TOOLTIP}" style="
            display: inline-block;
            padding: 1px 8px;
            border-radius: 2px;
            background-color: {PALETTE['surface_raised']};
            color: {PALETTE['bone_dim']};
            border: 1px solid {PALETTE['hairline']};
            font-size: 0.72em;
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 0.04em;
            ">{label}</span>""",
        unsafe_allow_html=True,
        help=SAMPLE_SIZE_TOOLTIP,
    )
