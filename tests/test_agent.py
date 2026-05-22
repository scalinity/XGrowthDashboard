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
        db_conn, text="v1", pillar="stir", audience="icp", cta="ask",
        content_type="value",
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


def test_every_agent_tool_handler_executes_against_fresh_db(db_conn):
    """C3 regression: every AGENT_TOOL handler must execute against a
    fresh-migration DB without crashing.

    Before C3, `_get_open_hypotheses` SELECTed columns that didn't exist
    on the `experiments` table — it crashed sqlite3 the first time the
    model invoked it. No test covered this. The fix: run each handler
    with a representative minimal kwargs set; assert no exception and a
    dict result.
    """
    # Minimal kwargs sets that satisfy each tool's required schema.
    sample_kwargs = {
        "query_dashboard_state": {"slice": "all"},
        "get_recent_posts": {"days_back": 7, "limit": 5},
        "get_lane_performance": {},
        "get_open_hypotheses": {},
        "get_lane_gaps": {"week_offset": 0},
        "analyze_post": {"post_id": 1},  # may return error dict; that's fine
        "summarize_winners": {"window_days": 30},
        "find_reply_targets": {"count": 3, "recency_hours": 48},
        "score_reply_candidates": {"candidates": []},
        "extract_lesson": {"post_id": 1},
        "draft_weekly_review_section": {
            "section_name": "interpretation", "week_id": 1
        },
        "save_draft_post": {
            "text": "test draft", "pillar": "stir",
            "audience": "icp", "cta": "ask",
            "content_type": "value",  # Phase 5.9 / §28.17 required
        },
        "save_draft_reply": {
            "text": "test reply",
            "target_post_url": "https://x.com/foo/status/123",
            "content_type": "value",  # Phase 5.9 / §28.17 required
        },
        "revise_draft": {
            # draft_post_id filled in below after save_draft_post runs.
            "feedback": "tighter", "new_text": "test draft v2",
        },
        "record_reply_target": {
            "target_post_url": "https://x.com/foo/status/123"
        },
        # Phase 5.9 / §28.17 — new read tool.
        "get_content_type_gaps": {"window_days": 7},
        # Phase 5.9 / §28.19 — velocity projection read.
        "get_velocity_projection": {},
        # Phase 5.9 / §28.20 — replier-pool discovery. Niche unset on a
        # fresh DB → handler returns the documented error dict (still a
        # valid dict, satisfies the smoke test contract).
        "score_replier_pool": {
            "thread_url": "https://x.com/foo/status/1",
            "replier_handles_or_excerpts_json": "@bar: aligned text\n@baz",
        },
        # Phase 5.10 / §28.22 — Brain Dump processing. brain_dump_id is
        # filled in below after a brain_dumps row is created. The handler
        # surfaces failure as a dict (not an exception) so the smoke test
        # holds even when ANTHROPIC_API_KEY is absent in CI.
        "process_brain_dump": {"brain_dump_id": None},
        # Phase 5.10 / §28.24 — Account Researcher. Handler surfaces
        # AccountResearchError as {"status": "failed"} so the smoke test
        # passes without ANTHROPIC_API_KEY.
        "analyze_account": {
            "target_handle": "@smoke_target",
            "target_recent_posts_text": "smoke post\n---\nsmoke post 2",
        },
        # Phase 5.10 / §28.25 — Profile Audit. Handler surfaces
        # ProfileAuditError as {"status": "failed"} so the smoke test
        # passes without ANTHROPIC_API_KEY.
        "audit_profile": {
            "bio_text": "smoke bio",
            "pinned_post_text": "smoke pinned post",
        },
        # Phase 5.11 / §28.26 — Campaigns analyzer. campaign_id filled
        # in below after a dual-stream campaign is seeded. Read-only.
        "analyze_campaign_progress": {"campaign_id": None},
        # Phase 5.11 / §28.27 — Monthly review draft tool. Returns a
        # Session-1 stub dict; no API call needed.
        "draft_monthly_review_section": {
            "section_name": "interpretation",
            "iso_month": "2026-05",
        },
        # Phase 5.11 / §28.29 — Inspiration transform tool. saved_inspiration_id
        # filled in below; surfaces TransformError as {"status": "failed"}
        # so the smoke test holds without ANTHROPIC_API_KEY.
        "transform_inspiration": {
            "saved_inspiration_id": None,
            "mode": "structure",
        },
        # Phase 5.11 / §28.29 — Pure deterministic plagiarism read.
        "score_inspiration_plagiarism_risk": {
            "source_text": "the quick brown fox jumps over the lazy dog",
            "output_text": "a slow red cat hides under the agile mouse",
        },
        # Phase 6 / §28.32 — Four blog drafting tools. blog_id filled in
        # below. Niche is unset on a fresh DB → handlers return
        # {"status": "failed"} (BlogDraftingNicheUndefinedError → dict),
        # which still satisfies the smoke contract.
        "outline_blog": {"blog_id": None},
        "draft_blog": {"blog_id": None},
        "suggest_blog_edits": {"blog_id": None},
        "generate_blog_seo_metadata": {"blog_id": None},
    }
    # Seed a brain_dumps row for the smoke test invocation.
    from app.agent import brain_dump as _brain_dump
    sample_kwargs["process_brain_dump"]["brain_dump_id"] = _brain_dump.create_dump(
        db_conn, raw_text="smoke-test dump"
    )
    # Seed a campaign so analyze_campaign_progress has something to
    # read. The dual-stream success criteria are required by §28.26.
    from app.agent import campaigns as _campaigns
    sample_kwargs["analyze_campaign_progress"]["campaign_id"] = (
        _campaigns.create_campaign(
            db_conn,
            name="smoke campaign",
            theme="t",
            hypothesis="h",
            start_date="2026-05-01",
            end_date="2026-05-28",
            success_criteria={
                "distribution": [{"metric": "impressions", "target": "10000"}],
                "validation": [{"metric": "downloads", "target": "5"}],
            },
        )
    )
    # Seed an inspiration so transform_inspiration has a row to target.
    from app.agent import inspiration as _inspiration
    sample_kwargs["transform_inspiration"]["saved_inspiration_id"] = (
        _inspiration.save_inspiration(
            db_conn,
            source_post_text="smoke source text for agent tool registry",
        )
    )

    # Seed a blog so the four Phase 6 tools have something to address.
    # Niche is empty on the fresh DB so each handler returns
    # {"status": "failed", "error": "...niche..."}.
    from app.agent import blogs as _blogs
    _smoke_blog = _blogs.create_blog(db_conn, title="smoke blog")
    for _t in ("outline_blog", "draft_blog", "suggest_blog_edits",
               "generate_blog_seo_metadata"):
        sample_kwargs[_t]["blog_id"] = _smoke_blog.id

    from app.agent.tools import AGENT_TOOLS
    saved_draft_id: int | None = None
    for tool in AGENT_TOOLS:
        kwargs = dict(sample_kwargs[tool.name])
        if tool.name == "revise_draft":
            assert saved_draft_id is not None, (
                "save_draft_post must run before revise_draft in test order"
            )
            kwargs["draft_post_id"] = saved_draft_id
        result = tool.handler(db_conn, **kwargs)
        assert isinstance(result, dict), (
            f"{tool.name} must return a dict; got {type(result).__name__}"
        )
        if tool.name == "save_draft_post":
            saved_draft_id = result["draft_id"]


