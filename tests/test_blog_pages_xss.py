"""P6R-2 — XSS escaping regression tests for the two Phase 6 view files.

The §14.14 Blogs index and §14.15 Blog Editor pages render multiple
user / agent-controlled fields via ``st.markdown(..., unsafe_allow_html=True)``.
Pre-P6R-2 those interpolations were raw; this module pins the
post-fix invariants by reading the page source and asserting:

1. Both page files import ``html.escape`` (aliased as ``_h``).
2. The five highest-risk interpolation sites are wrapped in ``_h(...)``:
   - blog title in the index list
   - linked-posts ``text_excerpt`` in the editor
   - suggestion ``anchor`` and ``rationale`` in the editor
   - identity-readout ``niche.person`` / ``niche.problem``
   - voice-profile ``self_description`` truncated
3. The string ``r['title']`` (and other raw interpolations) NEVER
   appears between an opening ``f"<`` and a closing ``>"`` without
   being wrapped in ``_h(...)`` — a heuristic source-level check
   against the regression vector.

Source-level checks are intentional. The Streamlit ``AppTest`` API
can't observe the HTML payload that ``unsafe_allow_html=True``
produces (it renders to delta-generator events, not raw HTML), so an
integration test would not actually prove the escape happened. The
source-level check is the testable contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BLOGS_INDEX = _REPO_ROOT / "app" / "pages" / "17_Blogs.py"
_BLOG_EDITOR = _REPO_ROOT / "app" / "pages" / "18_Blog_Editor.py"


@pytest.fixture(scope="module")
def blogs_index_source() -> str:
    return _BLOGS_INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def blog_editor_source() -> str:
    return _BLOG_EDITOR.read_text(encoding="utf-8")


def test_blogs_index_imports_html_escape(blogs_index_source: str) -> None:
    assert "from html import escape as _h" in blogs_index_source


def test_blog_editor_imports_html_escape(blog_editor_source: str) -> None:
    assert "from html import escape as _h" in blog_editor_source


# ---------------------------------------------------------------------------
# Specific interpolation sites that MUST be escaped.
# ---------------------------------------------------------------------------
def test_blogs_index_escapes_blog_title(blogs_index_source: str) -> None:
    """`r['title']` is the most-exposed field — agent-writable via
    repurpose_x_to_blog_idea AND Daniel-writable via the create form.
    The pre-fix code spliced it raw; assert it now goes through _h(...)."""
    assert "_h(r['title'])" in blogs_index_source
    # Belt-and-suspenders: the raw, unwrapped form must not appear in
    # an interpolation expression inside an HTML element body.
    assert "{r['title']}</div>" not in blogs_index_source


def test_blog_editor_escapes_niche_fields(blog_editor_source: str) -> None:
    assert "_h(nd.person)" in blog_editor_source
    assert "_h(nd.problem)" in blog_editor_source


def test_blog_editor_escapes_voice_profile_self_description(
    blog_editor_source: str,
) -> None:
    # truncated is a local var carrying the (possibly long) self-description.
    assert "_h(truncated)" in blog_editor_source


def test_blog_editor_escapes_suggestion_anchor_and_rationale(
    blog_editor_source: str,
) -> None:
    # Model-generated text from suggest_blog_edits — top XSS vector.
    assert "_h(sug['anchor'][:60])" in blog_editor_source
    assert "_h(sug['rationale'])" in blog_editor_source


def test_blog_editor_escapes_linked_post_text_excerpt(
    blog_editor_source: str,
) -> None:
    """posts.text can carry arbitrary saved-X content. The pre-fix
    code spliced `txt = (row["text"] or "")[:80]` raw into <i>...</i>."""
    assert 'txt = _h((row["text"] or "")[:80])' in blog_editor_source


def test_blog_editor_escapes_plagiarism_blocked_text_excerpt(
    blog_editor_source: str,
) -> None:
    # Agent-generated repurposed X output that triggered the plagiarism
    # guard — surfaced in the override banner. May contain HTML if the
    # model emitted it.
    assert "_h(item['text_excerpt'])" in blog_editor_source


def test_blog_editor_escapes_slug_in_header(blog_editor_source: str) -> None:
    """slug is _normalize_slug-sanitized so it's safe, but defensive
    escape protects against any future relaxation of the slug rule."""
    assert "_h(blog.slug)" in blog_editor_source


def test_blogs_index_escapes_lane_author_last_edited(
    blogs_index_source: str,
) -> None:
    """lane, author, and last_edited derive from pillar/audience/
    last_edited_by/last_edited_at_utc — schema-controlled enums or
    timestamps in practice, but defensively escape."""
    assert "_h(\" × \".join(lane_bits)" in blogs_index_source
    assert "_h(str(r.get(\"last_edited_by\")" in blogs_index_source
    assert "_h(str(r.get(\"last_edited_at_utc\")" in blogs_index_source


# ---------------------------------------------------------------------------
# Heuristic regression guard: no raw f-string interpolation of the
# most-risky fields inside HTML element bodies. If a future edit splices
# r['title'] / sug['anchor'] / sug['rationale'] / posts.text directly
# into a {...} interpolation in an unsafe_allow_html=True context, this
# trips.
# ---------------------------------------------------------------------------
_BANNED_RAW_INTERPOLATIONS = [
    # field -> file
    ("{r['title']}", _BLOGS_INDEX),
    ("{sug['anchor']", _BLOG_EDITOR),
    ("{sug['rationale']}", _BLOG_EDITOR),
]


@pytest.mark.parametrize("banned,path", _BANNED_RAW_INTERPOLATIONS)
def test_no_raw_interpolation_of_user_controlled_fields(
    banned: str, path: Path
) -> None:
    src = path.read_text(encoding="utf-8")
    # Allow the substring inside _h(...) — strip _h(...) calls first.
    import re as _re
    stripped = _re.sub(r"_h\([^)]*\)", "<<ESC>>", src)
    assert banned not in stripped, (
        f"raw (unescaped) interpolation of {banned!r} found in {path.name}; "
        "must be wrapped in _h(...) before splicing into "
        "st.markdown(..., unsafe_allow_html=True)"
    )
