"""XSS-escape guard for the Settings panel personality-lore card.

/review-2 CA1's Good Practices section confirmed that every user-
controlled string rendered into an ``unsafe_allow_html=True`` block in
the Settings page's personality-lore card is wrapped in
``html.escape(...)``. This test locks that audit in as a tripwire so
a future edit to the panel can't silently drop an escape.

Scope: narrow on purpose. Auditing every ``unsafe_allow_html=True``
call site across the codebase would produce too many false positives
(PALETTE tokens, hardcoded strings, integer interpolations are all
safe and don't need escaping). This guard targets the specific
attributes whose values come from the ``personality_lore`` table —
``theme``, ``description``, ``last_invoked_at_utc`` — which are
Daniel-authored but could in principle contain HTML special chars.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SETTINGS_PAGE = _PROJECT_ROOT / "app" / "pages" / "7_Settings.py"

# Personality-lore attributes that originate from a user-controlled
# settings row. Any time these appear inside an f-string template line
# in 7_Settings.py, the same template line MUST also include
# ``html.escape(...)`` for that attribute, OR the attribute must be
# inside a ``last_invoked_suffix(...)`` call (which composes plain
# stdlib text, not user-controlled).
_LORE_USER_CONTROLLED_ATTRS: tuple[str, ...] = (
    "_r.theme",
    "_r.description",
    "_r.last_invoked_at_utc",
)


def _interpolation_uses_safe_wrapper(line: str, attr: str) -> bool:
    """True when ``attr`` on this line is wrapped in html.escape or
    routed through a known-safe stdlib helper."""
    # html.escape(_r.<attr>) — the standard escape path.
    if re.search(rf"html\.escape\([^)]*{re.escape(attr)}", line):
        return True
    # last_invoked_suffix(_r.last_invoked_at_utc) — the helper renders
    # only stdlib date math + plain text ("(last invoked N days ago)"),
    # never user-controlled content.
    if re.search(
        rf"last_invoked_suffix\([^)]*{re.escape(attr)}", line
    ):
        return True
    return False


def test_personality_lore_panel_escapes_user_controlled_fields() -> None:
    """Every interpolation of a personality-lore user-controlled
    attribute inside an f-string template line in 7_Settings.py must
    pass through html.escape (or last_invoked_suffix for the timestamp
    helper case)."""
    text = _SETTINGS_PAGE.read_text(encoding="utf-8")
    offenders: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Only audit lines that look like f-string templates with
        # interpolation. {_r.attr} is the shape we care about.
        if "{" not in line or "}" not in line:
            continue
        for attr in _LORE_USER_CONTROLLED_ATTRS:
            # Match {_r.attr ...} (with optional .strftime or format spec).
            if not re.search(rf"\{{[^}}]*{re.escape(attr)}", line):
                continue
            if _interpolation_uses_safe_wrapper(line, attr):
                continue
            offenders.append((lineno, attr, line.strip()))
    assert not offenders, (
        "Unescaped user-controlled personality_lore attrs in 7_Settings.py:\n"
        + "\n".join(f"  line {n}: {a} — {ln!r}" for n, a, ln in offenders)
        + "\n\nWrap each in html.escape(...) before interpolation, or "
        "route through last_invoked_suffix(...) for the timestamp case. "
        "See /review-2 CA1 Pass-5 audit notes for context."
    )


def test_settings_page_imports_html_escape() -> None:
    """If someone refactors away the `import html` line, the audit
    above silently passes (no matches against html.escape()). Lock
    in the import."""
    text = _SETTINGS_PAGE.read_text(encoding="utf-8")
    assert re.search(r"^\s*import\s+html\b", text, flags=re.MULTILINE), (
        "app/pages/7_Settings.py must `import html` so html.escape is "
        "available for user-controlled-field rendering."
    )


@pytest.mark.parametrize("attr", _LORE_USER_CONTROLLED_ATTRS)
def test_attr_appears_in_panel(attr: str) -> None:
    """Sanity: each audited attribute is actually referenced in the
    Settings page. If a field is renamed or removed and this test
    starts firing on a non-existent attribute, prune the
    _LORE_USER_CONTROLLED_ATTRS list."""
    text = _SETTINGS_PAGE.read_text(encoding="utf-8")
    assert attr in text, (
        f"{attr} not found in 7_Settings.py — has the personality-lore "
        f"panel been refactored? Update _LORE_USER_CONTROLLED_ATTRS."
    )
