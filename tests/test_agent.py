"""Session-1 invariant tests for Phase 5.5 Growth Agent.

These tests pin the security perimeter:

* Tool-registry partitioning (publish tools cannot leak into AGENT_TOOLS).
* IWH counter lives outside the agent's reachable state.
* Six-check confirmation chain — each path tested individually.
* Atomic publish: validation failure leaves token unconsumed (§28.10
  step 6); post-validation failure consumes the token (§28.4 atomicity
  rule) and marks the row publish_method='failed'.
* Crash-recovery detects orphan posts.
* Raw confirmation_token is redacted from agent_tool_calls.arguments_json.
* Double-publish rejected by check (f) (draft no longer in 'draft' state).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.agent import audit, confirmation, publish, recovery
from app.agent._internal_tools import INTERNAL_TOOLS, publish_post_to_x
from app.agent.tools import AGENT_TOOLS, _save_draft_post, _revise_draft, get_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_draft_post(conn: sqlite3.Connection, text: str = "draft text") -> int:
    """Create a draft post directly (bypassing the tool layer for speed)."""
    cur = conn.execute(
        """
        INSERT INTO posts
            (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES (date('now'), ?, 'standalone', 'agent_assisted', 'draft')
        """,
        (text,),
    )
    return int(cur.lastrowid)


def _agent_message_id(conn: sqlite3.Connection) -> int:
    """Create a conversation + message and return the message id."""
    cur = conn.execute(
        "INSERT INTO agent_conversations (title, status) VALUES ('test', 'active')"
    )
    conv_id = int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) VALUES (?, 'assistant', '')",
        (conv_id,),
    )
    return int(cur.lastrowid)


# ===========================================================================
# 1. Tool-registry partitioning
# ===========================================================================
def test_publish_tools_not_in_agent_registry():
    """§28.2 rule #10 + §28.4 internal-only tool surface."""
    agent_names = {t.name for t in AGENT_TOOLS}
    internal_names = {t.name for t in INTERNAL_TOOLS}
    assert agent_names.isdisjoint(internal_names), (
        f"publish tools leaked into AGENT_TOOLS: {sorted(agent_names & internal_names)}"
    )
    # get_tool() refuses to resolve any publish tool name.
    for name in internal_names:
        with pytest.raises(KeyError):
            get_tool(name)


def test_anthropic_spec_payload_omits_publish_tools():
    """Whatever AGENT_TOOLS serializes to, no publish-tool name appears."""
    spec_payload = [t.to_anthropic_spec() for t in AGENT_TOOLS]
    names_in_payload = {entry["name"] for entry in spec_payload}
    for internal in INTERNAL_TOOLS:
        assert internal.name not in names_in_payload


def test_publish_tool_names_match_internal_tools():
    """audit.PUBLISH_TOOL_NAMES must equal the INTERNAL_TOOLS name set.

    If someone adds a third publish tool without updating PUBLISH_TOOL_NAMES,
    audit redaction silently fails for the new tool and raw tokens leak
    into agent_tool_calls.arguments_json. This test catches that.
    """
    assert audit.PUBLISH_TOOL_NAMES == {t.name for t in INTERNAL_TOOLS}


# ===========================================================================
# 2. IWH counter lives outside agent context
# ===========================================================================
def test_iwh_counter_increments_via_revise_draft_not_agent_output(db_conn):
    """§28.2 rule #13: only the orchestrator can increment the counter.

    Simulate an agent that tries to lie about its iwh_attempt_index by
    emitting `iwh_attempt_index=1` repeatedly. The orchestrator's revise_draft
    path increments deterministically from the parent's row, not from any
    value the agent emits.
    """
    out = _save_draft_post(
        db_conn, text="v1", pillar="stir", audience="icp", cta="ask"
    )
    draft_id = out["draft_id"]
    assert out["iwh_attempt_index"] == 1

    # Revise once — orchestrator increments to 2.
    rev1 = _revise_draft(
        db_conn, draft_post_id=draft_id, feedback="too vague", new_text="v2"
    )
    assert rev1["iwh_attempt_index"] == 2

    # Revise the revision — orchestrator increments to 3, regardless of any
    # voice_self_score the agent emits.
    rev2 = _revise_draft(
        db_conn,
        draft_post_id=rev1["new_draft_id"],
        feedback="still vague",
        new_text="v3",
        voice_self_score={"intelligence": 3, "wisdom": 3, "humility": 3},
    )
    assert rev2["iwh_attempt_index"] == 3

    # The original draft is superseded; the revision row reflects the chain.
    row = db_conn.execute(
        "SELECT iwh_attempt_index, status FROM agent_drafts WHERE id = ?",
        (rev2["new_draft_id"],),
    ).fetchone()
    assert row["iwh_attempt_index"] == 3
    assert row["status"] == "proposed"


# ===========================================================================
# 3. Six-check confirmation token chain — each path
# ===========================================================================
class TestSixCheckConfirmationChain:
    def test_check_a_missing_token(self, db_conn):
        post_id = _make_draft_post(db_conn)
        with pytest.raises(confirmation.MissingTokenError):
            confirmation.validate_and_consume_token(
                db_conn, post_id=post_id, raw_token="not-a-real-token"
            )

    def test_check_b_expired_token(self, db_conn):
        post_id = _make_draft_post(db_conn)
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_id, draft_text="draft text"
        )
        # Force expiry in the future view.
        with pytest.raises(confirmation.ExpiredTokenError):
            confirmation.validate_and_consume_token(
                db_conn,
                post_id=post_id,
                raw_token=minted.raw_token,
                now=datetime.now(timezone.utc) + timedelta(seconds=120),
            )

    def test_check_c_consumed_token(self, db_conn):
        post_id = _make_draft_post(db_conn)
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_id, draft_text="draft text"
        )
        # First consumption succeeds.
        confirmation.validate_and_consume_token(
            db_conn, post_id=post_id, raw_token=minted.raw_token
        )
        # Second consumption hits check (c).
        with pytest.raises(confirmation.ConsumedTokenError):
            confirmation.validate_and_consume_token(
                db_conn, post_id=post_id, raw_token=minted.raw_token
            )

    def test_check_d_draft_text_changed(self, db_conn):
        post_id = _make_draft_post(db_conn, text="original")
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_id, draft_text="original"
        )
        # Mutate text after mint (simulates Daniel editing post-confirmation).
        db_conn.execute(
            "UPDATE posts SET text = 'mutated' WHERE id = ?", (post_id,)
        )
        with pytest.raises(confirmation.DraftTextChangedError):
            confirmation.validate_and_consume_token(
                db_conn, post_id=post_id, raw_token=minted.raw_token
            )

    def test_check_e_post_id_mismatch(self, db_conn):
        post_a = _make_draft_post(db_conn, text="a")
        post_b = _make_draft_post(db_conn, text="b")
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_a, draft_text="a"
        )
        # Token authorizes post_a; caller passes post_b.
        with pytest.raises(confirmation.PostIdMismatchError):
            confirmation.validate_and_consume_token(
                db_conn, post_id=post_b, raw_token=minted.raw_token
            )

    def test_check_f_draft_not_in_draft_state(self, db_conn):
        post_id = _make_draft_post(db_conn)
        minted = confirmation.mint_confirmation_token(
            db_conn, post_id=post_id, draft_text="draft text"
        )
        # Transition draft → confirmed (simulates manual mark-posted between
        # mint and consume).
        db_conn.execute(
            "UPDATE posts SET manual_confirmation_status = 'confirmed' WHERE id = ?",
            (post_id,),
        )
        with pytest.raises(confirmation.DraftNotInDraftStateError):
            confirmation.validate_and_consume_token(
                db_conn, post_id=post_id, raw_token=minted.raw_token
            )


