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


def tool_call_block(
    tool_name: str,
    *,
    summary: str,
    body_md: str | None = None,
    status: str = "success",
    expanded: bool = False,
) -> None:
    """Console-log row for a single agent tool call.

    The Phase 5.5 chat view renders one of these per tool invocation. The
    collapsed form is one line: `[query_dashboard_state] · slice=today · 6
    rows`. Expanded form shows ``body_md`` (typically a fenced JSON code
    block of the args + result).

    ``status='error'`` shifts the left-keyline to the directional amber so
    failed calls are visible without scanning.
    """
    accent = (
        PALETTE["confidence_directional_bg"]
        if status == "error"
        else PALETTE["phosphor"]
    )
    label_color = (
        PALETTE["confidence_directional_bg"]
        if status == "error"
        else PALETTE["phosphor"]
    )
    header_html = (
        f"<span style='font-family: JetBrains Mono, monospace; "
        f"font-size: 0.78rem; letter-spacing: 0.06em; "
        f"text-transform: uppercase; color: {label_color};'>"
        f"[{tool_name}]</span> "
        f"<span class='dim' style='font-size: 0.85rem;'>· {summary}</span>"
    )
    with st.expander(label="", expanded=expanded):
        st.markdown(
            f"<div style='border-left: 2px solid {accent}; padding: 0.15rem 0.7rem; "
            f"margin: 0 0 0.4rem -0.1rem;'>{header_html}</div>",
            unsafe_allow_html=True,
        )
        if body_md:
            st.markdown(body_md)


def iwh_meter(intelligence: int, wisdom: int, humility: int) -> None:
    """Three discrete segments colored by IWH self-score (0..3 each).

    Rendered VU-meter-flavored but with stepped segments, not an analog
    needle — matches the rest of the dashboard's "show steps, not slopes"
    discipline (same reason the confidence labels are 4 discrete bands).
    """
    step_colors = [
        PALETTE["bone_faint"],
        PALETTE["bone_dim"],
        PALETTE["phosphor_dim"],
        PALETTE["phosphor"],
    ]

    def _segment(label: str, value: int) -> str:
        color = step_colors[max(0, min(3, value))]
        return (
            f"<div style='display: inline-block; width: 28%; padding: 0.3rem 0; "
            f"text-align: center; background: {PALETTE['surface']}; "
            f"border-top: 2px solid {color}; margin-right: 1.5%;'>"
            f"<div style='font-family: JetBrains Mono, monospace; font-size: 0.7rem; "
            f"letter-spacing: 0.08em; color: {PALETTE['bone_faint']};'>{label}</div>"
            f"<div class='numeric' style='font-size: 1.1rem; color: {color};'>"
            f"{value}</div></div>"
        )

    st.markdown(
        f"<div style='margin: 0.3rem 0 0.6rem 0;'>"
        f"{_segment('I', intelligence)}"
        f"{_segment('W', wisdom)}"
        f"{_segment('H', humility)}"
        f"</div>",
        unsafe_allow_html=True,
    )


def cost_meter(mtd_usd: float, cap_usd: float) -> None:
    """Horizontal cost strip — phosphor → amber at 80% → exceeded at 100%."""
    pct = (mtd_usd / cap_usd) if cap_usd > 0 else 0.0
    pct_clamped = min(1.0, max(0.0, pct))
    if pct >= 1.0:
        fill_color = PALETTE["confidence_directional_bg"]
        text_color = PALETTE["confidence_directional_bg"]
        suffix = " — CAP REACHED"
    elif pct >= 0.80:
        fill_color = PALETTE["confidence_directional_bg"]
        text_color = PALETTE["bone"]
        suffix = ""
    else:
        fill_color = PALETTE["phosphor_dim"]
        text_color = PALETTE["bone"]
        suffix = ""

    bar_html = (
        f"<div style='background: {PALETTE['surface']}; border: 1px solid {PALETTE['hairline']}; "
        f"height: 0.55rem; border-radius: 1px; margin: 0.2rem 0 0.35rem 0;'>"
        f"<div style='background: {fill_color}; width: {pct_clamped * 100:.1f}%; "
        f"height: 100%;'></div></div>"
    )
    label_html = (
        f"<div class='numeric' style='font-family: JetBrains Mono, monospace; "
        f"font-size: 0.85rem; color: {text_color};'>"
        f"${mtd_usd:0.2f} / ${cap_usd:0.2f}"
        f"  <span class='faint' style='font-size: 0.75rem;'>"
        f"({pct * 100:0.0f}%){suffix}</span></div>"
    )
    st.markdown(bar_html + label_html, unsafe_allow_html=True)


