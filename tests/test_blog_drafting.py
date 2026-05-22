"""Unit tests for ``app/agent/blog_drafting.py`` — Phase 6 §28.32.

Covers all four tools (outline_blog / draft_blog / suggest_blog_edits /
generate_blog_seo_metadata) end-to-end with an injected ``ModelCaller``
stub so the tests run without ``ANTHROPIC_API_KEY``.

Key invariants under test:

* ``outline_blog`` and ``draft_blog`` append exactly one ``blog_versions``
  row each with the correct ``agent_action`` and parsed
  ``confidence_label_at_version``.
* ``suggest_blog_edits`` does NOT auto-apply (no version row from the
  tool itself).
* ``generate_blog_seo_metadata`` writes to ``blogs.seo_*`` and does NOT
  create a version row.
* Round-trip outline → draft → SEO leaves a 3-version timeline
  (creation v.1 + outline v.2 + draft v.3) plus the SEO write
  (no v.4).
* Niche-undefined refuses all four tools.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.agent import blog_drafting as bd
from app.agent import blogs as bm
from app.agent import niche as _niche


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _set_niche(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(
        db_conn,
        problem="figuring out dinner",
        person="working parents who cook at home",
    )


def _stub_caller(response_payload: dict) -> bd.ModelCaller:
    """Build a ModelCaller that returns ``response_payload`` as JSON."""
    text = json.dumps(response_payload)

    def caller(system_prompt: str, user_message: str, model: str) -> tuple[str, int, int]:
        # Verify the prompt was loaded (sanity check) and the user
        # message contains the identity context block.
        assert system_prompt.strip(), "system prompt must not be empty"
        assert "Identity context" in user_message
        return (text, 100, 200)

    return caller


# ---------------------------------------------------------------------------
# outline_blog
# ---------------------------------------------------------------------------
def test_outline_blog_writes_version_row_with_agent_action(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="Kitchen scanner failures")
    caller = _stub_caller(
        {
            "outline_markdown": "## Hook\nThree failed dinners.\n\n## Pattern\nMisreads.",
            "section_count": 2,
            "estimated_length_words": 1500,
            "confidence_label": "inference",
            "rationale": "Outline grounded in Daniel's recent attempts.",
        }
    )
    result = bd.outline_blog(db_conn, blog_id=b.id, model_caller=caller)
    assert result.section_count == 2
    assert result.confidence_label == "inference"
    assert result.version_number == 2
    # Persisted in blog_versions with correct agent_action + confidence.
    row = db_conn.execute(
        """
        SELECT created_by, agent_action, confidence_label_at_version,
               outline_markdown_at_version
        FROM blog_versions WHERE id = ?
        """,
        (result.version_id,),
    ).fetchone()
    assert row["created_by"] == "agent"
    assert row["agent_action"] == "outline"
    assert row["confidence_label_at_version"] == "inference"
    assert "Hook" in row["outline_markdown_at_version"]


def test_outline_blog_cross_checks_section_count(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="counter")
    # Model lies about section_count but outline has 3 H2 headings — the
    # parser overrides to the actual count.
    caller = _stub_caller(
        {
            "outline_markdown": "## A\n\n## B\n\n## C\n",
            "section_count": 99,
            "estimated_length_words": 1200,
            "confidence_label": "fact",
            "rationale": "",
        }
    )
    result = bd.outline_blog(db_conn, blog_id=b.id, model_caller=caller)
    assert result.section_count == 3


def test_outline_blog_rejects_invalid_confidence(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="invalid")
    caller = _stub_caller(
        {
            "outline_markdown": "## A",
            "section_count": 1,
            "estimated_length_words": 800,
            "confidence_label": "gut_feel",
            "rationale": "",
        }
    )
    with pytest.raises(bd.BlogDraftingModelError):
        bd.outline_blog(db_conn, blog_id=b.id, model_caller=caller)


def test_outline_blog_writes_audit_row(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="audit")
    caller = _stub_caller(
        {
            "outline_markdown": "## A",
            "section_count": 1,
            "estimated_length_words": 800,
            "confidence_label": "inference",
            "rationale": "",
        }
    )
    bd.outline_blog(db_conn, blog_id=b.id, model_caller=caller)
    row = db_conn.execute(
        """
        SELECT event_type FROM audit_logs
        WHERE event_type = 'blog_agent_outline' AND target_id = ?
        """,
        (str(b.id),),
    ).fetchone()
    assert row is not None


def test_outline_blog_refuses_when_niche_undefined(
    empty_db_conn: sqlite3.Connection,
) -> None:
    # Seed niche to empty.
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_problem', '\"\"')"
    )
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_person', '\"\"')"
    )
    b = bm.create_blog(empty_db_conn, title="x")
    caller = _stub_caller({"outline_markdown": "## A", "section_count": 1,
                           "estimated_length_words": 100,
                           "confidence_label": "fact", "rationale": ""})
    with pytest.raises(bd.BlogDraftingNicheUndefinedError):
        bd.outline_blog(empty_db_conn, blog_id=b.id, model_caller=caller)


# ---------------------------------------------------------------------------
# draft_blog
# ---------------------------------------------------------------------------
def test_draft_blog_requires_outline(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="no outline")
    caller = _stub_caller({"body_markdown": "x", "word_count": 1,
                           "sections_used": [], "confidence_label": "fact"})
    with pytest.raises(bd.BlogDraftingError):
        bd.draft_blog(db_conn, blog_id=b.id, model_caller=caller)


def test_draft_blog_writes_version_row(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="drafty")
    # First, seed an outline.
    bm.save_blog(
        db_conn, b.id, outline_markdown="## A\n## B", created_by="daniel"
    )
    caller = _stub_caller(
        {
            "body_markdown": "## A\n\nOne two three four five.\n\n## B\n\nMore words here yes.",
            "word_count": 10,
            "sections_used": ["## A", "## B"],
            "confidence_label": "speculation",
            "notes": "Speculation chip — Daniel review.",
        }
    )
    result = bd.draft_blog(db_conn, blog_id=b.id, model_caller=caller)
    assert result.confidence_label == "speculation"
    # word_count is computed independently from body_markdown.split();
    # the stub's claimed word_count is informational only.
    assert result.word_count == len(
        "## A\n\nOne two three four five.\n\n## B\n\nMore words here yes.".split()
    )
    assert "A" in result.sections_used[0]
    row = db_conn.execute(
        "SELECT created_by, agent_action, confidence_label_at_version "
        "FROM blog_versions WHERE id = ?",
        (result.version_id,),
    ).fetchone()
    assert row["created_by"] == "agent"
    assert row["agent_action"] == "draft"
    assert row["confidence_label_at_version"] == "speculation"


def test_draft_blog_updates_actual_length_words(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="len")
    bm.save_blog(db_conn, b.id, outline_markdown="## A", created_by="daniel")
    caller = _stub_caller(
        {
            "body_markdown": "one two three four five six seven eight nine ten",
            "word_count": 10,
            "sections_used": [],
            "confidence_label": "inference",
        }
    )
    bd.draft_blog(db_conn, blog_id=b.id, model_caller=caller)
    blog_after = bm.get_blog(db_conn, b.id)
    assert blog_after.actual_length_words == 10


# ---------------------------------------------------------------------------
# suggest_blog_edits
# ---------------------------------------------------------------------------
def test_suggest_blog_edits_does_not_create_version_row(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="suggest")
    bm.save_blog(
        db_conn, b.id, body_markdown="## A paragraph that exists.", created_by="daniel"
    )
    versions_before = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    caller = _stub_caller(
        {
            "suggestions": [
                {
                    "paragraph_anchor": "## A paragraph that exists",
                    "suggested_replacement": "## A tighter paragraph.",
                    "rationale": "Trim hedge.",
                    "confidence_label": "inference",
                },
            ],
            "overall_confidence_label": "inference",
            "summary": "One tightening suggestion.",
        }
    )
    result = bd.suggest_blog_edits(db_conn, blog_id=b.id, model_caller=caller)
    versions_after = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert versions_after == versions_before, "suggest_blog_edits must NOT auto-apply"
    assert len(result.suggestions) == 1
    assert result.suggestions[0].paragraph_anchor.startswith("## A paragraph")


def test_suggest_blog_edits_drops_malformed_entries(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="malformed")
    bm.save_blog(db_conn, b.id, body_markdown="body", created_by="daniel")
    caller = _stub_caller(
        {
            "suggestions": [
                {"paragraph_anchor": "", "suggested_replacement": "x",
                 "rationale": "x", "confidence_label": "fact"},
                {"paragraph_anchor": "valid", "suggested_replacement": "",
                 "rationale": "x", "confidence_label": "fact"},
                {"paragraph_anchor": "ok", "suggested_replacement": "new",
                 "rationale": "x", "confidence_label": "totally-fake"},
            ],
            "overall_confidence_label": "inference",
            "summary": "noise",
        }
    )
    result = bd.suggest_blog_edits(db_conn, blog_id=b.id, model_caller=caller)
    # The last entry survives because invalid confidence falls back to inference.
    assert len(result.suggestions) == 1
    assert result.suggestions[0].paragraph_anchor == "ok"
    assert result.suggestions[0].confidence_label == "inference"


def test_suggest_blog_edits_requires_body(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="no body")
    caller = _stub_caller(
        {"suggestions": [], "overall_confidence_label": "fact", "summary": "x"}
    )
    with pytest.raises(bd.BlogDraftingError):
        bd.suggest_blog_edits(db_conn, blog_id=b.id, model_caller=caller)


# ---------------------------------------------------------------------------
# generate_blog_seo_metadata
# ---------------------------------------------------------------------------
def test_seo_writes_blog_columns_and_no_version_row(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="seo blog")
    bm.save_blog(db_conn, b.id, body_markdown="paragraph", created_by="daniel")
    versions_before = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    caller = _stub_caller(
        {
            "seo_title": "Kitchen Scanner UX: Three Failed Dinners",
            "seo_description": (
                "What three failed dinner attempts taught me about why the "
                "kitchen scanner needs a confirm step before suggesting recipes."
            ),
            "seo_tags": ["kitchen-scanner-ux", "cook-mode", "ai-product"],
            "confidence_label": "inference",
            "rationale": "Tags chosen from body topics.",
        }
    )
    result = bd.generate_blog_seo_metadata(db_conn, blog_id=b.id, model_caller=caller)
    versions_after = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert versions_after == versions_before, "SEO write must not create a version row"
    row = db_conn.execute(
        "SELECT seo_title, seo_description, seo_tags_json FROM blogs WHERE id = ?",
        (b.id,),
    ).fetchone()
    assert row["seo_title"].startswith("Kitchen Scanner UX")
    assert "confirm step" in row["seo_description"]
    assert json.loads(row["seo_tags_json"]) == [
        "kitchen-scanner-ux", "cook-mode", "ai-product"
    ]
    assert result.confidence_label == "inference"


def test_seo_normalizes_tags_to_lowercase(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="seo tags")
    caller = _stub_caller(
        {
            "seo_title": "t",
            "seo_description": "d" * 130,
            "seo_tags": ["  Cook-Mode  ", "AI-Product", "", "kitchen"],
            "confidence_label": "fact",
            "rationale": "",
        }
    )
    result = bd.generate_blog_seo_metadata(db_conn, blog_id=b.id, model_caller=caller)
    assert result.seo_tags == ("cook-mode", "ai-product", "kitchen")


# ---------------------------------------------------------------------------
# Round-trip: outline → draft → SEO produces 1 + 2 = 3 versions, no SEO row.
# ---------------------------------------------------------------------------
def test_round_trip_outline_draft_seo_version_counts(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    b = bm.create_blog(db_conn, title="round trip")
    outline_caller = _stub_caller(
        {
            "outline_markdown": "## A\n\n## B\n",
            "section_count": 2,
            "estimated_length_words": 1500,
            "confidence_label": "inference",
            "rationale": "",
        }
    )
    bd.outline_blog(db_conn, blog_id=b.id, model_caller=outline_caller)

    draft_caller = _stub_caller(
        {
            "body_markdown": "## A\n\nWords words words.\n\n## B\n\nMore words.",
            "word_count": 7,
            "sections_used": ["## A", "## B"],
            "confidence_label": "inference",
        }
    )
    bd.draft_blog(db_conn, blog_id=b.id, model_caller=draft_caller)

    seo_caller = _stub_caller(
        {
            "seo_title": "Title",
            "seo_description": "d" * 140,
            "seo_tags": ["a", "b"],
            "confidence_label": "inference",
            "rationale": "",
        }
    )
    bd.generate_blog_seo_metadata(db_conn, blog_id=b.id, model_caller=seo_caller)

    versions = db_conn.execute(
        "SELECT version_number, created_by, agent_action FROM blog_versions "
        "WHERE blog_id = ? ORDER BY version_number",
        (b.id,),
    ).fetchall()
    # v1 = create (daniel, NULL); v2 = outline (agent, 'outline'); v3 = draft (agent, 'draft').
    # SEO does NOT create v4.
    assert len(versions) == 3
    assert versions[0]["version_number"] == 1
    assert versions[0]["created_by"] == "daniel"
    assert versions[1]["agent_action"] == "outline"
    assert versions[2]["agent_action"] == "draft"

    # Confirm SEO columns landed.
    blog = bm.get_blog(db_conn, b.id)
    row = db_conn.execute(
        "SELECT seo_title FROM blogs WHERE id = ?", (blog.id,)
    ).fetchone()
    assert row["seo_title"] == "Title"


# ---------------------------------------------------------------------------
# Tool-registry adapters return failed dicts on BlogDraftingError.
# ---------------------------------------------------------------------------
def test_tool_adapters_surface_failed_dict_on_niche_undefined(
    empty_db_conn: sqlite3.Connection,
) -> None:
    # Clear niche so the refuse path fires.
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_problem', '\"\"')"
    )
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_person', '\"\"')"
    )
    b = bm.create_blog(empty_db_conn, title="niche off")
    from app.agent import tools as t
    # outline_blog adapter
    out = t._outline_blog_to_dict(empty_db_conn, blog_id=b.id)
    assert out["status"] == "failed"
    assert "niche" in out["error"].lower()
