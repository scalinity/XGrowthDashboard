"""Phase 5.9 / §28.18 — reply-quality lint.

Covers:

  1. Offline-mode pattern matchers catch the three failure modes
     (forced, AI-tasting, selfishly self-promoting).
  2. Substantive genuine reply passes the offline matcher.
  3. enabled=False short-circuits to passed=True with failure_mode=
     'lint_disabled' (regardless of input).
  4. _parse_reply_quality_response maps the four Haiku verdicts to the
     right ReplyQualityResult shape.
  5. session.decide_save_or_revise — reply drafts with forced text
     bounce as 'revise' even with perfect IWH; standalone drafts skip
     the reply-quality lint; reply_quality_lint_passed surfaces on the
     Decision.
  6. _save_draft_reply persists agent_drafts.reply_quality_lint_passed
     when the dispatcher injects it; NULL when nothing was injected
     (back-compat).
  7. Dispatcher integration — a forced reply through
     dispatch_tool_call returns status='revise_required' and doesn't
     write to agent_drafts; a genuine reply (with valid IWH + niche +
     content_type) lands.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.agent import lint
from app.agent import niche as _niche
from app.agent import session
from app.agent.client import dispatch_tool_call
from app.agent.tools import _save_draft_reply


# ---------------------------------------------------------------------------
# Offline-mode pattern matchers.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected_mode"),
    [
        ("Great post! 🔥 Check out my stuff at example.com", "selfishly_self_promoting"),
        ("Stop by my site for more thoughts on this.", "selfishly_self_promoting"),
        # Phase 10 W6 — multi-emoji decoration is now caught by the
        # more-specific emoji_as_personality category (it used to fall
        # through to the legacy "emoji-led affirmation" forced pattern).
        ("Absolute banger! 🔥🔥", "emoji_as_personality"),
        ("This.", "forced"),
        ("As an AI, I think this is interesting.", "ai_tasting"),
        ("Let me know if you'd like me to expand on that.", "ai_tasting"),
    ],
)
def test_offline_catches_failure_modes(text: str, expected_mode: str) -> None:
    result = lint._offline_reply_quality(text)
    assert result.passed is False
    assert result.failure_mode == expected_mode


def test_offline_passes_substantive_reply() -> None:
    text = (
        "The schema-grounded approach changes the failure mode — instead of "
        "the model hallucinating ingredients you get a clear 'no match' "
        "signal you can route on."
    )
    result = lint._offline_reply_quality(text)
    assert result.passed is True
    assert result.failure_mode is None


# ---------------------------------------------------------------------------
# enabled=False short-circuit.
# ---------------------------------------------------------------------------
def test_disabled_short_circuits_to_pass() -> None:
    result = lint.reply_quality_lint(
        "Great post! 🔥 Check out my stuff", target_post_text="x",
        enabled=False,
    )
    assert result.passed is True
    assert result.failure_mode == "lint_disabled"
    assert result.rationale == "lint disabled"


def test_is_reply_quality_lint_enabled_default_true() -> None:
    assert lint.is_reply_quality_lint_enabled(None) is True
    assert lint.is_reply_quality_lint_enabled("true") is True
    assert lint.is_reply_quality_lint_enabled("false") is False
    # Malformed JSON → conservative default = True (so the lint runs).
    assert lint.is_reply_quality_lint_enabled("nope") is True


# ---------------------------------------------------------------------------
# Haiku response parser.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "expected_passed", "expected_mode"),
    [
        ("no, this is genuine and substantive — addresses the OP's point", True, None),
        ("yes, forced — clearly a hollow affirmation", False, "forced"),
        ("yes, AI-tasting — opens with 'as an AI'", False, "ai_tasting"),
        (
            "yes, selfishly self-promoting — closes with a self-link CTA",
            False,
            "selfishly_self_promoting",
        ),
    ],
)
def test_parse_response_maps_four_verdicts(
    body: str, expected_passed: bool, expected_mode: str | None
) -> None:
    result = lint._parse_reply_quality_response(body)
    assert result.passed is expected_passed
    assert result.failure_mode == expected_mode


def test_parse_unparseable_defaults_to_pass() -> None:
    result = lint._parse_reply_quality_response("uhh I'm not sure")
    # Defensive default — outages don't block legitimate replies.
    assert result.passed is True
    assert result.failure_mode is None


# ---------------------------------------------------------------------------
# session.decide_save_or_revise integration.
# ---------------------------------------------------------------------------
_PERFECT_IWH = '<iwh_self_score>{"intelligence": 3, "wisdom": 3, "humility": 3}</iwh_self_score>'


def test_decide_skips_reply_quality_for_standalone(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    """draft_kind='standalone' must NOT run the reply-quality lint."""
    monkeypatch.setenv("LINT_OFFLINE", "1")
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=_PERFECT_IWH,
        draft_text="Great post! 🔥 Check out my stuff",  # would FAIL reply quality
        current_attempt_index=1,
        draft_kind="standalone",
    )
    assert decision.action == "save"
    assert decision.reply_quality_result is None


def test_decide_runs_reply_quality_for_reply(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    monkeypatch.setenv("LINT_OFFLINE", "1")
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=_PERFECT_IWH,
        draft_text="Great post! 🔥 Check out my stuff at example.com",
        current_attempt_index=1,
        draft_kind="reply",
        target_post_text="A thoughtful post about LLM evals.",
    )
    assert decision.action == "revise"
    assert decision.reply_quality_result is not None
    assert decision.reply_quality_result.passed is False
    assert "reply-quality lint" in decision.rationale


def test_decide_reply_quality_passes_substantive(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    monkeypatch.setenv("LINT_OFFLINE", "1")
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=_PERFECT_IWH,
        draft_text=(
            "The schema-grounded retrieval approach changes the failure mode — "
            "instead of hallucinated ingredients you get a clean 'no match'."
        ),
        current_attempt_index=1,
        draft_kind="reply",
        target_post_text="A post about LLM groundedness.",
    )
    assert decision.action == "save"
    assert decision.reply_quality_result is not None
    assert decision.reply_quality_result.passed is True


def test_decide_respects_setting_toggle(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    """When reply_quality_lint_enabled=false the lint short-circuits to pass."""
    monkeypatch.setenv("LINT_OFFLINE", "1")
    db_conn.execute(
        "UPDATE settings SET value_json = 'false' WHERE key = ?",
        ("reply_quality_lint_enabled",),
    )
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=_PERFECT_IWH,
        draft_text="Great post! 🔥 Check out my stuff",  # would FAIL if enabled
        current_attempt_index=1,
        draft_kind="reply",
        target_post_text="x",
    )
    assert decision.action == "save"
    assert decision.reply_quality_result is not None
    assert decision.reply_quality_result.failure_mode == "lint_disabled"


# ---------------------------------------------------------------------------
# Handler persistence — _save_draft_reply writes reply_quality_lint_passed.
# ---------------------------------------------------------------------------
def test_save_draft_reply_persists_passed_flag(
    db_conn: sqlite3.Connection,
) -> None:
    _niche.set_niche(db_conn, problem="x", person="y")
    out = _save_draft_reply(
        db_conn,
        text="A substantive reply.",
        target_post_url="https://x.com/foo/status/1",
        content_type="value",
        reply_quality_lint_passed=True,
    )
    row = db_conn.execute(
        "SELECT reply_quality_lint_passed FROM agent_drafts WHERE id = ?",
        (out["draft_id"],),
    ).fetchone()
    assert row["reply_quality_lint_passed"] == 1


def test_save_draft_reply_persists_failed_flag(
    db_conn: sqlite3.Connection,
) -> None:
    _niche.set_niche(db_conn, problem="x", person="y")
    out = _save_draft_reply(
        db_conn,
        text="reply text",
        target_post_url="https://x.com/foo/status/2",
        content_type="value",
        reply_quality_lint_passed=False,
    )
    row = db_conn.execute(
        "SELECT reply_quality_lint_passed FROM agent_drafts WHERE id = ?",
        (out["draft_id"],),
    ).fetchone()
    assert row["reply_quality_lint_passed"] == 0


def test_save_draft_reply_defaults_to_null_when_not_injected(
    db_conn: sqlite3.Connection,
) -> None:
    """Direct handler call without the dispatcher → field stays NULL."""
    _niche.set_niche(db_conn, problem="x", person="y")
    out = _save_draft_reply(
        db_conn,
        text="reply",
        target_post_url="https://x.com/foo/status/3",
        content_type="value",
    )
    row = db_conn.execute(
        "SELECT reply_quality_lint_passed FROM agent_drafts WHERE id = ?",
        (out["draft_id"],),
    ).fetchone()
    assert row["reply_quality_lint_passed"] is None


# ---------------------------------------------------------------------------
# Dispatcher end-to-end — the §28.18 acceptance test from the kickoff.
# ---------------------------------------------------------------------------
def _seed_msg(conn: sqlite3.Connection) -> int:
    conv = conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active') RETURNING id"
    ).fetchone()[0]
    return int(conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) "
        "VALUES (?, 'assistant', '') RETURNING id",
        (conv,),
    ).fetchone()[0])


def test_dispatcher_bounces_forced_reply_via_quality_lint(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    """The §28.18 acceptance test: synthetic forced reply bounces with
    status='revise_required'; no agent_drafts row lands; the audit row
    surfaces the reply-quality failure mode in rationale."""
    monkeypatch.setenv("LINT_OFFLINE", "1")
    _niche.set_niche(db_conn, problem="x", person="y")
    msg_id = _seed_msg(db_conn)
    result = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "Great post! 🔥 Check out my stuff at example.com",
            "target_post_url": "https://x.com/foo/status/123",
            "target_post_text": "Thoughtful evals piece.",
            "content_type": "value",
            "reply_intent": "growth",  # Phase 10 §29.5 dispatcher gate.
        },
        message_id=msg_id,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert result["status"] == "revise_required"
    assert "reply-quality lint" in result["rationale"]
    n = db_conn.execute("SELECT COUNT(*) FROM agent_drafts").fetchone()[0]
    assert n == 0


def test_dispatcher_lands_substantive_reply(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    monkeypatch.setenv("LINT_OFFLINE", "1")
    _niche.set_niche(db_conn, problem="x", person="y")
    msg_id = _seed_msg(db_conn)
    result = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": (
                "The schema-grounded retrieval approach changes the failure "
                "mode — hallucinated ingredients become a clean 'no match'."
            ),
            "target_post_url": "https://x.com/foo/status/124",
            "target_post_text": "Thoughtful evals piece.",
            "content_type": "value",
            "reply_intent": "icp_discovery",  # Phase 10 §29.5 dispatcher gate.
        },
        message_id=msg_id,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert result["status"] == "success", result
    rqlp = db_conn.execute(
        "SELECT reply_quality_lint_passed FROM agent_drafts ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert rqlp == 1


def test_dispatcher_toggle_off_lands_forced_reply(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    """With reply_quality_lint_enabled=false a 'forced' reply still lands
    (the IWH/dark-pattern gates pass); the persisted flag carries the
    disabled-state signal (passed=True per the short-circuit contract)."""
    monkeypatch.setenv("LINT_OFFLINE", "1")
    _niche.set_niche(db_conn, problem="x", person="y")
    db_conn.execute(
        "UPDATE settings SET value_json = 'false' WHERE key = ?",
        ("reply_quality_lint_enabled",),
    )
    msg_id = _seed_msg(db_conn)
    result = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "Great post! 🔥",
            "target_post_url": "https://x.com/foo/status/125",
            "target_post_text": "x",
            "content_type": "value",
            "reply_intent": "relationship",  # Phase 10 §29.5 dispatcher gate.
        },
        message_id=msg_id,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    # No dark-pattern hit and lint disabled → save path.
    assert result["status"] == "success", result
    rqlp = db_conn.execute(
        "SELECT reply_quality_lint_passed FROM agent_drafts ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert rqlp == 1