def token_ttl_countdown(seconds_remaining: int) -> None:
    """Large MM:SS readout — the single animated element in the whole app.

    Caller is responsible for the ~1Hz rerun. The countdown is purely a
    formatter — no internal timer. Color: phosphor while >10s, amber 5-10s,
    bone_faint when ≤5s.
    """
    s = max(0, int(seconds_remaining))
    if s > 10:
        color = PALETTE["phosphor"]
    elif s > 5:
        color = PALETTE["confidence_directional_bg"]
    else:
        color = PALETTE["bone_faint"]
    mm, ss = divmod(s, 60)
    st.markdown(
        f"<div class='kicker'>TOKEN EXPIRES IN</div>"
        f"<div class='numeric' style='font-family: JetBrains Mono, monospace; "
        f"font-size: 2.6rem; letter-spacing: -0.02em; color: {color}; "
        f"line-height: 1;'>"
        f"{mm:02d}:{ss:02d}</div>"
        f"<div class='faint' style='font-size: 0.75rem; margin-top: 0.4rem;'>"
        f"Tokens are single-use, sha256-hashed server-side. Expiry voids the click.</div>",
        unsafe_allow_html=True,
    )


def console_log_row(
    *,
    timestamp: str,
    kind: str,
    title: str,
    active: bool = False,
) -> None:
    """Compact one-line row used by the Agent Chat sessions sidebar."""
    border_color = PALETTE["phosphor"] if active else PALETTE["hairline"]
    title_color = PALETTE["bone"] if active else PALETTE["bone_dim"]
    st.markdown(
        f"<div style='border-left: 2px solid {border_color}; padding: 0.2rem 0.6rem; "
        f"margin: 0.15rem 0;'>"
        f"<span class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']};'>"
        f"{timestamp}</span> "
        f"<span style='font-family: JetBrains Mono, monospace; font-size: 0.7rem; "
        f"letter-spacing: 0.08em; text-transform: uppercase; color: {PALETTE['phosphor']};'>"
        f"· {kind}</span>"
        f"<div style='font-size: 0.85rem; color: {title_color}; "
        f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;'>{title}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def readout_card(
    label: str,
    value: str,
    caption: str | None = None,
    accent: str = "phosphor",
    empty: bool = False,
) -> None:
    """Render an instrument-panel "readout" card.

    The shape: a 2px left-keyline (solid when populated, dashed when
    ``empty=True``), a small ALL-CAPS label, a big mono value, and an
    optional caption beneath. Used by the Backups subsection in
    Settings; intentionally generic so future sub-panels (exports,
    agent, etc.) can reuse the same surface.

    Parameters
    ----------
    label
        Short ALL-CAPS-styled label rendered above the value. Plain text.
    value
        The headline value — rendered in JetBrains Mono at 1.25rem. Plain
        text. For numeric values, the caller should already have formatted
        them (this helper does not coerce).
    caption
        Optional secondary line beneath the value (e.g. "5m ago" or
        "(unparseable timestamp)"). Falsy values render no caption line.
    accent
        PALETTE key for the left keyline color. Defaults to ``"phosphor"``.
        Pass another existing key (e.g. ``"bone_dim"``) — do NOT pass a
        raw hex literal; new color tokens belong in PALETTE.
    empty
        Renders the dashed-border + dimmed-value variant for "no data
        yet" states.
    """
    if accent not in PALETTE:
        raise ValueError(
            f"Unknown PALETTE accent {accent!r}. Add new color tokens to "
            f"PALETTE in app/components/theme.py — do not pass raw hex."
        )

    if empty:
        border_style = "dashed"
        border_color = PALETTE["hairline"]
        value_color = PALETTE["bone_dim"]
        caption_color = PALETTE["bone_faint"]
    else:
        border_style = "solid"
        border_color = PALETTE[accent]
        value_color = PALETTE["bone"]
        caption_color = PALETTE["bone_dim"]

    caption_html = (
        f"""<div class='faint' style='font-size:0.78rem; color:{caption_color};
                                       margin-top:0.1rem;'>{caption}</div>"""
        if caption
        else ""
    )

    st.markdown(
        f"""<div style='padding:0.6rem 0.9rem; margin:0.4rem 0 0.8rem 0;
                       background:{PALETTE['surface']};
                       border-left:2px {border_style} {border_color};
                       border-radius:2px;'>
            <div class='faint' style='font-size:0.72rem; letter-spacing:0.08em;
                                       text-transform:uppercase; color:{PALETTE['bone_faint']};'>
                {label}
            </div>
            <div class='numeric' style='font-size:1.25rem; color:{value_color};
                                          margin-top:0.15rem;'>
                {value}
            </div>
            {caption_html}
        </div>""",
        unsafe_allow_html=True,
    )
