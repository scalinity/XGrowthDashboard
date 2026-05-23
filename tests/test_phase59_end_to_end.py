"""Phase 5.9 end-to-end happy path (§25 Phase 5.9 acceptance gate).

Drives all six features through their orchestrator entry points
against a single fresh DB:

  1. Niche unset → save_draft_post refuses via §28.2 rule #15 gate.
  2. Set niche → save personality draft with lore match → assert
     orchestrator scans + increments counters (§28.21).
  3. Save a forced reply → assert reply-quality lint fails + IWH
     revise (§28.18).
  4. Velocity panel suppresses projections when |delta_7d| in noise
     floor; surfaces a projection when above (§28.19).
  5. Paste a replier pool → candidates land with
     source='replier_under_thread' (§28.20).
  6. Content-type axis: get_content_type_gaps surfaces the under-
     represented type from the seeded posts (§28.17).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.agent import niche as _niche
from app.agent import personality_lore
from app.agent import replier_pool
from app.agent import velocity
from app.agent.client import dispatch_tool_call
from app.agent.content_types import get_content_type_gaps


_IWH_PASS = (
    '<iwh_self_score>{"intelligence": 3, "wisdom": 3, "humility": 3}</iwh_self_score>'
)


def _seed_msg(conn: sqlite3.Connection) -> int:
    conv = conn.execute(
        "INSERT INTO agent_conversations (status) VALUES ('active') RETURNING id"
    ).fetchone()[0]
    return int(conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) "
        "VALUES (?, 'assistant', '') RETURNING id",
        (conv,),
    ).fetchone()[0])


def _seed_snapshot(conn, *, snapshot_date, followers_count) -> None:
    conn.execute(
        """
        INSERT INTO account_snapshots
          (snapshot_date, collected_at_utc, username, profile_url,
           source, data_quality,
           followers_count, following_count, post_count, listed_count,
           baseline_followers)
        VALUES (?, ?, 'dannyscalant', 'https://x.com/dannyscalant',
                'manual', 'manual', ?, 100, 50, 0, 61)
        """,
        (snapshot_date, snapshot_date + "T09:00:00Z", followers_count),
    )


def test_phase59_full_happy_path(
    db_conn: sqlite3.Connection, monkeypatch,
) -> None:
    monkeypatch.setenv("LINT_OFFLINE", "1")

    # ------------------------------------------------------------------
    # (1) Niche-unset → save_draft_post refuses via rule #15.
    # ------------------------------------------------------------------
    msg = _seed_msg(db_conn)
    refused = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_post",
        tool_input={
            "text": "Specific takeaway.",
            "pillar": "build",
            "audience": "icp",
            "cta": "none",
            "content_type": "value",
        },
        message_id=msg,
        assistant_text=_IWH_PASS,
        current_attempt_index=1,
    )
    assert refused["status"] == "error"
    assert "niche gate" in refused["error"].lower()
    assert db_conn.execute(
        "SELECT COUNT(*) FROM agent_drafts"
    ).fetchone()[0] == 0

    # ------------------------------------------------------------------
    # (2) Set niche; seed lore; save personality draft → counters bump.
    # ------------------------------------------------------------------
    _niche.set_niche(
        db_conn,
        problem="how to grow on X without dark patterns",
        person="educational creators",
    )
    lore_id = personality_lore.add(
        db_conn,
        theme="kitchen-scanner fail",
        description="the time the scanner read ginger as soap",
    )
    msg2 = _seed_msg(db_conn)
    saved_personality = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_post",
        tool_input={
            "text": "Day 4 of the kitchen-scanner fail — ginger as soap, again.",
            "pillar": "self",
            "audience": "other",
            "cta": "none",
            "content_type": "personality",
        },
        message_id=msg2,
        assistant_text=_IWH_PASS,
        current_attempt_index=1,
    )
    assert saved_personality["status"] == "success", saved_personality
    # The scan increments invocation_count.
    row = next(
        r for r in personality_lore.list_all(db_conn) if r.id == lore_id
    )
    assert row.invocation_count == 1
    assert row.last_invoked_at_utc is not None

    # ------------------------------------------------------------------
    # (3) Forced reply → reply-quality lint fails + IWH revise.
    # ------------------------------------------------------------------
    msg3 = _seed_msg(db_conn)
    forced = dispatch_tool_call(
        db_conn,
        tool_name="save_draft_reply",
        tool_input={
            "text": "Great post! 🔥 Check out my stuff at example.com",
            "target_post_url": "https://x.com/foo/status/9999",
            "target_post_text": "A thoughtful piece on LLM evals.",
            "content_type": "value",
            "reply_intent": "growth",  # Phase 10 §29.5 dispatcher gate.
        },
        message_id=msg3,
        assistant_text=_IWH_PASS,
        current_attempt_index=1,
    )
    assert forced["status"] == "revise_required"
    assert "reply-quality lint" in forced["rationale"]
    # No reply draft landed.
    n_replies = db_conn.execute(
        "SELECT COUNT(*) FROM agent_drafts WHERE draft_kind = 'reply'"
    ).fetchone()[0]
    assert n_replies == 0

    # ------------------------------------------------------------------
    # (4) Velocity panel suppresses projections in the noise floor.
    # ------------------------------------------------------------------
    # Seed 8 daily snapshots at +1/day (delta_7d = 7, below floor=10).
    for days_back in range(8):
        d = (date.today() - timedelta(days=days_back)).isoformat()
        _seed_snapshot(
            db_conn,
            snapshot_date=d,
            followers_count=100 + (7 - days_back) * 1,
        )
    proj_noise = velocity.get_velocity_projection(db_conn)
    assert proj_noise is not None
    assert proj_noise.in_noise_floor is True
    assert proj_noise.projected_milestone_hit_date_at_7d_pace is None

    # Replace with +3/day so |delta_7d|=21 > 10 → projection materializes.
    # Bump milestone to 500 so the seeded followers haven't already met it.
    db_conn.execute("DELETE FROM account_snapshots")
    db_conn.execute(
        "UPDATE settings SET value_json = '500' WHERE key = ?",
        ("current_milestone",),
    )
    for days_back in range(8):
        d = (date.today() - timedelta(days=days_back)).isoformat()
        _seed_snapshot(
            db_conn,
            snapshot_date=d,
            followers_count=100 + (7 - days_back) * 3,
        )
    proj_real = velocity.get_velocity_projection(db_conn)
    assert proj_real is not None
    assert proj_real.in_noise_floor is False
    assert proj_real.projected_milestone_hit_date_at_7d_pace is not None

    # ------------------------------------------------------------------
    # (5) Paste a replier pool → candidates land.
    # ------------------------------------------------------------------
    pool_result = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/bigaccount/status/77",
        replier_handles_or_excerpts=(
            "@alpha: educational creators are exactly who I help\n"
            "@beta: completely unrelated take"
        ),
    )
    assert "error" not in pool_result
    assert pool_result["created_count"] == 2
    sources = {
        r["source"] for r in db_conn.execute(
            "SELECT source FROM reply_targets WHERE source = 'replier_under_thread'"
        ).fetchall()
    }
    assert sources == {"replier_under_thread"}

    # ------------------------------------------------------------------
    # (6) Content-type gap surfaces an under-represented type.
    # ------------------------------------------------------------------
    # The personality post above + the seed posts elsewhere should give
    # us a non-uniform distribution. Add a couple of value posts to make
    # the gap explicit.
    today_iso = date.today().isoformat()
    for _ in range(3):
        db_conn.execute(
            """
            INSERT INTO posts (created_date, text, type, posted_via,
                               manual_confirmation_status, content_type)
            VALUES (?, 'x', 'standalone', 'manual', 'confirmed', 'value')
            """,
            (today_iso,),
        )
    gap = get_content_type_gaps(db_conn, window_days=7)
    # Of (value, growth, personality, proof), 'growth' and 'proof' both
    # have 0 — canonical order picks 'growth'.
    assert gap["under_represented"] == "growth"
    assert gap["counts"]["value"] >= 3
    assert gap["counts"]["personality"] >= 1
