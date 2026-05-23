"""Unit tests for ``app/agent/_blog_ui_helpers.py`` — Phase 6 P6R-36b.

These helpers are the pure-function kernels extracted from two
Streamlit callbacks in ``app/pages/18_Blog_Editor.py``:

- ``apply_suggestion`` — the duplicate-anchor decision behind
  ``_accept_suggestion_cb`` (P6R-9).
- ``parse_default_export_dir`` — the non-string settings fallback
  behind ``_render_export_dialog`` (P6R-20).

The page file calls ``main()`` at import time, so direct
``from app.pages.18_Blog_Editor import …`` doesn't work in a test
context (no Streamlit runtime). The helpers were extracted to keep
the decision logic testable.
"""

from __future__ import annotations

import json

import pytest

from app.agent._blog_ui_helpers import (
    SuggestionAnchorAmbiguous,
    SuggestionAnchorMissing,
    apply_suggestion,
    parse_default_export_dir,
)


# ---------------------------------------------------------------------------
# apply_suggestion (P6R-9)
# ---------------------------------------------------------------------------
def test_apply_suggestion_replaces_unique_anchor() -> None:
    body = "## Hook\n\nFirst paragraph.\n\n## Pattern\n\nSecond paragraph."
    result = apply_suggestion(
        body, anchor="First paragraph.", replacement="A tighter first sentence."
    )
    assert "A tighter first sentence." in result
    assert "First paragraph." not in result
    # Other paragraphs untouched.
    assert "Second paragraph." in result


def test_apply_suggestion_raises_on_missing_anchor() -> None:
    body = "## Hook\n\nFirst paragraph."
    with pytest.raises(SuggestionAnchorMissing):
        apply_suggestion(body, anchor="nonexistent text", replacement="x")


def test_apply_suggestion_raises_on_ambiguous_anchor() -> None:
    """The core P6R-9 invariant: an anchor matching multiple paragraphs
    must NOT silently rewrite the first; surface the ambiguity."""
    body = (
        "## The pattern\n\nThree failed dinners.\n\n"
        "## The pattern\n\nMisreads happen in low light.\n"
    )
    with pytest.raises(SuggestionAnchorAmbiguous) as ei:
        apply_suggestion(
            body, anchor="## The pattern",
            replacement="## Replacement heading",
        )
    # Error message names the count so Daniel sees "matches 2 paragraphs".
    assert "matches 2 paragraphs" in str(ei.value)


def test_apply_suggestion_anchor_count_match_at_three_plus() -> None:
    body = "X\n\nX\n\nX\n"  # three X paragraphs
    with pytest.raises(SuggestionAnchorAmbiguous) as ei:
        apply_suggestion(body, anchor="X", replacement="Y")
    assert "3" in str(ei.value)


def test_apply_suggestion_empty_body() -> None:
    with pytest.raises(SuggestionAnchorMissing):
        apply_suggestion("", anchor="anything", replacement="x")


# ---------------------------------------------------------------------------
# parse_default_export_dir (P6R-20)
# ---------------------------------------------------------------------------
def test_parse_default_export_dir_returns_string_value() -> None:
    assert parse_default_export_dir(json.dumps("/tmp/exports/")) == "/tmp/exports/"


def test_parse_default_export_dir_fallback_on_none() -> None:
    assert parse_default_export_dir(None) == "data/blog_exports/"


def test_parse_default_export_dir_fallback_on_empty_string() -> None:
    assert parse_default_export_dir("") == "data/blog_exports/"


def test_parse_default_export_dir_fallback_on_bad_json() -> None:
    assert parse_default_export_dir("this is not json {") == "data/blog_exports/"


def test_parse_default_export_dir_fallback_on_non_string_parse() -> None:
    """The core P6R-20 invariant: a list/int/dict parsed value must NOT
    crash the dialog (pre-fix it raised AttributeError on .rstrip)."""
    assert parse_default_export_dir(json.dumps([1, 2, 3])) == "data/blog_exports/"
    assert parse_default_export_dir(json.dumps(42)) == "data/blog_exports/"
    assert parse_default_export_dir(json.dumps({"a": "b"})) == "data/blog_exports/"
    assert parse_default_export_dir(json.dumps(None)) == "data/blog_exports/"
    assert parse_default_export_dir(json.dumps(True)) == "data/blog_exports/"


def test_parse_default_export_dir_fallback_on_whitespace_string() -> None:
    assert parse_default_export_dir(json.dumps("   ")) == "data/blog_exports/"


def test_parse_default_export_dir_accepts_relative_path() -> None:
    assert (
        parse_default_export_dir(json.dumps("my-blogs/exports/"))
        == "my-blogs/exports/"
    )
