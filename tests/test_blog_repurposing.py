"""Unit tests for ``app/agent/blog_repurposing.py`` — Phase 6 §28.34.

Covers both directions:

* ``repurpose_blog_to_x`` with all three modes; plagiarism guard
  fires on high-overlap synthetic input and blocks until override.
* ``repurpose_x_to_blog_idea`` creates a new blog (status='idea') +
  blog_to_post_links row + niche snapshots.
* ``finalize_blog_to_post_link`` is idempotent at ship time.

Tests run without ``ANTHROPIC_API_KEY`` by injecting a stub
``ModelCaller``.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.agent import blog_repurposing as br
from app.agent import blogs as bm
from app.agent import niche as _niche


def _set_niche(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(
        db_conn,
        problem="figuring out dinner",
        person="working parents who cook at home",
    )


def _stub_caller(response_payload: dict) -> br.ModelCaller:
    text = json.dumps(response_payload)

    def caller(system_prompt: str, user_message: str, model: str) -> tuple[str, int, int]:
        assert system_prompt.strip()
        assert "Identity context" in user_message
        return (text, 80, 160)

    return caller


def _seed_blog(db_conn: sqlite3.Connection, body: str | None = None) -> int:
    _set_niche(db_conn)
    blog = bm.create_blog(db_conn, title="repurpose blog", pillar="stir", audience="icp")
    bm.save_blog(
        db_conn,
        blog.id,
        body_markdown=body or (
            "# Why I'm building Stir\n\n"
            "Three failed dinner attempts taught me about the kitchen "
            "scanner.\n\n"
            "## The pattern\n\n"
            "Misreads happen when light is low.\n\n"
            "## The lesson\n\n"
            "Add a confirm step before suggesting recipes."
        ),
        created_by="daniel",
    )
    return blog.id


# ---------------------------------------------------------------------------
# repurpose_blog_to_x — thread mode.
# ---------------------------------------------------------------------------
def test_repurpose_blog_to_x_thread_inserts_one_draft_per_post(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    caller = _stub_caller({
        "posts": [
            {"text": "Hook: three failed dinners changed everything.",
             "section_anchor": "## Hook", "confidence_label": "inference"},
            {"text": "The pattern: misreads happen in low light.",
             "section_anchor": "## The pattern", "confidence_label": "inference"},
            {"text": "Lesson: add a confirm step before recipe suggestions.",
             "section_anchor": "## The lesson", "confidence_label": "fact"},
        ],
        "overall_confidence_label": "inference",
        "rationale": "Standard derivation.",
    })
    result = br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="thread_from_sections", model_caller=caller
    )
    assert len(result.drafts) == 3
    # Each draft has a row in agent_drafts.
    rows = db_conn.execute(
        "SELECT id, draft_kind, similarity_warning_json, text FROM agent_drafts "
        "ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["draft_kind"] == "thread_root"
        warning = json.loads(row["similarity_warning_json"])
        assert warning["kind"] == "blog_to_x_plagiarism"
        assert warning["source_blog_id"] == blog_id
        assert warning["mode"] == "thread_from_sections"


def test_repurpose_blog_to_x_single_mode_inserts_one_draft(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    caller = _stub_caller({
        "text": "Compression: a confirm step turns kitchen-scanner misreads into a tap.",
        "confidence_label": "inference",
        "rationale": "Single-line compression.",
    })
    result = br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="single_post_summary", model_caller=caller
    )
    assert len(result.drafts) == 1
    assert result.drafts[0].text.startswith("Compression:")


def test_repurpose_blog_to_x_teaser_mode(db_conn: sqlite3.Connection) -> None:
    blog_id = _seed_blog(db_conn)
    caller = _stub_caller({
        "text": "Three failed dinners. One UX lesson. <URL>",
        "confidence_label": "inference",
        "url_placeholder_used": True,
        "rationale": "Placeholder URL because blog isn't published yet.",
    })
    result = br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="teaser_with_link", model_caller=caller
    )
    assert len(result.drafts) == 1
    assert "<URL>" in result.drafts[0].text


# ---------------------------------------------------------------------------
# Plagiarism guard fires on high-overlap synthetic input.
# ---------------------------------------------------------------------------
def test_repurpose_blog_to_x_plagiarism_blocked_on_high_overlap(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    # Make the "post" near-identical to the blog body to drive high
    # Jaccard + long n-gram.
    blog = bm.get_blog(db_conn, blog_id)
    near_identical = blog.current_body_markdown
    caller = _stub_caller({
        "text": near_identical,
        "confidence_label": "inference",
        "rationale": "Bad output — too close to source.",
    })
    with pytest.raises(br.PlagiarismBlockedError) as exc:
        br.repurpose_blog_to_x(
            db_conn, blog_id=blog_id, mode="single_post_summary", model_caller=caller
        )
    assert len(exc.value.blocked_outputs) == 1
    # No draft row was written.
    count = db_conn.execute("SELECT COUNT(*) FROM agent_drafts").fetchone()[0]
    assert count == 0


def test_repurpose_blog_to_x_override_lands_draft(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    blog = bm.get_blog(db_conn, blog_id)
    near_identical = blog.current_body_markdown
    caller = _stub_caller({
        "text": near_identical,
        "confidence_label": "inference",
        "rationale": "Daniel accepts the high-risk output.",
    })
    result = br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="single_post_summary",
        override_plagiarism=True, model_caller=caller,
    )
    assert len(result.drafts) == 1
    assert result.drafts[0].plagiarism_override_used is True
    assert result.drafts[0].plagiarism_risk_label == "high"


def test_repurpose_blog_to_x_guard_disabled_setting_skips_block(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    blog = bm.get_blog(db_conn, blog_id)
    # Flip the setting to false — guard becomes inert.
    db_conn.execute(
        "UPDATE settings SET value_json = 'false' "
        "WHERE key = 'blog_repurposing_plagiarism_check_enabled'"
    )
    caller = _stub_caller({
        "text": blog.current_body_markdown,
        "confidence_label": "inference",
        "rationale": "Guard off; should still land.",
    })
    # No override needed when guard is disabled.
    result = br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="single_post_summary", model_caller=caller
    )
    assert len(result.drafts) == 1


# ---------------------------------------------------------------------------
# Linkage at ship time.
# ---------------------------------------------------------------------------
def test_finalize_blog_to_post_link_idempotent(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = _seed_blog(db_conn)
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'shipped derived', 'standalone', 'manual',
                'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    link_id_1 = br.finalize_blog_to_post_link(
        db_conn, blog_id=blog_id, post_id=post_id, mode="thread_from_sections"
    )
    link_id_2 = br.finalize_blog_to_post_link(
        db_conn, blog_id=blog_id, post_id=post_id, mode="thread_from_sections"
    )
    assert link_id_1 == link_id_2
    count = db_conn.execute(
        "SELECT COUNT(*) FROM blog_to_post_links "
        "WHERE blog_id = ? AND post_id = ? AND direction = 'blog_to_post'",
        (blog_id, post_id),
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# repurpose_x_to_blog_idea.
# ---------------------------------------------------------------------------
def test_repurpose_x_to_blog_idea_creates_blog_and_link(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    # Seed a source X post + classification.
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'one short observation', 'standalone', 'manual',
                'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO post_classifications (post_id, pillar, audience, cta)
        VALUES (?, 'stir', 'icp', 'ask')
        """,
        (post_id,),
    )
    caller = _stub_caller({
        "title": "Why the kitchen scanner needs a confirm step",
        "subtitle": "Three failed dinners later",
        "outline_markdown": "## Hook\n\n## Pattern\n\n## Lesson",
        "target_length_words": 1400,
        "pillar_recommendation": "stir",
        "audience_recommendation": "icp",
        "rationale": "Expansion grounded in three observed misreads.",
        "confidence_label": "inference",
    })
    result = br.repurpose_x_to_blog_idea(
        db_conn, post_id=post_id, model_caller=caller
    )
    # New blog exists with status='idea'.
    new_blog = bm.get_blog(db_conn, result.new_blog_id)
    assert new_blog.status == "idea"
    assert new_blog.title.startswith("Why the kitchen scanner")
    assert new_blog.pillar == "stir"
    assert new_blog.audience == "icp"
    # Outline got seeded.
    assert new_blog.outline_markdown and "Hook" in new_blog.outline_markdown
    # blog_to_post_links row exists with direction='post_to_blog'.
    row = db_conn.execute(
        """
        SELECT direction, relationship_kind FROM blog_to_post_links WHERE id = ?
        """,
        (result.blog_to_post_link_id,),
    ).fetchone()
    assert row["direction"] == "post_to_blog"
    assert row["relationship_kind"] == "derived_outline"
    # Niche snapshots populated.
    snap = db_conn.execute(
        "SELECT niche_problem_snapshot, niche_person_snapshot FROM blogs WHERE id = ?",
        (result.new_blog_id,),
    ).fetchone()
    assert snap["niche_problem_snapshot"] == "figuring out dinner"
    assert "working parents" in snap["niche_person_snapshot"]


