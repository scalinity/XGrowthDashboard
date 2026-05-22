"""AGENT_TOOLS — the merged tool registry the Anthropic SDK sees (§28.4 + §25).

This is the tool catalog exposed to the model via
``messages.create(tools=[t.to_anthropic_spec() for t in AGENT_TOOLS])``.
The publish tools (``publish_post_to_x``, ``publish_reply_to_x``) are
deliberately absent — they live in ``app.agent._internal_tools`` and are
invoked only by the Streamlit click-handler (§28.4 internal-only tool
surface note + §28.2 rule #10).

Phase 5.5 Session 1 ships handlers as functional stubs:

* Read tools (#1–3, #4–7, #10–11): real SELECTs against existing views/tables.
* Save tools (#12–14): real INSERT/UPDATE flow because the IWH counter
  test relies on the side effect (incrementing
  ``agent_drafts.iwh_attempt_index``) regardless of what the model emits.
* ``score_reply_candidates`` (#9), ``record_reply_target`` (#15): stubs
  returning a "Phase 5.6 will fully wire" notice. The schema slot exists
  so the model can attempt the call; Session 2 wires the chat dispatcher
  to surface the stub response in the UI.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from app.db import transaction


@dataclass(frozen=True)
class ToolDef:
    """A registered agent tool — name, description, JSON schema, handler.

    The Anthropic SDK adapter serializes ``name``/``description``/``input_schema``
    into the model's tool catalog. The handler is invoked locally by
    ``app.agent.client`` (Session 2) when the model emits a ``tool_use``
    block.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def to_anthropic_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# 1. query_dashboard_state(slice)
# ---------------------------------------------------------------------------
def _query_dashboard_state(conn: sqlite3.Connection, *, slice: str = "all") -> dict[str, Any]:
    """Pull structured JSON of a dashboard slice. Read-only (§28.4 #1)."""
    if slice not in {"today", "next_rep", "weekly", "validation_status", "all"}:
        return {"error": f"unknown slice {slice!r}"}

    out: dict[str, Any] = {"slice": slice}
    if slice in ("today", "all"):
        rows = conn.execute(
            "SELECT * FROM v_daily_reps ORDER BY activity_date DESC LIMIT 1"
        ).fetchall()
        out["today"] = [dict(r) for r in rows]
    if slice in ("validation_status", "all"):
        rows = conn.execute(
            "SELECT * FROM v_funnel_daily ORDER BY funnel_date DESC LIMIT 7"
        ).fetchall()
        out["funnel_last_7"] = [dict(r) for r in rows]
    if slice in ("next_rep", "all"):
        out["lane_performance"] = [
            dict(r)
            for r in conn.execute("SELECT * FROM v_lane_performance").fetchall()
        ]
    if slice in ("weekly", "all"):
        rows = conn.execute(
            "SELECT * FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 7"
        ).fetchall()
        out["account_last_7"] = [dict(r) for r in rows]
    return out


