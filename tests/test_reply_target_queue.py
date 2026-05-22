"""Reply Target Queue + tools #6/#7 + maintenance — Phase 5.6 acceptance gates.

The 256-combo resolver test lives in ``test_reply_target_resolver.py``;
this module covers the rest of the §25 Phase 5.6 QA list.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.agent.tools import (
    _parse_x_post_id,
    _record_reply_target,
    _score_reply_candidates,
)
from app.db import transaction
from app.jobs.reply_target_maintenance import (
    expire_stale_candidates,
    stale_drafted_candidates,
    vacuum_cleanup_dead_candidates,
)


# ---------------------------------------------------------------------------
# Tool #7 — record_reply_target
# ---------------------------------------------------------------------------
def test_record_creates_a_reply_target_row(db_conn: sqlite3.Connection):
    out = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/someone/status/1234567890",
        target_user="someone",
        like_count=42,
        reply_count=8,
    )
    assert out["created"] is True
    assert isinstance(out["reply_target_id"], int)
    row = db_conn.execute(
        "SELECT target_x_post_id, status, target_author_handle, like_count, reply_count "
        "FROM reply_targets WHERE id = ?",
        (out["reply_target_id"],),
    ).fetchone()
    assert row["target_x_post_id"] == "1234567890"
    assert row["status"] == "candidate"
    assert row["target_author_handle"] == "someone"
    assert row["like_count"] == 42
    assert row["reply_count"] == 8


def test_record_is_idempotent_on_target_post_url(db_conn: sqlite3.Connection):
    """Re-recording the same URL returns the existing id and enriches it."""
    out1 = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/a/status/100",
        target_user="a",
    )
    assert out1["created"] is True
    out2 = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/a/status/100",
        target_user="a",
        like_count=99,
    )
    assert out2["created"] is False
    assert out2["reply_target_id"] == out1["reply_target_id"]
    row = db_conn.execute(
        "SELECT like_count FROM reply_targets WHERE id = ?",
        (out1["reply_target_id"],),
    ).fetchone()
    assert row["like_count"] == 99


def test_record_rejects_invalid_reply_intent(db_conn: sqlite3.Connection):
    out = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/a/status/200",
        reply_intent="not_in_enum",
    )
    assert "error" in out
    assert "reply_intent" in out["error"]


# ---------------------------------------------------------------------------
# Tool #6 — score_reply_candidates
# ---------------------------------------------------------------------------
def test_score_creates_row_from_candidate_dict_and_resolves_action(db_conn: sqlite3.Connection):
    out = _score_reply_candidates(
        db_conn,
        candidates=[{
            "url": "https://x.com/x/status/300",
            "author_handle": "x",
            "target_author_follower_count": 10000,
            "like_count": 80,             # well above the 50-threshold floor
            "reply_count": 5,             # fresh thread → saturation 3
            "relevance_score": 3,
            "reply_opportunity_score": 3,
            "score_rationale": "active build pillar thread",
            "pillar": "build",
            "reply_intent": "relationship",
        }],
    )
    assert len(out["scored"]) == 1
    s = out["scored"][0]
    # engagement = above floor 50, below 3*50=150 → score 2.
    assert s["engagement_surface_score"] == 2
    # saturation: reply_count=5 → 3.
    assert s["saturation_score"] == 3
    # Resolver: relevance=3, eng=2, sat=3, opp=3 → all >= 2 → reply_now.
    assert s["recommended_action_label"] == "reply_now"
    assert s["recommended_action_score"] == 3


def test_score_with_null_author_falls_back_to_floor_thresholds(db_conn: sqlite3.Connection):
    """§29.4 — without an author follower count, the floor thresholds apply."""
    out = _score_reply_candidates(
        db_conn,
        candidates=[{
            "url": "https://x.com/x/status/301",
            "author_handle": "x",
            "like_count": 30,            # above floor 15, below floor 50 → 1
            "reply_count": 0,
            "relevance_score": 2,
            "reply_opportunity_score": 2,
        }],
    )
    s = out["scored"][0]
    assert s["engagement_surface_score"] == 1
    # relevance=2, eng=1, sat=3, opp=2 → ANY 0? no. ALL>=2? no (eng=1). Then
    # relevance>=2 AND opp>=2 → reply_if_time.
    assert s["recommended_action_label"] == "reply_if_time"


def test_score_without_judgments_leaves_action_null(db_conn: sqlite3.Connection):
    """When relevance + reply_opportunity are absent, recommended_action stays NULL."""
    out = _score_reply_candidates(
        db_conn,
        candidates=[{
            "url": "https://x.com/x/status/302",
            "author_handle": "x",
            "like_count": 30,
            "reply_count": 5,
        }],
    )
    s = out["scored"][0]
    assert s["recommended_action_label"] is None
    assert s["recommended_action_score"] is None
    # Mechanical dimensions still computed.
    assert s["engagement_surface_score"] is not None
    assert s["saturation_score"] is not None


def test_score_rejects_mixed_mode_with_both_candidates_and_reply_target_id(db_conn: sqlite3.Connection):
    """/review-2 🟡 #4 — both inputs is an ambiguous call; reject loudly."""
    out = _score_reply_candidates(
        db_conn,
        candidates=[{"url": "https://x.com/x/status/1"}],
        reply_target_id=1,
    )
    assert out["scored"] == []
    assert any("either candidates or reply_target_id" in e for e in out["errors"])


