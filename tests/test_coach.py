"""Phase 5.10 / §28.23 — Coach citation-allowlist + refuse-without-evidence.

Covers:

* Citation extraction (six allowlisted record types + a view-row + a
  malformed bracket).
* Per-record-type resolvers against a seeded DB.
* The view-row resolver's filter-token shape check.
* ``unsupported_record_type`` stripping for unknown types.
* ``enforce()`` orchestration: text rewrite, double-space cleanup,
  refusal substitution under the documented gate, refusal disabled
  when ``refuse_without_evidence=False``, refusal NOT triggered on
  non-analytical messages even with no citations.
* ``coach_tool_registry()`` returns a filtered copy and
  ``assert_coach_excludes_write_tools`` raises on a leak.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from app.agent import coach as _coach
from app.agent.tools import AGENT_TOOLS


# ---------------------------------------------------------------------------
# Fixtures — seed minimal data for resolver coverage.
# ---------------------------------------------------------------------------
@pytest.fixture
def seeded_conn(db_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Seed one row each in posts / experiments / agent_drafts / weekly_reviews."""
    db_conn.execute(
        """
        INSERT INTO posts (id, created_date, text, type, posted_via,
                            manual_confirmation_status)
        VALUES (42, '2026-05-04', 'seed post', 'standalone', 'manual', 'confirmed')
        """
    )
    db_conn.execute(
        """
        INSERT INTO experiments (id, name, hypothesis, success_metric,
                                  status, start_date)
        VALUES (7, 'replier velocity', 'replies → followers',
                'follower_delta_7d', 'running', '2026-05-01')
        """
    )
    db_conn.execute(
        """
        INSERT INTO agent_drafts (id, draft_kind, text, content_type)
        VALUES (88, 'standalone', 'draft body', 'value')
        """
    )
    db_conn.execute(
        """
        INSERT INTO weekly_reviews (week_start_date, week_end_date)
        VALUES ('2026-05-04', '2026-05-10')
        """
    )
    return db_conn


# ---------------------------------------------------------------------------
# extract_citations — parsing.
# ---------------------------------------------------------------------------
def test_extract_citations_single_id_form() -> None:
    citations = _coach.extract_citations("see 〔post 42〕 for context")
    assert len(citations) == 1
    c = citations[0]
    assert c.record_type == "post"
    assert c.record_id == "42"
    assert c.filter_text is None


def test_extract_citations_view_row_form() -> None:
    citations = _coach.extract_citations(
        "look at 〔v_lane_performance row build/icp/value〕 closely"
    )
    assert len(citations) == 1
    c = citations[0]
    assert c.record_type == "v_lane_performance"
    assert c.record_id == "row build/icp/value"
    assert c.filter_text == "build/icp/value"


def test_extract_citations_multiple_in_one_message() -> None:
    text = (
        "Compare 〔post 42〕 with 〔experiment 7〕 and the latest "
        "〔weekly_review 2026-W19〕."
    )
    citations = _coach.extract_citations(text)
    assert [c.record_type for c in citations] == [
        "post",
        "experiment",
        "weekly_review",
    ]


def test_extract_citations_skips_empty_brackets() -> None:
    citations = _coach.extract_citations("〔〕 plus 〔post 42〕")
    # Empty bracket is silently skipped — the strip log only carries
    # tokens that survived parsing but failed validation.
    assert len(citations) == 1
    assert citations[0].record_type == "post"


# ---------------------------------------------------------------------------
# Resolvers — per record_type.
# ---------------------------------------------------------------------------
def test_resolve_post_existing_and_missing(
    seeded_conn: sqlite3.Connection,
) -> None:
    citations = _coach.extract_citations(
        "first 〔post 42〕 then 〔post 999〕"
    )
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert [c.record_id for c in surviving] == ["42"]
    assert len(stripped) == 1
    assert stripped[0].reason == "not_found"
    assert stripped[0].record_type == "post"