# ---------------------------------------------------------------------------
# 2. get_recent_posts
# ---------------------------------------------------------------------------
def _get_recent_posts(
    conn: sqlite3.Connection,
    *,
    pillar: str | None = None,
    audience: str | None = None,
    cta: str | None = None,
    days_back: int = 7,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent posts with metrics + classifications (§28.4 #2)."""
    sql = """
        SELECT p.id, p.x_post_id, p.created_date, p.text, p.type,
               pc.pillar, pc.audience, pc.cta,
               m.like_count, m.reply_count, m.repost_count
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        LEFT JOIN v_post_latest_metrics m ON m.post_id = p.id
        WHERE p.created_date >= date('now', ?)
    """
    params: list[Any] = [f"-{int(days_back)} days"]
    if pillar:
        sql += " AND pc.pillar = ?"
        params.append(pillar)
    if audience:
        sql += " AND pc.audience = ?"
        params.append(audience)
    if cta:
        sql += " AND pc.cta = ?"
        params.append(cta)
    sql += " ORDER BY p.created_date DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    return {"posts": [dict(r) for r in rows], "count": len(rows)}


# ---------------------------------------------------------------------------
# 3. get_lane_performance
# ---------------------------------------------------------------------------
def _get_lane_performance(
    conn: sqlite3.Connection,
    *,
    pillar: str | None = None,
    audience: str | None = None,
    cta: str | None = None,
    window_days: int = 14,  # noqa: ARG001 — passed by model; view ignores at MVP
) -> dict[str, Any]:
    """Rows from v_lane_performance with confidence labels (§28.4 #3)."""
    sql = "SELECT * FROM v_lane_performance WHERE 1=1"
    params: list[Any] = []
    if pillar:
        sql += " AND pillar = ?"
        params.append(pillar)
    if audience:
        sql += " AND audience = ?"
        params.append(audience)
    if cta:
        sql += " AND cta = ?"
        params.append(cta)
    rows = conn.execute(sql, params).fetchall()
    return {"lanes": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 4. get_open_hypotheses (§25)
# ---------------------------------------------------------------------------
def _get_open_hypotheses(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, hypothesis_text, lane, status, expected_signal,
               started_at_utc
        FROM experiments
        WHERE status IN ('proposed', 'running')
        ORDER BY started_at_utc DESC NULLS LAST
        """
    ).fetchall()
    return {"experiments": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 5. get_lane_gaps (§25)
# ---------------------------------------------------------------------------
def _get_lane_gaps(conn: sqlite3.Connection, *, week_offset: int = 0) -> dict[str, Any]:
    """Lanes with zero posts this week — surface the missing distribution lanes."""
    rows = conn.execute(
        """
        WITH possible AS (
            SELECT 'stir' AS pillar UNION SELECT 'build' UNION SELECT 'self'
        ),
        lanes AS (
            SELECT p.pillar AS pillar, a.audience AS audience, c.cta AS cta
            FROM possible p
            CROSS JOIN (SELECT 'icp' AS audience UNION SELECT 'other') a
            CROSS JOIN (SELECT 'ask' AS cta UNION SELECT 'none') c
        ),
        recent_counts AS (
            SELECT pc.pillar, pc.audience, pc.cta, COUNT(*) AS post_count
            FROM posts po
            JOIN post_classifications pc ON pc.post_id = po.id
            WHERE po.created_date >= date('now', ?)
            GROUP BY pc.pillar, pc.audience, pc.cta
        )
        SELECT l.pillar, l.audience, l.cta,
               COALESCE(r.post_count, 0) AS post_count
        FROM lanes l
        LEFT JOIN recent_counts r
          ON r.pillar = l.pillar AND r.audience = l.audience AND r.cta = l.cta
        ORDER BY post_count ASC, l.pillar, l.audience, l.cta
        """,
        (f"-{(int(week_offset) + 1) * 7} days",),
    ).fetchall()
    return {
        "week_offset": int(week_offset),
        "lanes": [dict(r) for r in rows],
        "zero_post_lanes": [dict(r) for r in rows if (r["post_count"] or 0) == 0],
    }


# ---------------------------------------------------------------------------
# 6. analyze_post (§25)
# ---------------------------------------------------------------------------
def _analyze_post(conn: sqlite3.Connection, *, post_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.id, p.x_post_id, p.text, p.type, p.created_date,
               pc.pillar, pc.audience, pc.cta, pc.hypothesis, pc.lesson,
               m.like_count, m.reply_count, m.repost_count, m.impression_count
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        LEFT JOIN v_post_latest_metrics m ON m.post_id = p.id
        WHERE p.id = ?
        """,
        (int(post_id),),
    ).fetchone()
    if row is None:
        return {"error": f"post {post_id} not found"}
    return {"post": dict(row)}


# ---------------------------------------------------------------------------
# 7. summarize_winners (§25)
# ---------------------------------------------------------------------------
def _summarize_winners(
    conn: sqlite3.Connection,
    *,
    window_days: int = 30,
    lane_filter: str | None = None,
    confidence_minimum: str | None = None,
) -> dict[str, Any]:
    sql = "SELECT * FROM v_lane_performance WHERE 1=1"
    params: list[Any] = []
    if lane_filter:
        # lane_filter form: "stir×icp×ask"
        parts = lane_filter.split("×")
        if len(parts) == 3:
            sql += " AND pillar = ? AND audience = ? AND cta = ?"
            params.extend(parts)
    rows = conn.execute(sql, params).fetchall()
    lanes = [dict(r) for r in rows]
    if confidence_minimum:
        ranking = {"none": 0, "low": 1, "moderate": 2, "high": 3}
        floor = ranking.get(confidence_minimum.lower(), 0)
        lanes = [
            r for r in lanes
            if ranking.get((r.get("confidence_label") or "none").lower(), 0) >= floor
        ]
    # Top-3 by median engagement when available.
    lanes.sort(key=lambda r: (r.get("median_engagement") or 0.0), reverse=True)
    return {"window_days": int(window_days), "top_lanes": lanes[:3]}


# ---------------------------------------------------------------------------
# 8. find_reply_targets (§25 — from agent_target_accounts)
# ---------------------------------------------------------------------------
def _find_reply_targets(
    conn: sqlite3.Connection,
    *,
    lane: str | None = None,
    count: int = 5,
    recency_hours: int = 48,  # noqa: ARG001 — V1.1 will use this against snapshots
) -> dict[str, Any]:
    sql = """
        SELECT id, x_handle, display_name, notes, lane, priority,
               last_engaged_at
        FROM agent_target_accounts
        WHERE is_active = 1
    """
    params: list[Any] = []
    if lane:
        sql += " AND lane = ?"
        params.append(lane)
    sql += " ORDER BY priority ASC, last_engaged_at ASC NULLS FIRST LIMIT ?"
    params.append(int(count))
    rows = conn.execute(sql, params).fetchall()
    return {"accounts": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 9. score_reply_candidates (§28.4 #6 — Phase 5.6 will fully wire)
# ---------------------------------------------------------------------------
def _score_reply_candidates(
    conn: sqlite3.Connection,  # noqa: ARG001
    *,
    candidates: list[dict[str, Any]] | None = None,
    reply_target_id: int | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    return {
        "scored": [],
        "note": (
            "score_reply_candidates is a Phase-5.5 stub. Full scoring "
            "(four dimensions × deterministic recommended_action) lands "
            "in Phase 5.6 per spec §29.3."
        ),
        "input_candidate_count": len(candidates or []),
    }


# ---------------------------------------------------------------------------
# 10. extract_lesson (§28.4 #8)
# ---------------------------------------------------------------------------
def _extract_lesson(conn: sqlite3.Connection, *, post_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.text, pc.hypothesis, pc.expected_signal, pc.actual_signal,
               m.like_count, m.reply_count, m.repost_count
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        LEFT JOIN v_post_latest_metrics m ON m.post_id = p.id
        WHERE p.id = ?
        """,
        (int(post_id),),
    ).fetchone()
    if row is None:
        return {"error": f"post {post_id} not found"}
    return {
        "post_id": int(post_id),
        "context": dict(row),
        "lesson_text": None,
        "note": (
            "Session-1 stub: returns structured context only. Session-2 "
            "wires the Anthropic call that drafts the lesson_text."
        ),
    }


# ---------------------------------------------------------------------------
# 11. draft_weekly_review_section (§28.4 #9)
# ---------------------------------------------------------------------------
def _draft_weekly_review_section(
    conn: sqlite3.Connection,  # noqa: ARG001 — Session 2 reads the week's data
    *,
    section_name: str,
    week_id: int,
) -> dict[str, Any]:
    if section_name not in {
        "interpretation",
        "lesson",
        "counterfactual",
        "next_week_experiment",
    }:
        return {"error": f"unknown section_name {section_name!r}"}
    return {
        "section_name": section_name,
        "week_id": int(week_id),
        "draft_text": None,
        "note": (
            "Session-1 stub: section name validated. Session-2 wires the "
            "Anthropic call that drafts the actual prose."
        ),
    }


# ---------------------------------------------------------------------------
# 12. save_draft_post (§28.4 #4) — REAL implementation; IWH counter LIVE.
#
# Side effects:
#   * Inserts an agent_drafts row with iwh_attempt_index from the session
#     state passed by the caller (defaults to 1 if unset).
#   * Inserts a posts row with manual_confirmation_status='draft' and
#     posted_via='agent_assisted'.
#   * Wires agent_drafts.final_post_id and posts.agent_draft_id.
#
# The IWH gate (does any score < minimum? dark-pattern lint?) lives in
# the caller (app/agent/session.py in Session 2). This function is the
# write surface; refusal lives one layer up.
# ---------------------------------------------------------------------------
def _save_draft_post(
    conn: sqlite3.Connection,
    *,
    text: str,
    pillar: str,
    audience: str,
    cta: str,
    hypothesis: str | None = None,
    why_posted: str | None = None,  # noqa: ARG001 — stored on post_classifications later
    expected_signal: str | None = None,  # noqa: ARG001 — same
    voice_self_score: dict[str, int] | None = None,
    iwh_attempt_index: int = 1,
    session_id: str | None = None,
    conversation_id: int | None = None,
    agent_reasoning: str | None = None,
) -> dict[str, Any]:
    with transaction(conn):
        draft_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 audience, cta, agent_reasoning, voice_self_score,
                 iwh_attempt_index, status)
            VALUES (?, ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (
                session_id,
                conversation_id,
                text,
                pillar,
                audience,
                cta,
                agent_reasoning,
                json.dumps(voice_self_score) if voice_self_score else None,
                int(iwh_attempt_index),
            ),
        )
        draft_id = int(draft_cur.lastrowid)

        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id)
            VALUES (datetime('now'), date('now'), ?, 'standalone',
                    'agent_assisted', 'draft', ?)
            """,
            (text, draft_id),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, draft_id),
        )

        # W12 lands the UNIQUE(post_id) index that lets a future change
        # switch this to INSERT ON CONFLICT. Until then we just INSERT —
        # since this whole block is inside `transaction(conn)`, a retry
        # would be caught by the transaction failure path, not produce a
        # duplicate. Duplicates can only arise across SEPARATE successful
        # save_draft_post calls for the same post (which W12 prevents).
        conn.execute(
            """
            INSERT INTO post_classifications
                (post_id, pillar, audience, cta, hypothesis)
            VALUES (?, ?, ?, ?, ?)
            """,
            (post_id, pillar, audience, cta, hypothesis),
        )

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "draft_url": f"/?draft_id={draft_id}",
    }


# ---------------------------------------------------------------------------
# 13. save_draft_reply (§28.4 #5) — REAL implementation.
# ---------------------------------------------------------------------------
def _save_draft_reply(
    conn: sqlite3.Connection,
    *,
    text: str,
    target_post_url: str,
    target_post_text: str | None = None,
    pillar: str | None = None,
    hypothesis: str | None = None,  # noqa: ARG001 — Session 2 stores on classifications
    voice_self_score: dict[str, int] | None = None,
    iwh_attempt_index: int = 1,
    session_id: str | None = None,
    conversation_id: int | None = None,
    agent_reasoning: str | None = None,
) -> dict[str, Any]:
    with transaction(conn):
        draft_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 target_post_url, target_post_text, agent_reasoning,
                 voice_self_score, iwh_attempt_index, status)
            VALUES (?, ?, 'reply', ?, ?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (
                session_id,
                conversation_id,
                text,
                pillar,
                target_post_url,
                target_post_text,
                agent_reasoning,
                json.dumps(voice_self_score) if voice_self_score else None,
                int(iwh_attempt_index),
            ),
        )
        draft_id = int(draft_cur.lastrowid)

        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id)
            VALUES (datetime('now'), date('now'), ?, 'reply',
                    'agent_assisted', 'draft', ?)
            """,
            (text, draft_id),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, draft_id),
        )

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "target_post_url": target_post_url,
    }


# ---------------------------------------------------------------------------
# 14. revise_draft (§25) — supersedes an existing draft with a new attempt.
# ---------------------------------------------------------------------------
def _revise_draft(
    conn: sqlite3.Connection,
    *,
    draft_post_id: int,
    feedback: str,
    new_text: str,
    voice_self_score: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Mark the source draft superseded and insert a revision row.

    The new row's ``iwh_attempt_index`` is the source's index + 1. This is
    the orchestrator's increment path — the agent never writes this value.
    """
    src = conn.execute(
        """
        SELECT id, session_id, conversation_id, draft_kind, pillar,
               audience, cta, target_post_url, target_post_text,
               iwh_attempt_index
        FROM agent_drafts WHERE id = ?
        """,
        (int(draft_post_id),),
    ).fetchone()
    if src is None:
        return {"error": f"draft {draft_post_id} not found"}

    new_index = int(src["iwh_attempt_index"]) + 1
    with transaction(conn):
        rev_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 audience, cta, target_post_url, target_post_text,
                 voice_self_score, iwh_attempt_index, status,
                 revision_of, user_feedback)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (
                src["session_id"],
                src["conversation_id"],
                src["draft_kind"],
                new_text,
                src["pillar"],
                src["audience"],
                src["cta"],
                src["target_post_url"],
                src["target_post_text"],
                json.dumps(voice_self_score) if voice_self_score else None,
                new_index,
                int(src["id"]),
                feedback,
            ),
        )
        new_id = int(rev_cur.lastrowid)
        conn.execute(
            "UPDATE agent_drafts SET status = 'superseded' WHERE id = ?",
            (int(src["id"]),),
        )
    return {
        "new_draft_id": new_id,
        "iwh_attempt_index": new_index,
        "superseded_draft_id": int(src["id"]),
    }