def test_score_rolls_back_partial_row_on_inner_check_failure(
    db_conn: sqlite3.Connection, monkeypatch
):
    """/review-2 🔴 #2 — a CHECK violation on the score UPDATE must roll
    back the just-INSERTed reply_targets row instead of orphaning it.
    """
    # Force the inner UPDATE to fail by patching the resolver to return
    # an out-of-range action_score that violates the CHECK on
    # recommended_action_score (BETWEEN 0 AND 3).
    import app.agent.tools as tools_mod

    original = tools_mod._compute_and_persist_scores_locked

    def _broken_compute(*args, **kwargs):
        # Mimic the contract but raise to simulate a downstream failure.
        raise sqlite3.IntegrityError("simulated CHECK violation on score")

    monkeypatch.setattr(tools_mod, "_compute_and_persist_scores_locked", _broken_compute)

    out = _score_reply_candidates(
        db_conn,
        candidates=[{
            "url": "https://x.com/x/status/999",
            "author_handle": "x",
            "like_count": 30,
            "reply_count": 5,
            "relevance_score": 3,
            "reply_opportunity_score": 3,
        }],
    )
    # Result: per-candidate error, no scored entries.
    assert out["scored"] == []
    assert any("simulated CHECK violation" in e for e in out["errors"])
    # Critical assertion: NO orphaned row in reply_targets.
    row = db_conn.execute(
        "SELECT id FROM reply_targets WHERE target_post_url = ?",
        ("https://x.com/x/status/999",),
    ).fetchone()
    assert row is None, (
        "🔴 #2 regression — partial reply_targets row survived an inner-tx "
        "failure; the outer transaction should have rolled it back."
    )

    # Restore the real function and re-run; the row should land cleanly.
    monkeypatch.setattr(tools_mod, "_compute_and_persist_scores_locked", original)
    out2 = _score_reply_candidates(
        db_conn,
        candidates=[{
            "url": "https://x.com/x/status/999",
            "author_handle": "x",
            "like_count": 30,
            "reply_count": 5,
            "relevance_score": 3,
            "reply_opportunity_score": 3,
        }],
    )
    assert len(out2["scored"]) == 1
    assert out2["errors"] == []