def test_repurpose_x_to_blog_idea_rejects_empty_post(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', '', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    caller = _stub_caller({"title": "x", "outline_markdown": "## a",
                           "target_length_words": 1000,
                           "confidence_label": "inference"})
    with pytest.raises(br.BlogRepurposingError):
        br.repurpose_x_to_blog_idea(db_conn, post_id=post_id, model_caller=caller)


def test_repurpose_x_to_blog_idea_unknown_post(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    caller = _stub_caller({"title": "x"})
    with pytest.raises(br.BlogRepurposingError):
        br.repurpose_x_to_blog_idea(db_conn, post_id=999_999, model_caller=caller)


# ---------------------------------------------------------------------------
# Niche-undefined refusal — both directions.
# ---------------------------------------------------------------------------
def test_both_tools_refuse_when_niche_undefined(
    empty_db_conn: sqlite3.Connection,
) -> None:
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_problem', '\"\"')"
    )
    empty_db_conn.execute(
        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('niche_person', '\"\"')"
    )
    blog_id = bm.create_blog(empty_db_conn, title="no niche").id
    caller = _stub_caller({"posts": []})
    with pytest.raises(br.BlogRepurposingNicheUndefinedError):
        br.repurpose_blog_to_x(
            empty_db_conn, blog_id=blog_id, mode="thread_from_sections",
            model_caller=caller,
        )
    post_id = empty_db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'hi', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    with pytest.raises(br.BlogRepurposingNicheUndefinedError):
        br.repurpose_x_to_blog_idea(empty_db_conn, post_id=post_id, model_caller=caller)