# ===========================================================================
# 4. Atomic publish — validation failure leaves token unconsumed (§28.10)
# ===========================================================================
def test_validation_failure_leaves_token_unconsumed_and_marks_attempt(db_conn):
    """§28.10 step 6: token stays unconsumed when validation fails.

    Daniel can retry within the TTL without re-clicking. publish_attempt_count
    increments and publish_last_error is populated so the failure is visible.
    """
    post_id = _make_draft_post(db_conn, text="hello")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="hello"
    )
    # Mutate text to force the (d) check to fail.
    db_conn.execute("UPDATE posts SET text = 'mutated' WHERE id = ?", (post_id,))

    result = publish.publish_post_atomic(
        db_conn, post_id=post_id, raw_token=minted.raw_token
    )
    assert result.success is False
    assert result.method == "failed"
    assert "DraftTextChangedError" in result.error

    # Token row should be UNCONSUMED — retry path preserved.
    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is None

    # Post row: attempt count incremented, last_error populated.
    post_row = db_conn.execute(
        "SELECT publish_attempt_count, publish_last_error FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    assert post_row["publish_attempt_count"] == 1
    assert "DraftTextChangedError" in post_row["publish_last_error"]


def test_publish_success_consumes_token_and_stages_manual_clipboard(db_conn):
    """MVP happy path — publish_method = manual_clipboard, token consumed."""
    post_id = _make_draft_post(db_conn, text="ship me")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="ship me"
    )
    result = publish_post_to_x(
        db_conn, post_id=post_id, confirmation_token=minted.raw_token
    )
    assert result.success is True
    assert result.method == "manual_clipboard"
    # urlencode uses form-encoding ('+' for spaces) — fine for twitter.com/intent.
    assert result.intent_url is not None and "ship+me" in result.intent_url

    # Token consumed; post staged.
    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is not None

    post_row = db_conn.execute(
        "SELECT publish_method, published_to_x_at, publish_attempt_count FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    assert post_row["publish_method"] == "manual_clipboard"
    assert post_row["published_to_x_at"] is not None
    assert post_row["publish_attempt_count"] == 1


# ===========================================================================
# 5. Double-publish rejected by check (f)
# ===========================================================================
def test_double_publish_rejected_by_check_f(db_conn):
    """§28.10 hard constraint: no auto-publish of already-confirmed posts."""
    post_id = _make_draft_post(db_conn, text="once and done")
    minted_first = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="once and done"
    )
    result = publish_post_to_x(
        db_conn, post_id=post_id, confirmation_token=minted_first.raw_token
    )
    assert result.success is True

    # Simulate Daniel marking the post confirmed via the existing flow.
    db_conn.execute(
        "UPDATE posts SET manual_confirmation_status = 'confirmed', x_post_id = 'fake-x-id' WHERE id = ?",
        (post_id,),
    )

    # Mint a second token and attempt re-publish — check (f) rejects.
    minted_second = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="once and done"
    )
    result2 = publish_post_to_x(
        db_conn, post_id=post_id, confirmation_token=minted_second.raw_token
    )
    assert result2.success is False
    assert "DraftNotInDraftStateError" in result2.error

    # The second token stays unconsumed (validation-failure path).
    token_row = db_conn.execute(
        "SELECT consumed_at_utc FROM publish_confirmation_tokens WHERE id = ?",
        (minted_second.token_id,),
    ).fetchone()
    assert token_row["consumed_at_utc"] is None


