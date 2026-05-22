"""Shared dark "instrument-panel" theme for every dashboard view.

Aesthetic direction — locked in per project CLAUDE.md "UI work":

- **Background**: deep ink `#0e1116`, raised surface `#161a20`.
- **Primary text**: warm bone `#e6e1d8` (not cool white — easier on the eyes
  during the morning ritual, and physical-instrument aesthetic).
- **Accent**: phosphor cyan-teal `#5fb3a1` for chart lines + section keylines.
  Phosphor green/cyan is the colour you remember from oscilloscopes; it
  evokes "measurement instrument" without being literally retro.
- **Typography**:
  - *Display serif* — **Fraunces** (Google Fonts variable). Used for view
    titles and major numeric callouts. Characterful, slightly editorial.
  - *Body sans* — **IBM Plex Sans**. Neutral, distinctive, free.
  - *Mono* — **JetBrains Mono**. Used for EVERY number in the UI so figures
    line up vertically and read like a lab readout.
- **No red.** Sample-size labels frame a question about evidence, not failure.

The CSS overrides below are deliberately scoped. Streamlit's component DOM
is opaque, but we hook a few well-known classes (`stMetric`, `stTitle`,
`stMarkdown` headings) plus our own classnames (`.hairline`, `.callout`,
`.numeric`) that the components reach for.
"""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Palette — referenced by chart/badge modules. Adding a new color? Add it
# here once, never inline a literal in a component.
# ---------------------------------------------------------------------------
PALETTE = {
    # Surfaces.
    "ink":             "#0e1116",
    "surface":         "#161a20",
    "surface_raised":  "#1c2128",
    "hairline":        "#2a2f37",

    # Type.
    "bone":            "#e6e1d8",
    "bone_dim":        "#a8a39a",
    "bone_faint":      "#6c6960",

    # Accents.
    "phosphor":        "#5fb3a1",   # primary chart / key-line accent
    "phosphor_dim":    "#3d7a6c",   # for muted variants

    # Confidence badges. Colorblind-friendly four-tier (gray-amber-blue-green).
    "confidence_insufficient_bg": "#2a2f37",
    "confidence_insufficient_fg": "#a8a39a",
    "confidence_directional_bg":  "#c98b16",   # warm amber on dark
    "confidence_directional_fg":  "#0e1116",
    "confidence_tentative_bg":    "#3a73e0",
    "confidence_tentative_fg":    "#ffffff",
    "confidence_confident_bg":    "#2da564",
    "confidence_confident_fg":    "#0e1116",

    # Functional.
    "noise_band":      "rgba(95, 179, 161, 0.12)",  # phosphor at 12% opacity
}

# ---------------------------------------------------------------------------
# Lane-scatter palette. One enforcement point for the "no red" rule.
# Used by Content Performance and (if added later) other multi-lane charts.
# Adding a colour? Pick from the colorblind-friendly cool half of the
# wheel — teals, blues, ambers, greens, plums, never red.
# ---------------------------------------------------------------------------
LANE_SCATTER_COLORS: list[str] = [
    "#5fb3a1",   # phosphor (primary)
    "#3a73e0",   # tentative-blue
    "#c98b16",   # directional-amber
    "#2da564",   # confident-green
    "#a8a39a",   # bone_dim
    "#a87fce",   # muted plum
    "#3b8a8a",   # deep teal (replaces the rejected reddish tone)
    "#7ecfd9",   # pale cyan
]

# ---------------------------------------------------------------------------
# Fonts. We import Fraunces + IBM Plex Sans + JetBrains Mono from Google
# Fonts via @import inside a <style> tag. One network round-trip, cached.
# ---------------------------------------------------------------------------
_FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght,SOFT@9..144,300..900,0..100&"
    "family=IBM+Plex+Sans:wght@300;400;500;600&"
    "family=JetBrains+Mono:wght@400;500;600&"
    "display=swap');"
)