# ---------------------------------------------------------------------------
# 15. record_reply_target (§28.4 #7 — Phase 5.6 fully wires)
# ---------------------------------------------------------------------------
def _record_reply_target(
    conn: sqlite3.Connection,  # noqa: ARG001
    *,
    target_post_url: str,
    target_post_text: str | None = None,  # noqa: ARG001
    target_user: str | None = None,  # noqa: ARG001
    pillar: str | None = None,  # noqa: ARG001
    audience: str | None = None,  # noqa: ARG001
    agent_reasoning: str | None = None,  # noqa: ARG001
    agent_priority_score: int | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    return {
        "reply_target_id": None,
        "target_post_url": target_post_url,
        "note": (
            "Session-1 stub: reply_targets table lands in Phase 5.6 "
            "(spec §29.6). This call records the intent but does not "
            "yet persist to a dedicated row."
        ),
    }


# ===========================================================================
# AGENT_TOOLS — the registered tool catalog (15 entries).
# ===========================================================================
AGENT_TOOLS: list[ToolDef] = [
    ToolDef(
        name="query_dashboard_state",
        description=(
            "Return structured JSON of a dashboard slice (today / next_rep / "
            "weekly / validation_status / all). Use at the start of any "
            "drafting task to ground yourself in current state."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "slice": {
                    "type": "string",
                    "enum": ["today", "next_rep", "weekly", "validation_status", "all"],
                }
            },
            "required": ["slice"],
        },
        handler=_query_dashboard_state,
    ),
    ToolDef(
        name="get_recent_posts",
        description=(
            "Recent posts with classifications and latest metrics. Filter "
            "by pillar/audience/cta. Use when drafting or analyzing patterns."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "cta": {"type": "string"},
                "days_back": {"type": "integer", "default": 7},
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_get_recent_posts,
    ),
    ToolDef(
        name="get_lane_performance",
        description=(
            "Rows from v_lane_performance with confidence labels. Never "
            "rank lanes below 'moderate' confidence."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "cta": {"type": "string"},
                "window_days": {"type": "integer", "default": 14},
            },
        },
        handler=_get_lane_performance,
    ),
    ToolDef(
        name="get_open_hypotheses",
        description=(
            "Currently running or proposed experiments from the experiments "
            "table. Use when proposing a draft that should test a hypothesis."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_open_hypotheses,
    ),
    ToolDef(
        name="get_lane_gaps",
        description=(
            "Which (pillar × audience × cta) lanes have ZERO posts in the "
            "given week offset. Use to find the most under-sampled lane."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "week_offset": {"type": "integer", "default": 0},
            },
        },
        handler=_get_lane_gaps,
    ),
    ToolDef(
        name="analyze_post",
        description=(
            "Deep-dive a single post by id: text, classification, hypothesis, "
            "latest metrics. Use before drafting a postmortem or revision."
        ),
        input_schema={
            "type": "object",
            "properties": {"post_id": {"type": "integer"}},
            "required": ["post_id"],
        },
        handler=_analyze_post,
    ),
    ToolDef(
        name="summarize_winners",
        description=(
            "Top-3 best-performing lanes in a window, filtered by lane and "
            "confidence floor. Refuses to rank lanes below given confidence."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "window_days": {"type": "integer", "default": 30},
                "lane_filter": {"type": "string"},
                "confidence_minimum": {
                    "type": "string",
                    "enum": ["low", "moderate", "high"],
                },
            },
        },
        handler=_summarize_winners,
    ),
    ToolDef(
        name="find_reply_targets",
        description=(
            "Curated agent_target_accounts to consider for reply outreach. "
            "MVP pulls from the manually-curated list; V1.1+ adds API search."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "lane": {"type": "string"},
                "count": {"type": "integer", "default": 5},
                "recency_hours": {"type": "integer", "default": 48},
            },
        },
        handler=_find_reply_targets,
    ),
    ToolDef(
        name="score_reply_candidates",
        description=(
            "Score a batch of candidate posts to reply under. Phase 5.5 "
            "stub; Phase 5.6 wires the four-dimension scoring per §29.3."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "text": {"type": "string"},
                            "user": {"type": "string"},
                        },
                    },
                },
                "reply_target_id": {"type": "integer"},
            },
        },
        handler=_score_reply_candidates,
    ),
    ToolDef(
        name="extract_lesson",
        description=(
            "Given a post id, return the structured context for drafting a "
            "lesson (the text, classification, hypothesis, metrics). Daniel "
            "saves the lesson via the existing classification flow."
        ),
        input_schema={
            "type": "object",
            "properties": {"post_id": {"type": "integer"}},
            "required": ["post_id"],
        },
        handler=_extract_lesson,
    ),
    ToolDef(
        name="draft_weekly_review_section",
        description=(
            "Draft one section of a weekly review (interpretation / lesson / "
            "counterfactual / next_week_experiment). Daniel edits before save."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "section_name": {
                    "type": "string",
                    "enum": [
                        "interpretation",
                        "lesson",
                        "counterfactual",
                        "next_week_experiment",
                    ],
                },
                "week_id": {"type": "integer"},
            },
            "required": ["section_name", "week_id"],
        },
        handler=_draft_weekly_review_section,
    ),
    ToolDef(
        name="save_draft_post",
        description=(
            "Persist a final draft post. The orchestrator runs IWH score + "
            "dark-pattern lint preflight BEFORE calling this; failed drafts "
            "bounce as revisions. Do NOT call until the user has approved."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "cta": {"type": "string"},
                "hypothesis": {"type": "string"},
                "why_posted": {"type": "string"},
                "expected_signal": {"type": "string"},
                "agent_reasoning": {"type": "string"},
            },
            "required": ["text", "pillar", "audience", "cta"],
        },
        handler=_save_draft_post,
    ),
    ToolDef(
        name="save_draft_reply",
        description=(
            "Persist a final draft reply. Target URL is preserved. Same "
            "IWH + lint preflight as save_draft_post."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target_post_url": {"type": "string"},
                "target_post_text": {"type": "string"},
                "pillar": {"type": "string"},
                "hypothesis": {"type": "string"},
                "agent_reasoning": {"type": "string"},
            },
            "required": ["text", "target_post_url"],
        },
        handler=_save_draft_reply,
    ),
    ToolDef(
        name="revise_draft",
        description=(
            "Supersede an existing draft with new text + feedback. The new "
            "row's iwh_attempt_index is parent + 1 — increment is owned by "
            "the orchestrator, never by the model."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "draft_post_id": {"type": "integer"},
                "feedback": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["draft_post_id", "feedback", "new_text"],
        },
        handler=_revise_draft,
    ),
    ToolDef(
        name="record_reply_target",
        description=(
            "Add a candidate target to the reply queue. Phase 5.5 stub; "
            "Phase 5.6 lands the dedicated reply_targets table."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_post_url": {"type": "string"},
                "target_post_text": {"type": "string"},
                "target_user": {"type": "string"},
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "agent_reasoning": {"type": "string"},
                "agent_priority_score": {"type": "integer"},
            },
            "required": ["target_post_url"],
        },
        handler=_record_reply_target,
    ),
]


def get_tool(name: str) -> ToolDef:
    """Lookup a registered tool by name. Raises ``KeyError`` on miss."""
    for t in AGENT_TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"unknown agent tool: {name!r}")
