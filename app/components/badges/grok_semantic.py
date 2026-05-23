"""Phase 9 §29.7 grok_semantic provenance badge.

P9R-47 — extracted as a pure helper from
``app/pages/10_Reply_Target_Queue.py`` so the badge HTML can be
unit-tested without importing the Streamlit page (whose
page-level code runs at import). The Queue page imports
``render_grok_badge_html`` and concatenates the return value into
the row card's @handle line.

Hard-coded HTML — NO user-controlled values are interpolated.
"""

from __future__ import annotations

# Phosphor-green pill matching the theme's accent palette
# (theme.py PALETTE['phosphor']). Letter-spacing + uppercase
# matches the kicker label convention used throughout the queue.
_GROK_BADGE_HTML: str = (
    "<span style='display:inline-block;"
    "background:rgba(126,201,126,0.12);"
    "color:#7ec97e;"
    "font-family:\"JetBrains Mono\", monospace;"
    "font-size:0.65rem;"
    "letter-spacing:0.04em;"
    "text-transform:uppercase;"
    "padding:0.08rem 0.4rem;"
    "border:1px solid rgba(126,201,126,0.28);"
    "border-radius:2px;"
    "margin-left:0.45rem;"
    "vertical-align:middle;'>"
    "grok_semantic"
    "</span>"
)


def render_grok_badge_html(discovered_via: str | None) -> str:
    """Return the grok_semantic badge HTML when applicable, empty otherwise.

    Pure function, no Streamlit imports — safe to unit-test.
    Only renders when ``discovered_via == 'grok_semantic'``. The
    badge HTML contains no interpolated values, so the return is
    safe for ``unsafe_allow_html=True`` rendering.
    """
    if (discovered_via or "").strip() == "grok_semantic":
        return _GROK_BADGE_HTML
    return ""


__all__ = ["render_grok_badge_html"]