_CSS_TEMPLATE = """
<style>
{font_import}

/* Whole-app baseline. */
html, body, [class*="stApp"] {{
    font-family: 'IBM Plex Sans', -apple-system, sans-serif;
    color: {bone};
    background-color: {ink};
}}

/* Page titles + section headings. */
h1 {{
    font-family: 'Fraunces', 'IBM Plex Serif', Georgia, serif;
    font-weight: 500;
    font-variation-settings: 'opsz' 144, 'SOFT' 50;
    font-size: 2.6rem;
    letter-spacing: -0.015em;
    color: {bone};
    margin-bottom: 0.25em;
    line-height: 1.05;
}}
h2 {{
    font-family: 'Fraunces', 'IBM Plex Serif', Georgia, serif;
    font-weight: 400;
    font-style: italic;
    font-variation-settings: 'opsz' 36, 'SOFT' 80;
    font-size: 1.6rem;
    color: {bone};
    letter-spacing: -0.005em;
    margin-top: 1.5em;
    margin-bottom: 0.6em;
}}
h3 {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 500;
    font-size: 1.05rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: {bone_dim};
    margin-top: 1.4em;
    margin-bottom: 0.4em;
}}

/* All Streamlit metric numbers — render in JetBrains Mono so columns align. */
[data-testid="stMetricValue"] {{
    font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace !important;
    font-weight: 500;
    font-size: 2rem;
    color: {bone};
    letter-spacing: -0.02em;
}}
[data-testid="stMetricLabel"] {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.78rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: {bone_dim};
}}
[data-testid="stMetricDelta"] {{
    font-family: 'JetBrains Mono', monospace;
    font-weight: 500;
    font-size: 0.9rem;
}}

/* Captions: small, dim, neutral. The honest-disclaimer texture. */
[data-testid="stCaptionContainer"], .stCaption {{
    color: {bone_faint};
    font-size: 0.82rem;
    font-style: italic;
}}

/* Sidebar — match the surface tone. */
[data-testid="stSidebar"] {{
    background-color: {surface};
    border-right: 1px solid {hairline};
}}

/* Buttons — flat, mono labels, phosphor accent. */
.stButton > button {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    background-color: {surface_raised};
    border: 1px solid {hairline};
    color: {bone};
    border-radius: 2px;
    padding: 0.45rem 1.1rem;
    transition: background-color 120ms ease, border-color 120ms ease;
}}
.stButton > button:hover {{
    border-color: {phosphor};
    color: {phosphor};
}}
.stButton > button[kind="primary"] {{
    background-color: {phosphor_dim};
    border-color: {phosphor};
    color: {bone};
}}
.stButton > button[kind="primary"]:hover {{
    background-color: {phosphor};
    color: {ink};
}}

/* Inputs. */
.stTextInput input, .stNumberInput input, .stTextArea textarea {{
    background-color: {surface} !important;
    color: {bone} !important;
    border: 1px solid {hairline} !important;
    font-family: 'IBM Plex Sans', sans-serif;
}}

/* Tabs — keep the lab-notebook flavor. */
.stTabs [data-baseweb="tab-list"] {{
    border-bottom: 1px solid {hairline};
    gap: 0.25rem;
}}
.stTabs [data-baseweb="tab"] {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: {bone_dim};
}}
.stTabs [aria-selected="true"] {{
    color: {bone};
    border-bottom: 2px solid {phosphor};
}}

/* Custom helper classes used by components. */
.hairline {{
    border: 0;
    border-top: 1px solid {hairline};
    margin: 1.5rem 0;
}}
.numeric {{
    font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace;
    font-feature-settings: 'tnum' 1;
    font-variant-numeric: tabular-nums;
    color: {bone};
}}
.callout {{
    background-color: {surface};
    border-left: 2px solid {phosphor};
    padding: 0.9rem 1.1rem;
    margin: 0.8rem 0;
    font-family: 'IBM Plex Sans', sans-serif;
    color: {bone};
}}
.callout em {{
    font-family: 'Fraunces', serif;
    font-style: italic;
    color: {phosphor};
    font-weight: 400;
}}
.dim {{
    color: {bone_dim};
}}
.faint {{
    color: {bone_faint};
}}
.kicker {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {phosphor};
    margin-bottom: 0.4rem;
}}

/* Tables — quiet, readable. */
[data-testid="stTable"] table, .stDataFrame table {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.92rem;
}}
[data-testid="stTable"] tbody td, .stDataFrame tbody td {{
    border-color: {hairline};
}}
</style>
"""


def apply_theme() -> None:
    """Inject fonts + global CSS overrides at the top of every page.

    Streamlit re-runs the whole script on every interaction, so the
    <style> tag is re-emitted each rerun. That's fine — Streamlit
    dedupes identical markdown blocks before sending them to the
    browser. No need to gate this behind session_state.
    """
    st.markdown(
        _CSS_TEMPLATE.format(font_import=_FONT_IMPORT, **PALETTE),
        unsafe_allow_html=True,
    )


def kicker(text: str) -> None:
    """Small mono uppercase label above a section title — like a magazine kicker."""
    st.markdown(f"<div class='kicker'>{text}</div>", unsafe_allow_html=True)


def hairline() -> None:
    """Thin horizontal rule with the project's hairline tone."""
    st.markdown("<hr class='hairline' />", unsafe_allow_html=True)


def callout(body_md: str) -> None:
    """A small phosphor-edged callout. `body_md` is rendered as HTML — keep
    the markup minimal: `<em>` is the only encouraged emphasis."""
    st.markdown(f"<div class='callout'>{body_md}</div>", unsafe_allow_html=True)


def dim(text: str) -> str:
    """Inline dim label — returns HTML for inclusion inside larger markdown."""
    return f"<span class='dim'>{text}</span>"


def numeric(text: str) -> str:
    """Inline tabular-figures span for use inside larger markdown blocks."""
    return f"<span class='numeric'>{text}</span>"
