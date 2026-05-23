"""Pure helpers extracted from Phase 6 Streamlit pages so they're
unit-testable without booting Streamlit's session-state machinery
(P6R-36b).

The page files (``app/pages/17_Blogs.py`` and
``app/pages/18_Blog_Editor.py``) call ``main()`` at import, so a test
that wants to exercise a callback's pure-logic kernel can't simply
``from app.pages.18_Blog_Editor import …``. This module lives outside
``app/pages/`` so a test can import it cleanly.

Each helper here is the input→output kernel of a callback. The
callbacks themselves still own the session-state plumbing (reading
inputs, writing error banners, dispatching to ``open_connection``);
this module owns the decision logic.
"""

from __future__ import annotations

import json


class SuggestionAnchorAmbiguous(ValueError):
    """Anchor matched more than one paragraph in the body."""


class SuggestionAnchorMissing(ValueError):
    """Anchor not found in the body."""


def apply_suggestion(body: str, anchor: str, replacement: str) -> str:
    """Apply ONE blog-edit suggestion to ``body``.

    P6R-9 + P6R-36b: extracted from ``_accept_suggestion_cb`` in
    18_Blog_Editor.py. Raises :class:`SuggestionAnchorMissing` when
    the anchor isn't in the body; :class:`SuggestionAnchorAmbiguous`
    when the anchor matches more than one paragraph (common when
    headings repeat across sections — silently rewriting the FIRST
    occurrence surprises the author). Otherwise returns the body
    with the unique anchor occurrence replaced.
    """
    occurrences = body.count(anchor)
    if occurrences == 0:
        raise SuggestionAnchorMissing(
            f"suggestion anchor not found in body: {anchor[:40]}…"
        )
    if occurrences > 1:
        raise SuggestionAnchorAmbiguous(
            f"suggestion anchor matches {occurrences} paragraphs in the body — "
            "ambiguous; rewrite the matching paragraph manually or re-prompt "
            "the agent for a more-specific anchor."
        )
    return body.replace(anchor, replacement, 1)


def parse_default_export_dir(raw_json: str | None) -> str:
    """Parse the ``blog_export_default_directory`` settings JSON value.

    P6R-20 + P6R-36b: extracted from ``_render_export_dialog``.
    ``raw_json`` is the raw ``value_json`` column content
    (already-JSON-encoded) or ``None``. Returns a usable directory
    string. Falls back to ``data/blog_exports/`` on:

    - missing row / ``None`` / empty string
    - JSON parse failure
    - non-string parsed value (lists, ints, dicts)
    - whitespace-only parsed string
    """
    fallback = "data/blog_exports/"
    if not raw_json:
        return fallback
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    if not isinstance(parsed, str):
        return fallback
    if not parsed.strip():
        return fallback
    return parsed