# ---------------------------------------------------------------------------
# Unknown mode.
# ---------------------------------------------------------------------------
def test_repurpose_blog_to_x_unknown_mode(db_conn: sqlite3.Connection) -> None:
    blog_id = _seed_blog(db_conn)
    caller = _stub_caller({"text": "x", "confidence_label": "fact"})
    with pytest.raises(br.BlogRepurposingError):
        br.repurpose_blog_to_x(
            db_conn, blog_id=blog_id, mode="totally-fake",  # type: ignore[arg-type]
            model_caller=caller,
        )


# ---------------------------------------------------------------------------
# Audit log integration.
# ---------------------------------------------------------------------------
def test_repurpose_blog_to_x_writes_audit_row(db_conn: sqlite3.Connection) -> None:
    blog_id = _seed_blog(db_conn)
    caller = _stub_caller({
        "text": "Quick compression.",
        "confidence_label": "inference",
        "rationale": "",
    })
    br.repurpose_blog_to_x(
        db_conn, blog_id=blog_id, mode="single_post_summary", model_caller=caller
    )
    row = db_conn.execute(
        """
        SELECT details_json FROM audit_logs
        WHERE event_type = 'blog_repurpose_to_x' AND target_id = ?
        """,
        (str(blog_id),),
    ).fetchone()
    assert row is not None
    details = json.loads(row["details_json"])
    assert details["mode"] == "single_post_summary"
    assert details["draft_count"] == 1


def test_repurpose_x_to_blog_idea_writes_audit_row(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'src post', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    caller = _stub_caller({
        "title": "t",
        "outline_markdown": "## A",
        "target_length_words": 1200,
        "confidence_label": "inference",
        "rationale": "",
    })
    result = br.repurpose_x_to_blog_idea(
        db_conn, post_id=post_id, model_caller=caller
    )
    row = db_conn.execute(
        """
        SELECT details_json FROM audit_logs
        WHERE event_type = 'post_repurposed_to_blog_idea' AND target_id = ?
        """,
        (str(result.new_blog_id),),
    ).fetchone()
    assert row is not None