def test_resolve_experiment_existing_and_missing(
    seeded_conn: sqlite3.Connection,
) -> None:
    citations = _coach.extract_citations(
        "〔experiment 7〕 vs 〔experiment 99〕"
    )
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert len(surviving) == 1 and surviving[0].record_id == "7"
    assert stripped[0].reason == "not_found"


def test_resolve_agent_draft_existing(
    seeded_conn: sqlite3.Connection,
) -> None:
    citations = _coach.extract_citations("see 〔agent_draft 88〕")
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert len(surviving) == 1
    assert not stripped


def test_resolve_weekly_review_iso_week_format(
    seeded_conn: sqlite3.Connection,
) -> None:
    # ISO 2026-W19 = Monday 2026-05-04 — matches the seeded row.
    citations = _coach.extract_citations("〔weekly_review 2026-W19〕")
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert len(surviving) == 1
    assert not stripped


def test_resolve_weekly_review_iso_date_format(
    seeded_conn: sqlite3.Connection,
) -> None:
    # Direct date form also works — the resolver accepts both.
    citations = _coach.extract_citations("〔weekly_review 2026-05-04〕")
    surviving, _stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert len(surviving) == 1


def test_resolve_monthly_review_always_strips_until_phase511(
    seeded_conn: sqlite3.Connection,
) -> None:
    """monthly_review is on the allowlist but the table doesn't exist yet (§28.23)."""
    citations = _coach.extract_citations("〔monthly_review 2026-05〕")
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert not surviving
    assert len(stripped) == 1
    # Recognized record_type → reason should be 'not_found', NOT
    # 'unsupported_record_type'. The agent's intent is preserved.
    assert stripped[0].reason == "not_found"


def test_unknown_record_type_stripped_with_specific_reason(
    seeded_conn: sqlite3.Connection,
) -> None:
    citations = _coach.extract_citations("〔dashboard_screenshot foo〕")
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert not surviving
    assert stripped[0].reason == "unsupported_record_type"


def test_malformed_citation_no_id_stripped(
    seeded_conn: sqlite3.Connection,
) -> None:
    citations = _coach.extract_citations("〔post〕")
    surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert not surviving
    assert stripped[0].reason == "malformed"


# ---------------------------------------------------------------------------
# View-row resolver.
# ---------------------------------------------------------------------------
def test_resolve_view_row_unknown_view(seeded_conn: sqlite3.Connection) -> None:
    citations = _coach.extract_citations("〔v_made_up_view row a/b/c〕")
    _surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert stripped and stripped[0].reason == "view_not_found"


def test_resolve_view_row_filter_token_count_mismatch(
    seeded_conn: sqlite3.Connection,
) -> None:
    # v_lane_performance expects three tokens (pillar/audience/cta);
    # two tokens trip view_filter_mismatch.
    citations = _coach.extract_citations(
        "〔v_lane_performance row build/icp〕"
    )
    _surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert stripped and stripped[0].reason == "view_filter_mismatch"


def test_resolve_view_row_filter_no_match(
    seeded_conn: sqlite3.Connection,
) -> None:
    # Three tokens, but nothing in v_lane_performance matches them.
    citations = _coach.extract_citations(
        "〔v_lane_performance row stir/icp/value〕"
    )
    _surviving, stripped = _coach.validate_against_allowlist(seeded_conn, citations)
    assert stripped and stripped[0].reason == "view_filter_mismatch"


# ---------------------------------------------------------------------------
# enforce — orchestration.
# ---------------------------------------------------------------------------
def test_enforce_strips_invalid_citations_from_text(
    seeded_conn: sqlite3.Connection,
) -> None:
    text = (
        "Compare 〔post 42〕 with 〔post 999〕 — both worth a look."
    )
    result = _coach.enforce(text, seeded_conn)
    assert "〔post 42〕" in result.clean_text  # survivor kept
    assert "〔post 999〕" not in result.clean_text  # stripped
    assert len(result.surviving) == 1
    assert len(result.stripped) == 1
    assert not result.refused