def test_score_re_scores_existing_row_by_id(db_conn: sqlite3.Connection):
    rec = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/x/status/400",
        target_user="x",
        like_count=200,
        reply_count=15,
    )
    rt_id = rec["reply_target_id"]
    # Patch the row with metric-driven scores only.
    out = _score_reply_candidates(db_conn, reply_target_id=rt_id)
    s = out["scored"][0]
    # No NULL-author footnote here; we have like_count but no author count.
    # Engagement: 200 likes, floor thresholds 15/50 → above 50 but below 150
    # → score 2 (wait, 200 >= 150 → 3). Let me check: 3 * high_threshold(50)=150,
    # so >= 150 → 3.
    assert s["engagement_surface_score"] == 3
    # Saturation: 15 → 2 ("top 30; thread active").
    assert s["saturation_score"] == 2
    # No relevance/opp → action stays NULL.
    assert s["recommended_action_label"] is None


# ---------------------------------------------------------------------------
# Maintenance jobs
# ---------------------------------------------------------------------------
def test_expire_stale_candidates_transitions_correctly(db_conn: sqlite3.Connection):
    # Insert a stale row (50h old) and a fresh row.
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, last_checked_at_utc) "
        "VALUES ('manual','https://x.com/a/status/500','a', datetime('now','-50 hours'))"
    )
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, target_author_handle) "
        "VALUES ('manual','https://x.com/b/status/501','b')"
    )
    expired = expire_stale_candidates(db_conn)
    assert len(expired) == 1
    statuses = {
        r["target_post_url"]: r["status"]
        for r in db_conn.execute(
            "SELECT target_post_url, status FROM reply_targets"
        ).fetchall()
    }
    assert statuses["https://x.com/a/status/500"] == "expired"
    assert statuses["https://x.com/b/status/501"] == "candidate"


def test_stale_drafted_banner_returns_drafted_rows(db_conn: sqlite3.Connection):
    # Insert a drafted row with an agent_draft_id (the banner only fires
    # when both conditions hold).
    db_conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active')"
    )
    db_conn.execute(
        "INSERT INTO agent_drafts (draft_kind, text, iwh_attempt_index, status) "
        "VALUES ('reply', 'placeholder', 1, 'proposed')"
    )
    draft_id = db_conn.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]
    db_conn.execute(
        """
        INSERT INTO reply_targets
            (discovered_via, target_post_url, target_author_handle, status,
             agent_draft_id, last_checked_at_utc)
        VALUES ('manual','https://x.com/d/status/600','d','drafted', ?,
                datetime('now','-30 hours'))
        """,
        (draft_id,),
    )
    stale = stale_drafted_candidates(db_conn)
    assert len(stale) == 1
    assert stale[0]["target_post_url"] == "https://x.com/d/status/600"


def test_vacuum_cleanup_deletes_dead_rows_over_90_days(db_conn: sqlite3.Connection):
    # Insert a skipped row 100 days old + a posted row 100 days old (must stay).
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, status, discovered_at_utc) "
        "VALUES ('manual','https://x.com/k/status/700','k','skipped', "
        "datetime('now','-100 days'))"
    )
    db_conn.execute(
        "INSERT INTO reply_targets (discovered_via, target_post_url, "
        "target_author_handle, status, discovered_at_utc) "
        "VALUES ('manual','https://x.com/k/status/701','k','posted', "
        "datetime('now','-100 days'))"
    )
    n = vacuum_cleanup_dead_candidates(db_conn)
    assert n == 1
    urls = {
        r["target_post_url"]
        for r in db_conn.execute("SELECT target_post_url FROM reply_targets").fetchall()
    }
    assert "https://x.com/k/status/700" not in urls
    assert "https://x.com/k/status/701" in urls   # posted stays forever