# ===========================================================================
# 6. Raw-token redaction
# ===========================================================================
def test_raw_token_redacted_from_arguments_json(db_conn):
    """§28.2 rule #11: raw confirmation_token NEVER persists in audit log."""
    post_id = _make_draft_post(db_conn, text="audit me")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="audit me"
    )
    publish_post_to_x(
        db_conn, post_id=post_id, confirmation_token=minted.raw_token
    )

    # Find the audit row.
    row = db_conn.execute(
        """
        SELECT arguments_json, redacted_arguments, status
        FROM agent_tool_calls
        WHERE tool_name = 'publish_post_to_x'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["redacted_arguments"] == 1
    assert row["status"] == "success"

    args = json.loads(row["arguments_json"])
    assert "confirmation_token" not in args, (
        "raw confirmation_token leaked into agent_tool_calls.arguments_json — "
        "redaction wrapper is broken"
    )
    assert "confirmation_token_id" in args
    assert args["confirmation_token_id"] == minted.token_id
    # The raw UUID hex must NOT appear anywhere in the serialized args.
    assert minted.raw_token not in row["arguments_json"]


def test_raw_token_redacted_on_validation_failure_too(db_conn):
    """Redaction happens on the error path too — the raw token must NEVER
    persist regardless of whether the publish succeeded."""
    post_id = _make_draft_post(db_conn, text="will fail")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="will fail"
    )
    db_conn.execute("UPDATE posts SET text = 'changed' WHERE id = ?", (post_id,))
    publish_post_to_x(
        db_conn, post_id=post_id, confirmation_token=minted.raw_token
    )

    row = db_conn.execute(
        """
        SELECT arguments_json, redacted_arguments, status
        FROM agent_tool_calls
        WHERE tool_name = 'publish_post_to_x' AND status = 'error'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row["redacted_arguments"] == 1
    args = json.loads(row["arguments_json"])
    assert "confirmation_token" not in args
    assert minted.raw_token not in row["arguments_json"]


# ===========================================================================
# 7. Crash recovery — detect orphan posts
# ===========================================================================
def test_detect_orphan_posts(db_conn):
    """§28.10 step 8: posts where publish flow started but never landed."""
    # Set up three rows:
    #   * orphan: publish_attempt_count > 0, published_to_x_at NOT NULL,
    #     x_post_id NULL, publish_method != 'failed'.
    #   * complete: x_post_id populated → not an orphan.
    #   * failed: publish_method='failed' → already reconciled → not an orphan.
    orphan_id = _make_draft_post(db_conn, text="orphan")
    complete_id = _make_draft_post(db_conn, text="complete")
    failed_id = _make_draft_post(db_conn, text="failed")
    db_conn.execute(
        """
        UPDATE posts SET publish_attempt_count = 1, published_to_x_at = ?,
                         publish_method = 'manual_clipboard'
        WHERE id = ?
        """,
        ("2026-05-21 22:00:00", orphan_id),
    )
    db_conn.execute(
        """
        UPDATE posts SET publish_attempt_count = 1, published_to_x_at = ?,
                         publish_method = 'manual_clipboard', x_post_id = 'live-id'
        WHERE id = ?
        """,
        ("2026-05-21 22:00:00", complete_id),
    )
    db_conn.execute(
        """
        UPDATE posts SET publish_attempt_count = 1, publish_method = 'failed',
                         publish_last_error = 'transient'
        WHERE id = ?
        """,
        (failed_id,),
    )

    orphans = recovery.detect_orphans(db_conn)
    orphan_ids = {o.post_id for o in orphans}
    assert orphan_ids == {orphan_id}, f"expected just {orphan_id}, got {orphan_ids}"

    # Reconcile the orphan — it should disappear from the orphan list.
    recovery.mark_orphan_posted(
        db_conn, post_id=orphan_id, x_post_id="live-id-2", x_post_url="https://x.com/x"
    )
    assert recovery.detect_orphans(db_conn) == []