def test_dispatch_tool_call_refuses_save_draft_with_low_iwh(db_conn, monkeypatch):
    """C5 regression: the IWH+lint gate must run inside dispatch_tool_call
    before save_draft_* handlers, not only when tests call decide_save_or_
    revise directly. Before C5, the gate was dead code in production —
    save_draft_post ran unconditionally on any tool_use the model emitted.
    """
    monkeypatch.setenv("LINT_OFFLINE", "1")
    from app.agent.client import dispatch_tool_call
    # Phase 5.9 / §28.2 rule #15 — niche must be defined for the dispatcher
    # to reach the IWH gate this test is exercising.
    from app.agent import niche as _niche
    _niche.set_niche(db_conn, problem="growing on X", person="builders")

    # Bootstrap a conversation + message to anchor the audit row.
    conv = db_conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active')"
    ).lastrowid
    msg = db_conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) VALUES (?, 'assistant', '')",
        (conv,),
    ).lastrowid

    # Assistant text emits a low IWH score (intelligence=1 < minimum=2).
    assistant_text = (
        'Here is a draft: "x" '
        '<iwh_self_score>{"intelligence": 1, "wisdom": 3, "humility": 3}</iwh_self_score>'
    )
    result = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_post",
        tool_input={
            "text": "a generic draft", "pillar": "stir",
            "audience": "icp", "cta": "ask",
        },
        message_id=int(msg),
        assistant_text=assistant_text,
        current_attempt_index=1,
    )
    # The gate refused the call (action=revise → status=revise_required).
    assert result["status"] == "revise_required"
    assert "below minimum" in result["rationale"]

    # Critically: no agent_drafts row was written.
    drafts = db_conn.execute(
        "SELECT COUNT(*) AS n FROM agent_drafts WHERE conversation_id = ?",
        (conv,),
    ).fetchone()
    assert drafts["n"] == 0, "save_draft_post must NOT have written a row"

    # And the audit row records the IWH refusal with notes='iwh-gate revise'.
    audit_row = db_conn.execute(
        "SELECT status, error_message, notes FROM agent_tool_calls "
        "WHERE message_id = ? ORDER BY id DESC LIMIT 1",
        (int(msg),),
    ).fetchone()
    assert audit_row["status"] == "error"
    assert "IWH revise" in audit_row["error_message"]


