"""Phase 5.9 / §28.17 — V/G/P/P content type axis.

Covers:

  1. content_types.validate_for_save — rejects missing, 'unspecified',
     unknown values; accepts lower-cased + stripped V/G/P/P.
  2. get_content_type_gaps — golden-input counts over a rolling window,
     under-represented selection with canonical tie-break, even-spread
     and zero-row paths.
  3. _save_draft_post / _save_draft_reply orchestrator refusal for
     missing or 'unspecified' content_type — handler must raise BEFORE
     any DB write.
  4. AGENT_TOOLS registry contains get_content_type_gaps and the save
     tools have content_type in their required fields.
  5. v_content_type_performance excludes 'unspecified' rows.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from app.agent import content_types
from app.agent import niche as _niche
from app.agent.tools import (
    AGENT_TOOLS,
    _save_draft_post,
    _save_draft_reply,
    get_tool,
)


# ---------------------------------------------------------------------------
# validate_for_save — the orchestrator's enforcement contract.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ct", ["value", "growth", "personality", "proof"])
def test_validate_accepts_all_four_canonical_types(ct: str) -> None:
    assert content_types.validate_for_save(ct) == ct


@pytest.mark.parametrize("ct", ["VALUE", "  Growth  ", "Personality"])
def test_validate_strips_and_lowercases(ct: str) -> None:
    out = content_types.validate_for_save(ct)
    assert out == ct.strip().lower()


def test_validate_rejects_none() -> None:
    with pytest.raises(content_types.ContentTypeInvalidError):
        content_types.validate_for_save(None)


def test_validate_rejects_unspecified() -> None:
    """CHECK constraint permits 'unspecified' for backfill, but the
    orchestrator must refuse it from the agent (§28.17)."""
    with pytest.raises(content_types.ContentTypeInvalidError) as exc:
        content_types.validate_for_save("unspecified")
    assert "unspecified" in str(exc.value).lower()


def test_validate_rejects_unknown() -> None:
    with pytest.raises(content_types.ContentTypeInvalidError):
        content_types.validate_for_save("thought_leadership")


# ---------------------------------------------------------------------------
# get_content_type_gaps — golden-input shape.
# ---------------------------------------------------------------------------
def _insert_post(
    conn: sqlite3.Connection,
    *,
    content_type: str,
    days_ago: int = 0,
) -> int:
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    return int(conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status, content_type)
        VALUES (?, 'x', 'standalone', 'manual', 'confirmed', ?)
        RETURNING id
        """,
        (d, content_type),
    ).fetchone()[0])


def test_gaps_returns_even_spread_when_no_posts(db_conn: sqlite3.Connection) -> None:
    out = content_types.get_content_type_gaps(db_conn, window_days=7)
    assert out["under_represented"] is None
    assert "no classified posts" in out["rationale"].lower()
    # Every key still present so the UI's `.get` calls don't KeyError.
    for ct in content_types.CONTENT_TYPES:
        assert out["counts"][ct] == 0


def test_gaps_picks_under_represented(db_conn: sqlite3.Connection) -> None:
    for _ in range(5):
        _insert_post(db_conn, content_type="value")
    _insert_post(db_conn, content_type="growth")
    _insert_post(db_conn, content_type="growth")
    # personality + proof both at 0 → tie; canonical order picks personality.
    out = content_types.get_content_type_gaps(db_conn, window_days=7)
    assert out["under_represented"] == "personality"
    assert out["counts"]["value"] == 5
    assert out["counts"]["growth"] == 2
    assert "5 value" in out["rationale"]
    assert "0 personality" in out["rationale"]


def test_gaps_returns_none_when_all_counts_equal(db_conn: sqlite3.Connection) -> None:
    for ct in content_types.CONTENT_TYPES:
        _insert_post(db_conn, content_type=ct)
    out = content_types.get_content_type_gaps(db_conn, window_days=7)
    assert out["under_represented"] is None
    assert "even spread" in out["rationale"]


def test_gaps_window_excludes_old_posts(db_conn: sqlite3.Connection) -> None:
    # 30 days back — outside a 7-day window.
    _insert_post(db_conn, content_type="value", days_ago=30)
    _insert_post(db_conn, content_type="growth", days_ago=2)
    out = content_types.get_content_type_gaps(db_conn, window_days=7)
    # Only the growth post counts; value should be 0.
    assert out["counts"]["value"] == 0
    assert out["counts"]["growth"] == 1


def test_gaps_excludes_unspecified_from_under_representation(
    db_conn: sqlite3.Connection,
) -> None:
    """'unspecified' rows are counted (for transparency) but never
    suggested as under-represented because they're not a real category."""
    for _ in range(10):
        _insert_post(db_conn, content_type="unspecified")
    _insert_post(db_conn, content_type="value")
    _insert_post(db_conn, content_type="growth")
    _insert_post(db_conn, content_type="personality")
    out = content_types.get_content_type_gaps(db_conn, window_days=7)
    # P59A-S9: counts contains only the four real V/G/P/P types now;
    # the backfill bucket lives on its own key.
    assert "unspecified" not in out["counts"]
    assert out["unspecified_count"] == 10
    # proof is at 0; should be the under-represented suggestion.
    assert out["under_represented"] == "proof"


