"""Phase 5.9 / §28.21 — personality lore registry.

Covers:

  1. add / list_all / list_active / set_active / set_priority / edit / delete.
  2. detect_invoked_lore — theme substring match AND description token match.
  3. scan_and_increment_invocations — only increments matched rows, updates
     last_invoked_at_utc, returns matched ids.
  4. _save_draft_post with content_type='personality' wires the scan;
     non-personality content_types DO NOT trigger the scan.
  5. render_splice_block — empty string on zero rows, formatted block
     with the "last invoked N days ago" suffix when rows present.
  6. is_over_relied_on — only fires when count > threshold AND recent.
  7. Startup invariant — no AGENT_TOOLS entry references personality_lore.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.agent import personality_lore
from app.agent import niche as _niche
from app.agent.prompt_builder import build_system_prompt
from app.agent.tools import _save_draft_post


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _set_niche(conn: sqlite3.Connection) -> None:
    _niche.set_niche(conn, problem="x", person="y")


def _seed_three_lore(conn: sqlite3.Connection) -> tuple[int, int, int]:
    a = personality_lore.add(
        conn,
        theme="water bottle in frame",
        description="long-running self-deprecating joke about my water "
                    "bottle being visible in video shots",
        priority=10,
    )
    b = personality_lore.add(
        conn,
        theme="kitchen-scanner fail",
        description="the time the scanner read ginger as soap",
        priority=20,
    )
    c = personality_lore.add(
        conn,
        theme="neuro-oncology long arc",
        description="reminder that Stir is a stepping stone, not the destination",
        priority=30,
    )
    return a, b, c


# ---------------------------------------------------------------------------
# CRUD round-trips.
# ---------------------------------------------------------------------------
def test_add_and_list_all(db_conn: sqlite3.Connection) -> None:
    a, b, c = _seed_three_lore(db_conn)
    rows = personality_lore.list_all(db_conn)
    assert {r.id for r in rows} == {a, b, c}
    # All default to active + priority 100 unless overridden.
    for r in rows:
        assert r.is_active is True
    # list_active orders by priority ASC.
    active = personality_lore.list_active(db_conn)
    assert [r.id for r in active] == [a, b, c]  # priorities 10, 20, 30


def test_add_rejects_empty_fields(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        personality_lore.add(db_conn, theme="", description="bar")
    with pytest.raises(ValueError):
        personality_lore.add(db_conn, theme="theme", description="   ")


def test_set_active_and_priority(db_conn: sqlite3.Connection) -> None:
    rid = personality_lore.add(db_conn, theme="t", description="d")
    personality_lore.set_active(db_conn, lore_id=rid, is_active=False)
    personality_lore.set_priority(db_conn, lore_id=rid, priority=1)
    row = personality_lore.list_all(db_conn)[0]
    assert row.is_active is False
    assert row.priority == 1


def test_edit_partial(db_conn: sqlite3.Connection) -> None:
    rid = personality_lore.add(db_conn, theme="old theme", description="old desc")
    personality_lore.edit(db_conn, lore_id=rid, theme="new theme")
    row = personality_lore.list_all(db_conn)[0]
    assert row.theme == "new theme"
    assert row.description == "old desc"


def test_delete(db_conn: sqlite3.Connection) -> None:
    rid = personality_lore.add(db_conn, theme="t", description="d")
    personality_lore.delete(db_conn, lore_id=rid)
    assert personality_lore.list_all(db_conn) == []


# ---------------------------------------------------------------------------
# detect_invoked_lore — match rules.
# ---------------------------------------------------------------------------
def test_detect_matches_theme_substring(db_conn: sqlite3.Connection) -> None:
    _seed_three_lore(db_conn)
    active = personality_lore.list_active(db_conn)
    invoked = personality_lore.detect_invoked_lore(
        active,
        "Day 3 of the water bottle in frame mystery — caught it again.",
    )
    assert len(invoked) >= 1
    # The matched theme is 'water bottle in frame'.
    matched_themes = {r.theme for r in active if r.id in invoked}
    assert "water bottle in frame" in matched_themes


def test_detect_matches_description_keyword(db_conn: sqlite3.Connection) -> None:
    _seed_three_lore(db_conn)
    active = personality_lore.list_active(db_conn)
    # P59A-W10: require >=2 non-stopword tokens overlapping with the
    # description ("scanner" + "ginger" both in the kitchen-scanner
    # description). A single token like "scanner" alone no longer
    # triggers — the prior single-token rule over-counted.
    invoked = personality_lore.detect_invoked_lore(
        active, "the scanner crashed reading ginger again today"
    )
    matched_themes = {r.theme for r in active if r.id in invoked}
    assert "kitchen-scanner fail" in matched_themes


def test_detect_single_token_no_longer_matches_description(
    db_conn: sqlite3.Connection,
) -> None:
    """P59A-W10 regression: single description-token overlap must NOT
    invoke the row. Prevents 'kitchen' (or any common noun) from
    lighting up every lore mentioning the same word."""
    _seed_three_lore(db_conn)
    active = personality_lore.list_active(db_conn)
    invoked = personality_lore.detect_invoked_lore(
        active, "the scanner crashed today"  # only 'scanner' overlaps
    )
    matched_themes = {r.theme for r in active if r.id in invoked}
    assert "kitchen-scanner fail" not in matched_themes


def test_detect_returns_empty_on_no_match(db_conn: sqlite3.Connection) -> None:
    _seed_three_lore(db_conn)
    active = personality_lore.list_active(db_conn)
    invoked = personality_lore.detect_invoked_lore(
        active,
        "Random update — unrelated thoughts on Mediterranean weather patterns",
    )
    assert invoked == []


def test_detect_skips_stopwords(db_conn: sqlite3.Connection) -> None:
    """Stopwords like 'the' / 'is' / 'about' must not trigger fuzzy matches."""
    personality_lore.add(
        db_conn, theme="zzz-unique-theme-string", description="the the the the"
    )
    active = personality_lore.list_active(db_conn)
    invoked = personality_lore.detect_invoked_lore(
        active, "the example sentence the the"
    )
    assert invoked == []


# ---------------------------------------------------------------------------
# scan_and_increment_invocations — updates counters atomically.
# ---------------------------------------------------------------------------
def test_scan_increments_only_matched(db_conn: sqlite3.Connection) -> None:
    a, b, c = _seed_three_lore(db_conn)
    matched = personality_lore.scan_and_increment_invocations(
        db_conn, draft_text="the scanner crashed and lost ginger"
    )
    assert b in matched  # kitchen-scanner fail matched
    # Only `b`'s counter incremented.
    rows = {r.id: r for r in personality_lore.list_all(db_conn)}
    assert rows[b].invocation_count == 1
    assert rows[b].last_invoked_at_utc is not None
    assert rows[a].invocation_count == 0
    assert rows[c].invocation_count == 0


def test_scan_no_match_does_not_touch_counters(db_conn: sqlite3.Connection) -> None:
    a, _b, _c = _seed_three_lore(db_conn)
    matched = personality_lore.scan_and_increment_invocations(
        db_conn, draft_text="zzz unrelated"
    )
    assert matched == []
    row = next(r for r in personality_lore.list_all(db_conn) if r.id == a)
    assert row.invocation_count == 0


# ---------------------------------------------------------------------------
# Orchestrator wiring — _save_draft_post scans on content_type='personality'.
# ---------------------------------------------------------------------------
def test_save_personality_draft_scans_lore(db_conn: sqlite3.Connection) -> None:
    _set_niche(db_conn)
    _a, b, _c = _seed_three_lore(db_conn)
    out = _save_draft_post(
        db_conn,
        text="Day 4 of the kitchen-scanner fail — ginger again.",
        pillar="self",
        audience="other",
        cta="none",
        content_type="personality",
    )
    assert b in out["invoked_lore_ids"]
    row = next(r for r in personality_lore.list_all(db_conn) if r.id == b)
    assert row.invocation_count == 1
    assert row.last_invoked_at_utc is not None


def test_save_value_draft_does_not_scan_lore(db_conn: sqlite3.Connection) -> None:
    """Only personality drafts trigger the scan — value/growth/proof skip it."""
    _set_niche(db_conn)
    _seed_three_lore(db_conn)
    out = _save_draft_post(
        db_conn,
        text="Here's the kitchen-scanner trick: build your prompt with a schema.",
        pillar="build",
        audience="icp",
        cta="none",
        content_type="value",
    )
    # The draft text mentions 'kitchen-scanner' — but content_type=value
    # so the scan is a no-op.
    assert out["invoked_lore_ids"] == []
    all_counts = [r.invocation_count for r in personality_lore.list_all(db_conn)]
    assert all_counts == [0, 0, 0]


# ---------------------------------------------------------------------------
# render_splice_block + prompt_builder integration.
# ---------------------------------------------------------------------------
def test_splice_block_empty_when_no_lore(db_conn: sqlite3.Connection) -> None:
    assert personality_lore.render_splice_block([]) == ""
    # The full prompt still renders, with the placeholder replaced by ''.
    prompt = build_system_prompt(db_conn)
    assert "{{ PERSONALITY_LORE_PLACEHOLDER }}" not in prompt
    assert "Personal lore" not in prompt


def test_splice_block_renders_active_rows(db_conn: sqlite3.Connection) -> None:
    _seed_three_lore(db_conn)
    prompt = build_system_prompt(db_conn)
    assert "Personal lore" in prompt
    assert "water bottle in frame" in prompt
    assert "kitchen-scanner fail" in prompt
    # Inactive rows must be excluded.
    rid = personality_lore.add(
        db_conn, theme="ignored-bit", description="should not appear",
        is_active=False,
    )
    prompt2 = build_system_prompt(db_conn)
    assert "ignored-bit" not in prompt2
    # Make sure the inactive row really is in the table (not silently
    # dropped by add()).
    assert rid in [r.id for r in personality_lore.list_all(db_conn)]


def test_splice_respects_splice_count_limit(db_conn: sqlite3.Connection) -> None:
    """Top-N from personality_lore_splice_count (default 5)."""
    db_conn.execute(
        "UPDATE settings SET value_json = '2' WHERE key = ?",
        ("personality_lore_splice_count",),
    )
    a = personality_lore.add(db_conn, theme="alpha-only", description="d", priority=1)
    b = personality_lore.add(db_conn, theme="beta-only", description="d", priority=2)
    _c = personality_lore.add(db_conn, theme="gamma-only", description="d", priority=3)
    prompt = build_system_prompt(db_conn)
    assert "alpha-only" in prompt
    assert "beta-only" in prompt
    assert "gamma-only" not in prompt
    # Sanity: a and b are the lowest-priority pair, so they're spliced.
    active = personality_lore.list_active(db_conn, limit=2)
    assert {r.id for r in active} == {a, b}


# ---------------------------------------------------------------------------
# is_over_relied_on — count + recency.
# ---------------------------------------------------------------------------
def test_over_relied_requires_count_and_recency() -> None:
    base = personality_lore.LoreRow(
        id=1, theme="t", description="d", example_posts_json=None,
        invocation_count=10, last_invoked_at_utc=None,
        is_active=True, priority=100, added_at_utc="2026-01-01T00:00:00Z",
    )
    # No last_invoked → cannot be over-relied even with high count.
    assert personality_lore.is_over_relied_on(base, overuse_threshold=8) is False

    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    row_recent_high = personality_lore.LoreRow(
        **{**base.__dict__, "last_invoked_at_utc": recent, "invocation_count": 10}
    )
    assert personality_lore.is_over_relied_on(
        row_recent_high, overuse_threshold=8
    ) is True

    # High count but invoked >30 days ago — not flagged.
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    row_old_high = personality_lore.LoreRow(
        **{**base.__dict__, "last_invoked_at_utc": old, "invocation_count": 10}
    )
    assert personality_lore.is_over_relied_on(
        row_old_high, overuse_threshold=8
    ) is False

    # Low count, recent — not flagged.
    row_low = personality_lore.LoreRow(
        **{**base.__dict__, "last_invoked_at_utc": recent, "invocation_count": 3}
    )
    assert personality_lore.is_over_relied_on(
        row_low, overuse_threshold=8
    ) is False


# ---------------------------------------------------------------------------
# Startup invariant — no tool reaches personality_lore.
# ---------------------------------------------------------------------------
def test_no_agent_tool_references_personality_lore() -> None:
    """Mirrors app/main.py::_assert_personality_lore_unreachable.

    Scans every AGENT_TOOLS entry's name + description + JSON schema for
    the bare table name. Same pattern as the publish-tool exclusion check.
    """
    import json as _json
    from app.agent.tools import AGENT_TOOLS

    needle = "personality_lore"
    offenders: list[str] = []
    for tool in AGENT_TOOLS:
        haystack = (
            tool.name + " " + tool.description + " "
            + _json.dumps(tool.input_schema)
        )
        if needle in haystack:
            offenders.append(tool.name)
    assert not offenders, (
        f"AGENT_TOOLS leaked personality_lore: {sorted(offenders)}"
    )
