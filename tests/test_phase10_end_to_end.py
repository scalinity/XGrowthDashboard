"""Phase 10 — Voice Discipline Polish Pack end-to-end happy path.

Drives the five sub-features through the dispatcher in one pass:

  (a) A standalone draft goes through the scorer with all 10 dimensions
      populated (screenshot test injected via the test caller seam).
  (b) A reply with the new 'engagement_bait' failure mode bounces via
      §28.18 reply-quality lint AND the failure_mode label is in the
      revise rationale.
  (c) A reply attempt without reply_intent gets refused by the §29.5
      Phase 10 dispatcher gate.
  (d) The reply_intent_required = false toggle restores the pre-Phase-10
      pass-through behavior.
  (e) A genuine reply with valid reply_intent lands and persists.
"""

from __future__ import annotations

import sqlite3

from app.agent import prepublish_scorer as ps
from app.agent import niche as _niche
from app.agent.client import dispatch_tool_call
from app.agent.tools import _save_draft_post


_PERFECT_IWH = '<iwh_self_score>{"intelligence": 3, "wisdom": 3, "humility": 3}</iwh_self_score>'


def _seed_msg(conn: sqlite3.Connection) -> int:
    conv = conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active') RETURNING id"
    ).fetchone()[0]
    return int(conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) "
        "VALUES (?, 'assistant', '') RETURNING id",
        (conv,),
    ).fetchone()[0])


def test_phase10_end_to_end_happy_path(
    db_conn: sqlite3.Connection, monkeypatch
) -> None:
    monkeypatch.setenv("LINT_OFFLINE", "1")
    _niche.set_niche(db_conn, problem="x", person="y")

    # ------------------------------------------------------------------
    # (a) Standalone draft → scorer populates all 10 dimensions.
    # ------------------------------------------------------------------
    # We bypass the dispatcher and call _save_draft_post directly so we
    # can use the screenshot_test_caller seam (the dispatcher path uses
    # the real Haiku call; in LINT_OFFLINE mode the screenshot dim is
    # NULL by design).
    _save_draft_post(
        db_conn,
        text="Three failed dinner attempts before 7pm. Stir scanned the fridge.",
        pillar="stir", audience="icp", cta="none", content_type="value",
    )
    # Manually re-score with a richer text via score() + caller to
    # confirm the 10th dim populates end-to-end. Length needs to land
    # near the 200-char target band to clear length_fit_score>=2.
    rich_text = (
        "Three failed dinner attempts before 7pm.\n"
        "Stir scanned the fridge, suggested 3 cookable options, and "
        "the working parent texted me a photo of the meal.\n"
        "Shipped the iOS build today."
    )
    standalone_row = ps.score(
        draft_text=rich_text,
        draft_kind="standalone",
        pillar="stir", cta="none",
        target_post_text=None,
        active_voice_profile=None,
        conn=db_conn,
        screenshot_test_caller=lambda d, p: 3,
    )
    assert standalone_row.screenshot_test_score == 3
    # The 10th dim populates the row; the composite_label reflects the
    # combination — at least 'viable' since all nine deterministic dims
    # score well on this rich text.
    assert standalone_row.composite_label in {"viable", "strong"}, (
        f"got {standalone_row.composite_label} for rich_text: {standalone_row}"
    )

    # ------------------------------------------------------------------
    # (b) Reply with a new failure-mode category bounces via §28.18.
    # ------------------------------------------------------------------
    msg1 = _seed_msg(db_conn)
    bounced = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "5 secrets nobody tells you about React — number 3 will shock you",
            "target_post_url": "https://x.com/foo/status/300",
            "target_post_text": "Thoughtful piece on React.",
            "content_type": "value",
            "reply_intent": "growth",  # valid intent → reaches lint gate
        },
        message_id=msg1,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert bounced["status"] == "revise_required", bounced
    # New failure-mode label surfaces in the rationale.
    assert "engagement_bait" in bounced["rationale"]
    # No draft landed.
    n_drafts = db_conn.execute(
        "SELECT COUNT(*) FROM agent_drafts WHERE draft_kind = 'reply'"
    ).fetchone()[0]
    assert n_drafts == 0

    # ------------------------------------------------------------------
    # (c) Reply without reply_intent → §29.5 Phase 10 gate refuses.
    # ------------------------------------------------------------------
    msg2 = _seed_msg(db_conn)
    no_intent = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "A substantive reply addressing the OP.",
            "target_post_url": "https://x.com/foo/status/301",
            "target_post_text": "x",
            "content_type": "value",
            # reply_intent intentionally missing.
        },
        message_id=msg2,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert no_intent["status"] == "error"
    assert "reply-intent gate" in no_intent["error"]

    # ------------------------------------------------------------------
    # (d) Toggle reply_intent_required = false → pass-through restored.
    # ------------------------------------------------------------------
    db_conn.execute(
        "UPDATE settings SET value_json = 'false' WHERE key = ?",
        ("reply_intent_required",),
    )
    msg3 = _seed_msg(db_conn)
    toggle_off = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "A substantive reply addressing the OP.",
            "target_post_url": "https://x.com/foo/status/302",
            "target_post_text": "x",
            "content_type": "value",
            # reply_intent still missing — but toggle is off, so OK.
        },
        message_id=msg3,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert toggle_off["status"] == "success", toggle_off

    # Restore toggle for the next step.
    db_conn.execute(
        "UPDATE settings SET value_json = 'true' WHERE key = ?",
        ("reply_intent_required",),
    )

    # ------------------------------------------------------------------
    # (e) Genuine reply with valid intent → lands; failure_mode is NULL.
    # ------------------------------------------------------------------
    msg4 = _seed_msg(db_conn)
    genuine = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": (
                "The schema-grounded retrieval approach changes the failure "
                "mode — hallucinated ingredients become a clean 'no match'."
            ),
            "target_post_url": "https://x.com/foo/status/303",
            "target_post_text": "Thoughtful evals piece.",
            "content_type": "value",
            "reply_intent": "relationship",
        },
        message_id=msg4,
        assistant_text=_PERFECT_IWH,
        current_attempt_index=1,
    )
    assert genuine["status"] == "success", genuine

    # Check the persisted columns.
    row = db_conn.execute(
        """
        SELECT reply_quality_lint_passed,
               reply_quality_lint_failure_mode
          FROM agent_drafts
         WHERE draft_kind = 'reply'
         ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert row["reply_quality_lint_passed"] == 1
    assert row["reply_quality_lint_failure_mode"] is None
