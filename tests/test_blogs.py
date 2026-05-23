"""Unit tests for ``app/agent/blogs.py`` — Phase 6 §28.31.

Covers:

* ``create_blog`` (slug derivation + disambiguation + version 1 anchor).
* ``save_blog`` (no-op detection, version append, current-pointer flip).
* ``transition_status`` (state machine — every legal edge, every illegal
  edge, the ``external_url`` precondition on ``published_externally``).
* ``revert_to_version`` (forward-moving history, prior row's
  ``is_current_for_blog`` unchanged).
* ``set_seo_metadata`` (sidecar — no version row appended).
"""

from __future__ import annotations

import sqlite3

import pytest

from app.agent import blogs as bm


# ---------------------------------------------------------------------------
# create_blog
# ---------------------------------------------------------------------------
def test_create_blog_returns_idea_status_with_version_one(
    db_conn: sqlite3.Connection,
) -> None:
    blog = bm.create_blog(db_conn, title="Hello Kitchen Scanner")
    assert blog.status == "idea"
    assert blog.title == "Hello Kitchen Scanner"
    assert blog.actual_length_words == 0
    assert blog.slug == "hello-kitchen-scanner"
    row = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ? AND is_current_for_blog = 1",
        (blog.id,),
    ).fetchone()[0]
    assert row == 1


def test_create_blog_disambiguates_duplicate_slug(db_conn: sqlite3.Connection) -> None:
    a = bm.create_blog(db_conn, title="Same Title")
    b = bm.create_blog(db_conn, title="Same Title")
    c = bm.create_blog(db_conn, title="Same Title")
    assert a.slug == "same-title"
    assert b.slug == "same-title-2"
    assert c.slug == "same-title-3"


def test_create_blog_normalizes_unicode_and_punctuation(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="  Why I'm Building Stir — first principles!  ")
    # Apostrophes / em-dashes / exclamation collapse to hyphens; collapsing
    # runs are squashed.
    assert b.slug == "why-i-m-building-stir-first-principles"