def test_dispatch_tool_call_blocks_engagement_bait_via_lint(db_conn, monkeypatch):
    """C5 regression: even with a perfect IWH score, the lint pass blocks
    engagement-bait drafts."""
    monkeypatch.setenv("LINT_OFFLINE", "1")
    from app.agent.client import dispatch_tool_call
    # Phase 5.9 / §28.2 rule #15 — niche must be defined for the dispatcher
    # to reach the lint gate this test is exercising.
    from app.agent import niche as _niche
    _niche.set_niche(db_conn, problem="growing on X", person="builders")

    conv = db_conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active')"
    ).lastrowid
    msg = db_conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) VALUES (?, 'assistant', '')",
        (conv,),
    ).lastrowid

    assistant_text = (
        '<iwh_self_score>{"intelligence": 3, "wisdom": 3, "humility": 3}</iwh_self_score>'
    )
    result = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_post",
        tool_input={
            "text": "5 secrets parents don't know — number 3 will surprise you!",
            "pillar": "stir", "audience": "icp", "cta": "ask",
        },
        message_id=int(msg),
        assistant_text=assistant_text,
        current_attempt_index=1,
    )
    assert result["status"] == "revise_required"
    assert "dark-pattern" in result["rationale"]
    drafts = db_conn.execute(
        "SELECT COUNT(*) AS n FROM agent_drafts"
    ).fetchone()
    assert drafts["n"] == 0


def test_revised_drafts_are_publishable(db_conn):
    """C2 regression: every revise_draft must mint a posts row so the
    publish modal can find it via `WHERE agent_draft_id = ?`.

    Before C2, only first-attempt drafts had a linked posts row; revisions
    silently failed to publish because the click-handler raised "Internal:
    agent_drafts row has no linked posts row." This is the entire IWH
    revision flow — every draft past attempt 1 was unpublishable.
    """
    out = _save_draft_post(
        db_conn, text="v1", pillar="stir", audience="icp", cta="ask",
        content_type="value",
    )
    rev = _revise_draft(
        db_conn, draft_post_id=out["draft_id"], feedback="weak", new_text="v2"
    )
    # The revise tool MUST return a post_id and the row MUST exist.
    assert "post_id" in rev, "revise_draft did not return a post_id"
    post_row = db_conn.execute(
        "SELECT id, text, agent_draft_id, manual_confirmation_status FROM posts WHERE id = ?",
        (rev["post_id"],),
    ).fetchone()
    assert post_row is not None, "revise_draft did not mint a posts row"
    assert post_row["text"] == "v2"
    assert post_row["agent_draft_id"] == rev["new_draft_id"]
    assert post_row["manual_confirmation_status"] == "draft"

    # Daniel can now mint a token + publish — i.e. the modal's lookup
    # succeeds.
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=int(rev["post_id"]), draft_text="v2"
    )
    result = publish_post_to_x(
        db_conn,
        post_id=int(rev["post_id"]),
        confirmation_token=minted.raw_token,
    )
    assert result.success is True
    assert result.method == "manual_clipboard"


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


def test_detect_orphans_excludes_fresh_manual_clipboard_publishes(db_conn):
    """W1 regression: a manual_clipboard publish within the grace window
    is NOT an orphan — Daniel just hasn't pasted the URL yet via the
    existing Mark-posted form."""
    fresh_id = _make_draft_post(db_conn, text="just published")
    # Mark as freshly published — datetime('now') falls inside the
    # MANUAL_CLIPBOARD_GRACE_MINUTES window.
    db_conn.execute(
        """
        UPDATE posts SET publish_attempt_count = 1,
                         published_to_x_at = datetime('now'),
                         publish_method = 'manual_clipboard'
        WHERE id = ?
        """,
        (fresh_id,),
    )
    orphans = recovery.detect_orphans(db_conn)
    assert fresh_id not in {o.post_id for o in orphans}, (
        "fresh manual_clipboard publish must NOT show as orphan within the grace window"
    )