# ---------------------------------------------------------------------------
# Mark posted — atomic transaction including the rollback path.
# ---------------------------------------------------------------------------
def test_mark_posted_transaction_links_rows_atomically(db_conn: sqlite3.Connection):
    rec = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/m/status/800",
        target_user="m",
        like_count=42,
        reply_count=5,
    )
    rt_id = rec["reply_target_id"]
    posted_url = "https://x.com/dannyscalant/status/9001"
    x_id = _parse_x_post_id(posted_url)
    with transaction(db_conn):
        cur = db_conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type,
                 posted_via, manual_confirmation_status,
                 x_post_id, url, in_reply_to_reply_target_id, reply_intent)
            VALUES (datetime('now'), date('now'), ?, 'reply',
                    'manual', 'needs_metrics', ?, ?, ?, ?)
            """,
            ("placeholder reply text", x_id, posted_url, rt_id, "icp_discovery"),
        )
        post_id = int(cur.lastrowid)
        db_conn.execute(
            """
            UPDATE reply_targets
            SET status = 'posted', posted_reply_post_id = ?,
                reply_intent = ?, last_checked_at_utc = datetime('now')
            WHERE id = ?
            """,
            (post_id, "icp_discovery", rt_id),
        )
    # Both rows updated, both link FK's consistent.
    rt = db_conn.execute(
        "SELECT status, posted_reply_post_id, reply_intent FROM reply_targets WHERE id = ?",
        (rt_id,),
    ).fetchone()
    p = db_conn.execute(
        "SELECT in_reply_to_reply_target_id, reply_intent FROM posts WHERE id = ?",
        (rt["posted_reply_post_id"],),
    ).fetchone()
    assert rt["status"] == "posted"
    assert p["in_reply_to_reply_target_id"] == rt_id
    assert rt["reply_intent"] == "icp_discovery"
    assert p["reply_intent"] == "icp_discovery"


def test_mark_posted_rollback_on_failure(db_conn: sqlite3.Connection):
    """Force a failure inside the transaction and verify both rows survive."""
    rec = _record_reply_target(
        db_conn,
        target_post_url="https://x.com/m/status/801",
        target_user="m",
    )
    rt_id = rec["reply_target_id"]
    initial_status = db_conn.execute(
        "SELECT status FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()["status"]
    assert initial_status == "candidate"

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db_conn):
            # Insert a valid posts row.
            db_conn.execute(
                """
                INSERT INTO posts
                    (created_at_utc, created_date, text, type,
                     posted_via, manual_confirmation_status,
                     in_reply_to_reply_target_id)
                VALUES (datetime('now'), date('now'), 'x', 'reply',
                        'manual', 'needs_metrics', ?)
                """,
                (rt_id,),
            )
            # Force a CHECK failure on reply_targets to roll the whole thing back.
            db_conn.execute(
                "UPDATE reply_targets SET status = 'INVALID_STATUS' WHERE id = ?",
                (rt_id,),
            )
    # Rollback should have left the row at 'candidate'.
    after = db_conn.execute(
        "SELECT status FROM reply_targets WHERE id = ?", (rt_id,)
    ).fetchone()["status"]
    assert after == "candidate"
    # And no posts row should have survived.
    n_posts = db_conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE in_reply_to_reply_target_id = ?",
        (rt_id,),
    ).fetchone()["c"]
    assert n_posts == 0


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------
def test_parse_x_post_id_extracts_id():
    assert _parse_x_post_id("https://x.com/dannyscalant/status/1840000000") == "1840000000"
    assert _parse_x_post_id("https://twitter.com/jack/status/2") == "2"


def test_parse_x_post_id_returns_none_for_bad_url():
    assert _parse_x_post_id("https://x.com/dannyscalant") is None
    assert _parse_x_post_id("not a url") is None


# ---------------------------------------------------------------------------
# §29.8 drift check — reply_intent enum stays in sync across spec / code / prompt.
# ---------------------------------------------------------------------------
def test_reply_intent_enum_matches_across_spec_code_and_prompt():
    from app.agent.prompt_builder import verify_reply_intent_enum_matches
    spec, code, prompt = verify_reply_intent_enum_matches()
    assert set(spec) == set(code) == set(prompt), (
        f"§29.5 reply_intent enum drift detected:\n"
        f"  spec:   {spec}\n  code:   {code}\n  prompt: {prompt}"
    )