def test_create_blog_rejects_empty_title(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.create_blog(db_conn, title="   ")


def test_create_blog_rejects_nonpositive_target_length(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.create_blog(db_conn, title="t", target_length_words=0)


def test_create_blog_logs_audit_row(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="audit me")
    row = db_conn.execute(
        """
        SELECT event_category, event_type, target_type, target_id
        FROM audit_logs
        WHERE event_type = 'blog_created' AND target_id = ?
        """,
        (str(b.id),),
    ).fetchone()
    assert row is not None
    assert row["event_category"] == "data"
    assert row["target_type"] == "blog"


# ---------------------------------------------------------------------------
# save_blog
# ---------------------------------------------------------------------------
def test_save_blog_appends_version_on_body_change(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="body change")
    v2 = bm.save_blog(
        db_conn, b.id, body_markdown="hello world", created_by="daniel"
    )
    assert v2 is not None
    assert v2.version_number == 2
    assert v2.is_current_for_blog is True
    assert v2.body_markdown == "hello world"
    # Prior version demoted.
    prior = db_conn.execute(
        "SELECT is_current_for_blog FROM blog_versions "
        "WHERE blog_id = ? AND version_number = 1",
        (b.id,),
    ).fetchone()[0]
    assert prior == 0


def test_save_blog_no_op_returns_none_and_no_new_row(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="noop")
    # First save lands a real change.
    v2 = bm.save_blog(db_conn, b.id, body_markdown="content", created_by="daniel")
    assert v2 is not None and v2.version_number == 2
    # Second save with EVERY field equal to current state — no-op.
    result = bm.save_blog(
        db_conn, b.id, body_markdown="content", outline_markdown=None,
        title="noop", status="idea", created_by="daniel",
    )
    assert result is None
    max_v = db_conn.execute(
        "SELECT MAX(version_number) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert max_v == 2


def test_save_blog_updates_actual_length_words(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="length")
    bm.save_blog(
        db_conn, b.id, body_markdown="one two three four five", created_by="daniel"
    )
    blog = bm.get_blog(db_conn, b.id)
    assert blog.actual_length_words == 5


def test_save_blog_records_agent_action_and_confidence(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="agent")
    v = bm.save_blog(
        db_conn,
        b.id,
        body_markdown="agent draft",
        created_by="agent",
        agent_action="draft",
        confidence_label_at_version="inference",
    )
    assert v is not None
    assert v.created_by == "agent"
    assert v.agent_action == "draft"
    assert v.confidence_label_at_version == "inference"
    blog = bm.get_blog(db_conn, b.id)
    assert blog.agent_assisted is True


def test_save_blog_rejects_unknown_agent_action(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="bad action")
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.save_blog(
            db_conn,
            b.id,
            body_markdown="x",
            created_by="agent",
            agent_action="totally-made-up",
        )


def test_save_blog_partial_unique_current_invariant(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="invariant")
    bm.save_blog(db_conn, b.id, body_markdown="a", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="b", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="c", created_by="daniel")
    current_count = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions "
        "WHERE blog_id = ? AND is_current_for_blog = 1",
        (b.id,),
    ).fetchone()[0]
    assert current_count == 1


# ---------------------------------------------------------------------------
# transition_status
# ---------------------------------------------------------------------------
def test_transition_status_legal_edge_writes_version(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="legal")
    v = bm.transition_status(db_conn, b.id, "outlining")
    assert v.status_at_version == "outlining"
    blog = bm.get_blog(db_conn, b.id)
    assert blog.status == "outlining"


def test_transition_status_illegal_edge_raises(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="illegal")
    # idea → published_externally is two-hops illegal.
    with pytest.raises(bm.InvalidStatusTransitionError):
        bm.transition_status(db_conn, b.id, "published_externally")
    # status unchanged.
    blog = bm.get_blog(db_conn, b.id)
    assert blog.status == "idea"


def test_transition_status_unknown_status_raises(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="unk")
    with pytest.raises(bm.InvalidStatusTransitionError):
        bm.transition_status(db_conn, b.id, "totally-fake-status")


def test_transition_status_identity_is_rejected(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="identity")
    # idea → idea isn't a real state change.
    with pytest.raises(bm.InvalidStatusTransitionError):
        bm.transition_status(db_conn, b.id, "idea")


def test_transition_status_full_happy_path(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="happy")
    bm.transition_status(db_conn, b.id, "outlining")
    bm.transition_status(db_conn, b.id, "drafting")
    bm.transition_status(db_conn, b.id, "editing")
    bm.transition_status(db_conn, b.id, "ready")
    bm.transition_status(db_conn, b.id, "exported")
    bm.transition_status(
        db_conn, b.id, "published_externally", external_url="https://example.com/x"
    )
    blog = bm.get_blog(db_conn, b.id)
    assert blog.status == "published_externally"
    ext = db_conn.execute(
        "SELECT external_url, external_published_at FROM blogs WHERE id = ?",
        (b.id,),
    ).fetchone()
    assert ext["external_url"] == "https://example.com/x"
    assert ext["external_published_at"] is not None


def test_transition_status_published_externally_requires_external_url(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="pub")
    for s in ("outlining", "drafting", "editing", "ready", "exported"):
        bm.transition_status(db_conn, b.id, s)
    with pytest.raises(bm.InvalidBlogFieldError):
        # Missing external_url AND blog row has none populated.
        bm.transition_status(db_conn, b.id, "published_externally")


def test_transition_status_backward_edges_legal(db_conn: sqlite3.Connection) -> None:
    """drafting → outlining and editing → drafting are legal §28.31 edges."""
    b = bm.create_blog(db_conn, title="back")
    bm.transition_status(db_conn, b.id, "outlining")
    bm.transition_status(db_conn, b.id, "drafting")
    bm.transition_status(db_conn, b.id, "outlining")  # legal
    bm.transition_status(db_conn, b.id, "drafting")
    bm.transition_status(db_conn, b.id, "editing")
    bm.transition_status(db_conn, b.id, "drafting")  # legal
    blog = bm.get_blog(db_conn, b.id)
    assert blog.status == "drafting"


def test_transition_status_archived_is_terminal(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="term")
    bm.transition_status(db_conn, b.id, "archived")
    for s in ("idea", "outlining", "drafting", "editing", "ready", "exported",
              "published_externally"):
        with pytest.raises(bm.InvalidStatusTransitionError):
            bm.transition_status(db_conn, b.id, s)


def test_transition_status_atomicity_under_audit_failure(
    db_conn: sqlite3.Connection,
) -> None:
    """P6R-1: if audit_log.log raises mid-transition, the version row +
    status change must NOT persist. Pre-fix this would have left an
    orphan version row with the new status_at_version while no audit row
    landed."""
    from unittest.mock import patch
    b = bm.create_blog(db_conn, title="atomic")
    versions_before = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    status_before = bm.get_blog(db_conn, b.id).status

    with patch(
        "app.agent.blogs._audit_log.log",
        side_effect=sqlite3.OperationalError("simulated audit failure"),
    ):
        with pytest.raises(sqlite3.OperationalError):
            bm.transition_status(db_conn, b.id, "outlining")

    # No new version row landed.
    versions_after = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert versions_after == versions_before, (
        "audit failure must roll back the version row too"
    )
    # blogs.status did not move.
    assert bm.get_blog(db_conn, b.id).status == status_before


def test_revert_to_version_atomicity_under_audit_failure(
    db_conn: sqlite3.Connection,
) -> None:
    """P6R-1: same invariant for revert_to_version — if audit fails,
    the new version row + demote of the prior current must roll back."""
    from unittest.mock import patch
    b = bm.create_blog(db_conn, title="revert atomic")
    v2 = bm.save_blog(db_conn, b.id, body_markdown="alpha", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="beta", created_by="daniel")
    assert v2 is not None

    versions_before = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    current_id_before = db_conn.execute(
        "SELECT id FROM blog_versions "
        "WHERE blog_id = ? AND is_current_for_blog = 1",
        (b.id,),
    ).fetchone()[0]

    with patch(
        "app.agent.blogs._audit_log.log",
        side_effect=sqlite3.OperationalError("simulated audit failure"),
    ):
        with pytest.raises(sqlite3.OperationalError):
            bm.revert_to_version(db_conn, b.id, v2.id)

    versions_after = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert versions_after == versions_before
    current_id_after = db_conn.execute(
        "SELECT id FROM blog_versions "
        "WHERE blog_id = ? AND is_current_for_blog = 1",
        (b.id,),
    ).fetchone()[0]
    assert current_id_after == current_id_before, (
        "demote-of-prior-current must also roll back on audit failure"
    )


def test_transition_status_rejects_non_http_external_url(
    db_conn: sqlite3.Connection,
) -> None:
    """P6R-7: external_url must be http/https. javascript:/data:/file:
    refused with InvalidBlogFieldError."""
    b = bm.create_blog(db_conn, title="scheme")
    for s in ("outlining", "drafting", "editing", "ready", "exported"):
        bm.transition_status(db_conn, b.id, s)
    for bad in ("javascript:alert(1)", "data:text/html,<h1>x</h1>",
                "file:///etc/passwd", "vbscript:msgbox"):
        with pytest.raises(bm.InvalidBlogFieldError):
            bm.transition_status(
                db_conn, b.id, "published_externally", external_url=bad
            )
    # http(s) still works.
    bm.transition_status(
        db_conn, b.id, "published_externally",
        external_url="https://example.com/published",
    )


def test_transition_status_audit_row_records_edge(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="audit edge")
    bm.transition_status(db_conn, b.id, "outlining")
    row = db_conn.execute(
        """
        SELECT event_category, event_type
        FROM audit_logs
        WHERE event_type = 'blog_status_idea_to_outlining'
          AND target_id = ?
        """,
        (str(b.id),),
    ).fetchone()
    assert row is not None
    assert row["event_category"] == "data"


# ---------------------------------------------------------------------------
# revert_to_version
# ---------------------------------------------------------------------------
def test_revert_creates_forward_moving_history(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="revert")
    v2 = bm.save_blog(db_conn, b.id, body_markdown="alpha", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="beta", created_by="daniel")
    v4 = bm.save_blog(db_conn, b.id, body_markdown="gamma", created_by="daniel")
    assert v4 is not None and v4.version_number == 4
    # Revert to v2 (body='alpha').
    new = bm.revert_to_version(
        db_conn, b.id, v2.id, daniel_revision_note="reverting because gamma was wrong"
    )
    assert new.version_number == 5
    assert new.body_markdown == "alpha"
    assert new.is_current_for_blog is True
    assert "reverted to v2" in (new.daniel_revision_note or "")
    # v2.is_current_for_blog stays False.
    v2_after = db_conn.execute(
        "SELECT is_current_for_blog FROM blog_versions WHERE id = ?", (v2.id,)
    ).fetchone()[0]
    assert v2_after == 0
    # v4 still has its content unchanged.
    v4_after = db_conn.execute(
        "SELECT body_markdown FROM blog_versions WHERE id = ?", (v4.id,)
    ).fetchone()[0]
    assert v4_after == "gamma"


def test_revert_to_current_is_rejected(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="revert cur")
    v2 = bm.save_blog(db_conn, b.id, body_markdown="content", created_by="daniel")
    assert v2 is not None
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.revert_to_version(db_conn, b.id, v2.id)


def test_revert_to_identical_content_raises(db_conn: sqlite3.Connection) -> None:
    """If reverting would produce a no-op (content same as current),
    surface it instead of silently doing nothing."""
    b = bm.create_blog(db_conn, title="revert id")
    v2 = bm.save_blog(db_conn, b.id, body_markdown="alpha", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="beta", created_by="daniel")
    # First revert: v4 is now the current row, carrying v2's body ('alpha').
    bm.revert_to_version(db_conn, b.id, v2.id, daniel_revision_note="back to alpha")
    # Second revert to v2 would produce another row also carrying 'alpha'
    # — but save_blog's no-op detection catches body+outline+title+status
    # all unchanged. Surface the diagnostic error.
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.revert_to_version(db_conn, b.id, v2.id)


def test_revert_unknown_version_raises(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="revert unk")
    with pytest.raises(bm.BlogVersionNotFoundError):
        bm.revert_to_version(db_conn, b.id, version_id=999_999)


def test_revert_audit_row_records_versions(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="revert audit")
    v2 = bm.save_blog(db_conn, b.id, body_markdown="alpha", created_by="daniel")
    bm.save_blog(db_conn, b.id, body_markdown="beta", created_by="daniel")
    bm.revert_to_version(db_conn, b.id, v2.id, daniel_revision_note="back")
    row = db_conn.execute(
        """
        SELECT details_json
        FROM audit_logs
        WHERE event_type = 'blog_reverted' AND target_id = ?
        """,
        (str(b.id),),
    ).fetchone()
    assert row is not None
    import json
    details = json.loads(row["details_json"])
    assert details["to_target_version_number"] == 2


# ---------------------------------------------------------------------------
# set_seo_metadata
# ---------------------------------------------------------------------------
def test_set_seo_metadata_does_not_create_version_row(
    db_conn: sqlite3.Connection,
) -> None:
    b = bm.create_blog(db_conn, title="seo")
    before = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    bm.set_seo_metadata(
        db_conn, b.id,
        seo_title="Title", seo_description="Desc",
        seo_tags=["alpha", "beta"],
    )
    after = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (b.id,)
    ).fetchone()[0]
    assert before == after, "SEO write must not create a blog_versions row"
    row = db_conn.execute(
        "SELECT seo_title, seo_description, seo_tags_json FROM blogs WHERE id = ?",
        (b.id,),
    ).fetchone()
    assert row["seo_title"] == "Title"
    assert row["seo_description"] == "Desc"
    import json
    assert json.loads(row["seo_tags_json"]) == ["alpha", "beta"]


def test_set_seo_metadata_audit_row(db_conn: sqlite3.Connection) -> None:
    b = bm.create_blog(db_conn, title="seo audit")
    bm.set_seo_metadata(
        db_conn, b.id, seo_title="t", seo_description="d", seo_tags=[]
    )
    row = db_conn.execute(
        """
        SELECT event_category, event_type
        FROM audit_logs
        WHERE event_type = 'blog_seo_metadata_set' AND target_id = ?
        """,
        (str(b.id),),
    ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# is_legal_transition pure helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "current,requested,expected",
    [
        ("idea", "outlining", True),
        ("idea", "archived", True),
        ("idea", "drafting", False),
        ("idea", "published_externally", False),
        ("drafting", "outlining", True),
        ("editing", "drafting", True),
        ("ready", "exported", True),
        ("exported", "published_externally", True),
        ("published_externally", "archived", True),
        ("published_externally", "ready", False),
        ("archived", "idea", False),
        ("archived", "archived", False),
        ("idea", "idea", False),
        ("not-a-state", "outlining", False),
    ],
)
def test_is_legal_transition(current: str, requested: str, expected: bool) -> None:
    assert bm.is_legal_transition(current, requested) is expected


# ---------------------------------------------------------------------------
# list_blogs + v_blog_pipeline integration
# ---------------------------------------------------------------------------
def test_list_blogs_returns_v_blog_pipeline_rows(db_conn: sqlite3.Connection) -> None:
    a = bm.create_blog(db_conn, title="a")
    bm.create_blog(db_conn, title="b")
    bm.transition_status(db_conn, a.id, "archived")
    all_rows = bm.list_blogs(db_conn)
    assert len(all_rows) == 2
    only_archived = bm.list_blogs(db_conn, statuses=["archived"])
    assert len(only_archived) == 1
    assert only_archived[0]["blog_id"] == a.id


def test_list_blogs_rejects_unknown_status(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(bm.InvalidBlogFieldError):
        bm.list_blogs(db_conn, statuses=["totally-fake"])