# ---------------------------------------------------------------------------
# Orchestrator refusal in _save_draft_*.
# ---------------------------------------------------------------------------
def _set_niche(conn: sqlite3.Connection) -> None:
    """Helper — Phase 5.9 rule #15 requires niche before drafting.

    The content_type tests in this file aren't testing rule #15, so we
    pre-set the niche to get past the orchestrator's earlier gate.
    """
    _niche.set_niche(conn, problem="x", person="y")


def test_save_draft_post_refuses_missing_content_type(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    with pytest.raises(content_types.ContentTypeInvalidError):
        _save_draft_post(
            db_conn,
            text="hi",
            pillar="stir",
            audience="icp",
            cta="none",
        )
    # No row landed.
    n = db_conn.execute("SELECT COUNT(*) FROM agent_drafts").fetchone()[0]
    assert n == 0


def test_save_draft_post_refuses_unspecified_content_type(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    with pytest.raises(content_types.ContentTypeInvalidError):
        _save_draft_post(
            db_conn,
            text="hi",
            pillar="stir",
            audience="icp",
            cta="none",
            content_type="unspecified",
        )


def test_save_draft_post_accepts_canonical_content_type_and_propagates(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    out = _save_draft_post(
        db_conn,
        text="A specific takeaway.",
        pillar="build",
        audience="icp",
        cta="none",
        content_type="value",
    )
    draft_ct = db_conn.execute(
        "SELECT content_type FROM agent_drafts WHERE id = ?",
        (out["draft_id"],),
    ).fetchone()[0]
    post_ct = db_conn.execute(
        "SELECT content_type FROM posts WHERE id = ?",
        (out["post_id"],),
    ).fetchone()[0]
    # Both rows carry the same value — agent_drafts.content_type mirrors
    # posts.content_type per §28.17.
    assert draft_ct == "value"
    assert post_ct == "value"


def test_revise_draft_preserves_content_type(
    db_conn: sqlite3.Connection,
) -> None:
    """P59A-W12 + P59A-C1 regression: every IWH revision must propagate
    content_type from the source draft. Before C1, _revise_draft
    omitted content_type from both INSERTs and the new agent_drafts
    row landed with content_type=NULL (silently dropped from
    v_content_type_performance) while the linked posts row defaulted
    to content_type='unspecified' — bypassing the §28.17 'refuse
    unspecified' orchestrator promise via the revise path.
    """
    from app.agent.tools import _revise_draft
    _set_niche(db_conn)
    src = _save_draft_post(
        db_conn,
        text="Original draft.",
        pillar="build",
        audience="icp",
        cta="none",
        content_type="personality",
    )
    rev = _revise_draft(
        db_conn,
        draft_post_id=src["draft_id"],
        feedback="too vague",
        new_text="Revised draft.",
    )
    # New agent_drafts row carries the source content_type.
    new_draft_ct = db_conn.execute(
        "SELECT content_type FROM agent_drafts WHERE id = ?",
        (rev["new_draft_id"],),
    ).fetchone()[0]
    assert new_draft_ct == "personality"
    # New linked posts row also carries it.
    new_post_ct = db_conn.execute(
        "SELECT content_type FROM posts WHERE id = ?",
        (rev["post_id"],),
    ).fetchone()[0]
    assert new_post_ct == "personality"


def test_save_draft_reply_refuses_missing_content_type(
    db_conn: sqlite3.Connection,
) -> None:
    _set_niche(db_conn)
    with pytest.raises(content_types.ContentTypeInvalidError):
        _save_draft_reply(
            db_conn,
            text="hi",
            target_post_url="https://x.com/foo/status/1",
        )


# ---------------------------------------------------------------------------
# Tool registry shape.
# ---------------------------------------------------------------------------
def test_get_content_type_gaps_is_in_registry() -> None:
    tool = get_tool("get_content_type_gaps")
    assert tool.input_schema["properties"]["window_days"]["type"] == "integer"


def test_save_draft_post_schema_marks_content_type_required() -> None:
    tool = get_tool("save_draft_post")
    assert "content_type" in tool.input_schema["required"]
    assert tool.input_schema["properties"]["content_type"]["enum"] == list(
        content_types.CONTENT_TYPES
    )


def test_save_draft_reply_schema_marks_content_type_required() -> None:
    tool = get_tool("save_draft_reply")
    assert "content_type" in tool.input_schema["required"]


def test_no_duplicate_tool_names_after_phase59_registration() -> None:
    names = [t.name for t in AGENT_TOOLS]
    assert len(names) == len(set(names))
    assert "get_content_type_gaps" in names


# ---------------------------------------------------------------------------
# v_content_type_performance excludes 'unspecified'.
# ---------------------------------------------------------------------------
def test_view_excludes_unspecified(db_conn: sqlite3.Connection) -> None:
    # Need to seed posts with classifications + metrics for the view to
    # surface a row. The view joins through v_post_latest_metrics.
    _insert_post(db_conn, content_type="value")
    _insert_post(db_conn, content_type="unspecified")
    rows = db_conn.execute(
        "SELECT content_type FROM v_content_type_performance"
    ).fetchall()
    # Whatever ends up in the view, 'unspecified' must not be there.
    seen = {r["content_type"] for r in rows}
    assert "unspecified" not in seen