def test_enforce_collapses_double_spaces_left_by_strip(
    seeded_conn: sqlite3.Connection,
) -> None:
    text = "look here 〔post 999〕 and then continue"
    result = _coach.enforce(text, seeded_conn)
    # After removing the bracket token, the two spaces that
    # surrounded it collapse to one — readability matters.
    assert "  " not in result.clean_text


def test_enforce_refuses_when_analytical_claim_has_no_surviving_citations(
    seeded_conn: sqlite3.Connection,
) -> None:
    # Analytical claim per confidence_patterns.py: "gained N followers".
    text = "you gained 12 followers this week 〔post 999〕"
    result = _coach.enforce(text, seeded_conn)
    assert result.refused is True
    assert result.clean_text.startswith(
        "I don't have data in your dashboard to answer this honestly."
    )
    assert result.refusal_reason is not None


def test_enforce_does_not_refuse_when_analytical_claim_has_surviving_citation(
    seeded_conn: sqlite3.Connection,
) -> None:
    # Same analytical phrasing, but with a valid citation that survives.
    text = "you gained 12 followers this week 〔post 42〕"
    result = _coach.enforce(text, seeded_conn)
    assert result.refused is False
    assert "〔post 42〕" in result.clean_text


def test_enforce_does_not_refuse_on_non_analytical_text(
    seeded_conn: sqlite3.Connection,
) -> None:
    # Plain conversational reply with no analytical pattern — no
    # citations needed, no refusal even with refuse_without_evidence ON.
    text = "Happy to think through that with you. What's the question?"
    result = _coach.enforce(text, seeded_conn)
    assert result.refused is False
    assert result.clean_text == text


def test_enforce_respects_refuse_without_evidence_toggle(
    seeded_conn: sqlite3.Connection,
) -> None:
    text = "you gained 12 followers this week"
    result = _coach.enforce(
        text, seeded_conn, refuse_without_evidence=False
    )
    # Toggle OFF → text passes through whatever the agent emitted.
    assert result.refused is False
    assert result.clean_text == text


# ---------------------------------------------------------------------------
# Tool-registry filtering + startup assertion.
# ---------------------------------------------------------------------------
def test_coach_tool_registry_excludes_every_forbidden_name() -> None:
    filtered = _coach.coach_tool_registry(AGENT_TOOLS)
    names = {t.name for t in filtered}
    overlap = names & _coach.COACH_FORBIDDEN_TOOLS
    assert not overlap, f"Coach registry leaked write tools: {sorted(overlap)}"
    # And the filter preserves the read tools we'd expect.
    assert "query_dashboard_state" in names
    assert "get_lane_performance" in names


def test_assert_coach_excludes_write_tools_passes_on_main() -> None:
    """Sanity: the production AGENT_TOOLS already satisfies the invariant."""
    _coach.assert_coach_excludes_write_tools(AGENT_TOOLS)


def test_assert_coach_excludes_write_tools_raises_on_leak() -> None:
    """A synthetic leaked tool trips the assertion."""

    @dataclass(frozen=True)
    class _FakeTool:
        name: str

    # Build a registry where the filter doesn't actually exclude the
    # write tool — coach_tool_registry uses the COACH_FORBIDDEN_TOOLS
    # set, so we pass a tool whose name we KNOW is in that set but
    # construct a synthetic registry that bypasses the filter.
    leaked_name = next(iter(_coach.COACH_FORBIDDEN_TOOLS))
    fake_registry = [_FakeTool(name=leaked_name)]

    # We need to trip the assertion. coach_tool_registry filters by
    # name, so it would remove our fake. Instead, monkey-patch the
    # filter function to a no-op for this test only.
    import app.agent.coach as coach_module

    original = coach_module.coach_tool_registry
    coach_module.coach_tool_registry = lambda tools: tools  # type: ignore[assignment]
    try:
        with pytest.raises(AssertionError, match="Coach tool registry leaks"):
            coach_module.assert_coach_excludes_write_tools(fake_registry)
    finally:
        coach_module.coach_tool_registry = original  # type: ignore[assignment]
