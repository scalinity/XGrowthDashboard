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
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from app.db import transaction
from app.agent import account_research as _account_research
from app.agent import brain_dump as _brain_dump
from app.agent import profile_audit as _profile_audit
from app.agent import content_types as _content_types
from app.agent import personality_lore as _personality_lore
from app.agent import prepublish_scorer as _prepublish_scorer
from app.agent import repetition_guard as _repetition_guard
from app.agent import replier_pool as _replier_pool
from app.agent import velocity as _velocity
from app.agent import voice_profile as _voice_profile
from app.agent.reply_targets import (
    ACTION_TO_SCORE,
    REPLY_INTENT_ENUM,
    engagement_surface_score,
    engagement_surface_thresholds,
    resolve_recommended_action,
    saturation_score as _saturation_score_helper,
)

_LOG = logging.getLogger(__name__)


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
            "SELECT * FROM v_funnel_daily ORDER BY event_date DESC LIMIT 7"
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
    # v_post_latest_metrics exposes likes / reply_count-ish columns under
    # the names `likes`, `replies`, `reposts` (002_views.sql) — previous
    # code referenced `like_count` / `reply_count` / `repost_count` which
    # don't exist on the view.
    sql = """
        SELECT p.id, p.x_post_id, p.created_date, p.text, p.type,
               pc.pillar, pc.audience, pc.cta,
               m.likes, m.replies, m.reposts, m.impressions
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
    """Active or planned experiments from `experiments` (§28.4 #4).

    Schema reality check (`migrations/001_initial.sql:382-395`): the
    columns are `hypothesis`, `content_lane`, `target_audience`,
    `success_metric`, `start_date` — NOT `hypothesis_text` / `lane` /
    `expected_signal` / `started_at_utc`. CHECK accepts
    'planned' | 'running' | 'completed' | 'abandoned' — NOT 'proposed'.
    The original handler crashed sqlite3 the first time the model
    invoked it.
    """
    rows = conn.execute(
        """
        SELECT id, name, hypothesis, content_lane, target_audience,
               success_metric, status, start_date
        FROM experiments
        WHERE status IN ('planned', 'running')
        ORDER BY start_date DESC, id DESC
        """
    ).fetchall()
    return {"experiments": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 5. get_lane_gaps (§25)
# ---------------------------------------------------------------------------
def _get_lane_gaps(conn: sqlite3.Connection, *, week_offset: int = 0) -> dict[str, Any]:
    """Lanes with zero posts in the given week — surface missing lanes.

    ``week_offset=0`` is the current 7-day window (back to today-7);
    ``week_offset=1`` is the prior week (today-14 to today-7); etc.
    The prior implementation only had a lower bound, so any prior-week
    query also included this week's posts and under-reported gaps.
    """
    n = int(week_offset)
    lower_bound = f"-{(n + 1) * 7} days"
    if n == 0:
        # Current window — include today (open upper bound).
        # NOTE: post_classifications has no UNIQUE(post_id) yet (W12 lands
        # the migration), so the JOIN can double-count a post that picked
        # up multiple classification rows from an earlier retry. Use
        # COUNT(DISTINCT po.id) defensively.
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
                SELECT pc.pillar, pc.audience, pc.cta,
                       COUNT(DISTINCT po.id) AS post_count
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
            (lower_bound,),
        ).fetchall()
    else:
        upper_bound = f"-{n * 7} days"
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
                SELECT pc.pillar, pc.audience, pc.cta,
                       COUNT(DISTINCT po.id) AS post_count
                FROM posts po
                JOIN post_classifications pc ON pc.post_id = po.id
                WHERE po.created_date >= date('now', ?)
                  AND po.created_date <  date('now', ?)
                GROUP BY pc.pillar, pc.audience, pc.cta
            )
            SELECT l.pillar, l.audience, l.cta,
                   COALESCE(r.post_count, 0) AS post_count
            FROM lanes l
            LEFT JOIN recent_counts r
              ON r.pillar = l.pillar AND r.audience = l.audience AND r.cta = l.cta
            ORDER BY post_count ASC, l.pillar, l.audience, l.cta
            """,
            (lower_bound, upper_bound),
        ).fetchall()
    return {
        "week_offset": n,
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
               m.likes, m.replies, m.reposts, m.impressions
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
        # Accept any of the common separators models emit. Unicode `×` is
        # the canonical form but Sonnet/Haiku frequently emit `x`, `*`,
        # or `:` even when prompted. Split on the first separator found.
        import re as _re
        parts = [p.strip() for p in _re.split(r"\s*[×x*:]\s*", lane_filter)]
        parts = [p for p in parts if p]  # drop empties
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
    # Top-3 by median engagement rate when available. The view column is
    # `median_engagement_rate` (002_views.sql); a previous typo here used
    # the non-existent `median_engagement` and silently returned the
    # unsorted prefix.
    lanes.sort(
        key=lambda r: (r.get("median_engagement_rate") or 0.0),
        reverse=True,
    )
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
# 9. score_reply_candidates (§28.4 #6) — Phase 5.6 wires §29.3 resolver.
#
# Two input shapes per the prompt:
#   * candidates: list[dict]    — fresh candidate(s); upserts the
#     ``reply_targets`` row (creates if URL not present, otherwise re-scores
#     the existing row), then computes scores + recommended_action.
#   * reply_target_id: int      — re-scores an existing row in place.
#
# Per §29.3:
#   * engagement_surface_score is computed deterministically from
#     ``like_count`` and ``target_author_follower_count`` via §29.4 thresholds.
#   * saturation_score is computed deterministically from ``reply_count``.
#   * relevance_score and reply_opportunity_score are judgments — the agent
#     supplies them as part of the call (§29.3 dimension table). If absent
#     they default to NULL, which means recommended_action_label is left NULL
#     (the row is recorded but unresolved). The Queue surfaces the gap.
# ---------------------------------------------------------------------------
def _load_engagement_surface_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Pull the four §29.4 settings rows; fall back to documented defaults."""
    defaults = {
        "engagement_surface_floor_likes": 15,
        "engagement_surface_pct_of_author": 0.001,
        "engagement_surface_high_floor_likes": 50,
        "engagement_surface_high_pct": 0.005,
    }
    rows = conn.execute(
        "SELECT key, value_json FROM settings WHERE key IN "
        "('engagement_surface_floor_likes', 'engagement_surface_pct_of_author', "
        " 'engagement_surface_high_floor_likes', 'engagement_surface_high_pct')"
    ).fetchall()
    settings = dict(defaults)
    for r in rows:
        # /review-2 🔵 #7 — narrow the catch + log so a corrupted setting
        # row doesn't silently fall back to the default with no signal.
        try:
            settings[r["key"]] = json.loads(r["value_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _LOG.warning(
                "engagement-surface setting %r unparseable (%r); using default %r",
                r["key"], exc, defaults.get(r["key"]),
            )
    return settings


def _compute_and_persist_scores_locked(
    conn: sqlite3.Connection,
    *,
    reply_target_id: int,
    relevance: int | None,
    reply_opportunity: int | None,
    rationale: str | None,
    reply_intent: str | None = None,
    pillar: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Re-score an existing reply_targets row IN A CALLER-OWNED TRANSACTION.

    /review-2 🔴 #2 — this function MUST be called inside an outer
    ``with transaction(conn):`` block. The caller owns the transaction
    boundary so the INSERT-into-reply_targets, the metric-refresh UPDATE,
    and the score UPDATE all commit atomically. If the resolver or the
    UPDATE raises (e.g. CHECK constraint violation on an out-of-range
    relevance_score the agent emitted), the entire unit-of-work rolls
    back, preventing orphaned partial rows.
    """
    row = conn.execute(
        "SELECT * FROM reply_targets WHERE id = ?", (int(reply_target_id),)
    ).fetchone()
    if row is None:
        return {"error": f"reply_target_id {reply_target_id} not found"}

    settings = _load_engagement_surface_settings(conn)
    med, hi = engagement_surface_thresholds(row["target_author_follower_count"], settings)
    eng = engagement_surface_score(int(row["like_count"] or 0), med, hi)
    sat = _saturation_score_helper(int(row["reply_count"] or 0))

    # Resolver only runs when all four MVP scores are present. Without
    # relevance + reply_opportunity from the agent, persist what we have
    # and leave recommended_action_* NULL.
    if relevance is not None and reply_opportunity is not None:
        label = resolve_recommended_action(
            int(relevance), eng, sat, int(reply_opportunity)
        )
        action_score = ACTION_TO_SCORE[label]
    else:
        label = None
        action_score = None

    conn.execute(
        """
        UPDATE reply_targets
        SET relevance_score          = COALESCE(?, relevance_score),
            engagement_surface_score = ?,
            saturation_score         = ?,
            reply_opportunity_score  = COALESCE(?, reply_opportunity_score),
            recommended_action_label = ?,
            recommended_action_score = ?,
            score_rationale          = COALESCE(?, score_rationale),
            reply_intent             = COALESCE(?, reply_intent),
            pillar                   = COALESCE(?, pillar),
            audience                 = COALESCE(?, audience),
            last_checked_at_utc      = datetime('now')
        WHERE id = ?
        """,
        (
            relevance, eng, sat, reply_opportunity,
            label, action_score, rationale,
            reply_intent, pillar, audience,
            int(reply_target_id),
        ),
    )

    return {
        "reply_target_id": int(reply_target_id),
        "relevance_score": relevance if relevance is not None else row["relevance_score"],
        "engagement_surface_score": eng,
        "saturation_score": sat,
        "reply_opportunity_score": reply_opportunity if reply_opportunity is not None else row["reply_opportunity_score"],
        "recommended_action_label": label,
        "recommended_action_score": action_score,
        "score_rationale": rationale or row["score_rationale"],
    }


def _score_reply_candidates(
    conn: sqlite3.Connection,
    *,
    candidates: list[dict[str, Any]] | None = None,
    reply_target_id: int | None = None,
) -> dict[str, Any]:
    # /review-2 🟡 #4 — reject the mixed-mode call shape loudly instead of
    # silently dropping reply_target_id when candidates are also passed.
    if reply_target_id is not None and candidates:
        return {
            "scored": [],
            "errors": [
                "pass either candidates or reply_target_id, not both"
            ],
        }

    if reply_target_id is not None:
        # /review-2 🔴 #2 — caller owns the transaction.
        with transaction(conn):
            result = _compute_and_persist_scores_locked(
                conn,
                reply_target_id=int(reply_target_id),
                relevance=None,
                reply_opportunity=None,
                rationale=None,
            )
        if "error" in result:
            return {"scored": [], "errors": [result["error"]]}
        return {"scored": [result], "errors": []}

    if not candidates:
        return {"scored": [], "errors": ["no candidates and no reply_target_id provided"]}

    scored: list[dict[str, Any]] = []
    errors: list[str] = []
    for c in candidates:
        url = (c.get("url") or c.get("target_post_url") or "").strip()
        if not url:
            errors.append("candidate missing url")
            continue
        # /review-2 🔴 #2 — INSERT + metric-refresh + score UPDATE all run
        # inside one transaction so a CHECK failure on the inner UPDATE
        # rolls back the just-minted row instead of orphaning it.
        # /review-2 🟡 #2 — error dicts go to `errors`, not `scored`,
        # so callers reading scored[i]["recommended_action_label"] don't
        # KeyError.
        try:
            with transaction(conn):
                existing = conn.execute(
                    "SELECT id FROM reply_targets WHERE target_post_url = ?",
                    (url,),
                ).fetchone()
                if existing is None:
                    new_cur = conn.execute(
                        """
                        INSERT INTO reply_targets
                            (discovered_via, target_post_url, target_x_post_id,
                             target_author_handle, target_author_display_name,
                             target_author_follower_count, target_text,
                             post_age_minutes, like_count, reply_count,
                             repost_count, quote_count, pillar, audience,
                             reply_intent)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c.get("discovered_via", "agent_score"),
                            url,
                            c.get("target_x_post_id") or _parse_x_post_id(url),
                            c.get("author_handle") or c.get("target_author_handle") or "unknown",
                            c.get("target_author_display_name"),
                            c.get("target_author_follower_count"),
                            c.get("text") or c.get("target_text"),
                            c.get("post_age_minutes"),
                            c.get("like_count"),
                            c.get("reply_count"),
                            c.get("repost_count"),
                            c.get("quote_count"),
                            c.get("pillar"),
                            c.get("audience"),
                            c.get("reply_intent"),
                        ),
                    )
                    rt_id = int(new_cur.lastrowid)
                else:
                    rt_id = int(existing["id"])
                    # Refresh metrics inside the same transaction so the
                    # downstream score UPDATE sees the refreshed values.
                    updates: list[tuple[str, Any]] = []
                    for k_in, k_db in (
                        ("like_count", "like_count"),
                        ("reply_count", "reply_count"),
                        ("repost_count", "repost_count"),
                        ("quote_count", "quote_count"),
                        ("target_author_follower_count", "target_author_follower_count"),
                        ("post_age_minutes", "post_age_minutes"),
                    ):
                        if c.get(k_in) is not None:
                            updates.append((k_db, c[k_in]))
                    if updates:
                        set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
                        conn.execute(
                            f"UPDATE reply_targets SET {set_clause} WHERE id = ?",
                            [v for _, v in updates] + [rt_id],
                        )

                result = _compute_and_persist_scores_locked(
                    conn,
                    reply_target_id=rt_id,
                    relevance=c.get("relevance_score"),
                    reply_opportunity=c.get("reply_opportunity_score"),
                    rationale=c.get("score_rationale"),
                    reply_intent=c.get("reply_intent"),
                    pillar=c.get("pillar"),
                    audience=c.get("audience"),
                )
        except Exception as exc:  # noqa: BLE001 — wrap any DB error per candidate
            errors.append(f"candidate {url!r}: {exc}")
            continue
        if "error" in result:
            errors.append(result["error"])
            continue
        scored.append(result)

    return {"scored": scored, "errors": errors}


def _parse_x_post_id(url: str) -> str | None:
    """Pull the numeric post id from an X URL.

    Accepts the canonical ``https://x.com/{handle}/status/{id}`` form (and
    twitter.com aliases). Returns None on no match.
    """
    import re as _re
    m = _re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 10. extract_lesson (§28.4 #8)
# ---------------------------------------------------------------------------
def _extract_lesson(conn: sqlite3.Connection, *, post_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.text, pc.hypothesis, pc.expected_signal, pc.actual_signal,
               m.likes, m.replies, m.reposts
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        LEFT JOIN v_post_latest_metrics m ON m.post_id = p.id
        WHERE p.id = ?
        """,
        (int(post_id),),
    ).fetchone()
    if row is None:
        return {"error": f"post {post_id} not found"}
    # W26: signal stub status to the dispatcher via a private key so
    # the audit row records status='partial' instead of 'success'.
    # Reviewers can grep agent_tool_calls.status for stub-noise.
    return {
        "post_id": int(post_id),
        "context": dict(row),
        "lesson_text": None,
        "_audit_status": "partial",
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
    content_type: str | None = None,
    hypothesis: str | None = None,
    hypothesis_id: int | None = None,
    why_posted: str | None = None,  # noqa: ARG001 — stored on post_classifications later
    expected_signal: str | None = None,  # noqa: ARG001 — same
    voice_self_score: dict[str, int] | None = None,
    iwh_attempt_index: int = 1,
    session_id: str | None = None,
    conversation_id: int | None = None,
    agent_reasoning: str | None = None,
    confidence_label: str | None = None,
) -> dict[str, Any]:
    # Phase 5.9 / §28.17 — orchestrator-enforced V/G/P/P validation. Raises
    # ContentTypeInvalidError on missing / 'unspecified' / unknown. The
    # CHECK constraint also permits 'unspecified' so the migration can
    # backfill legacy rows; this guard is the runtime contract.
    ct = _content_types.validate_for_save(content_type)
    with transaction(conn):
        draft_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 audience, cta, hypothesis_id, agent_reasoning,
                 voice_self_score, iwh_attempt_index, status,
                 confidence_label, content_type)
            VALUES (?, ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (
                session_id,
                conversation_id,
                text,
                pillar,
                audience,
                cta,
                hypothesis_id,
                agent_reasoning,
                json.dumps(voice_self_score) if voice_self_score else None,
                int(iwh_attempt_index),
                confidence_label,
                ct,
            ),
        )
        draft_id = int(draft_cur.lastrowid)

        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id, content_type)
            VALUES (datetime('now'), date('now'), ?, 'standalone',
                    'agent_assisted', 'draft', ?, ?)
            """,
            (text, draft_id, ct),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, draft_id),
        )

        # W12 added a UNIQUE(post_id) index on post_classifications. ON
        # CONFLICT DO UPDATE keeps the row a single source of truth and
        # absorbs retries idempotently — repeated save_draft_post calls
        # for the same post now overwrite the classification rather than
        # accumulating rows.
        conn.execute(
            """
            INSERT INTO post_classifications
                (post_id, pillar, audience, cta, hypothesis)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                pillar = excluded.pillar,
                audience = excluded.audience,
                cta = excluded.cta,
                hypothesis = excluded.hypothesis,
                updated_at = datetime('now')
            """,
            (post_id, pillar, audience, cta, hypothesis),
        )

        # Phase 5.8 / §28.11 — pre-publish heuristic scorer. Deterministic,
        # never blocks. Writes a prepublish_scores row and wires the
        # cyclical FK on agent_drafts. Lives inside the transaction so a
        # scorer crash rolls back the draft alongside.
        score_row = _prepublish_scorer.score(
            draft_text=text,
            draft_kind="standalone",
            pillar=pillar,
            cta=cta,
            target_post_text=None,
            active_voice_profile=_voice_profile.get_active(conn),
        )
        _prepublish_scorer.insert_score_row(
            conn, agent_draft_id=draft_id, row=score_row
        )

        # Phase 5.8 / §28.13 — repetition guard. Returns None when the
        # embedding provider is unavailable; persist NULL and proceed.
        similarity_warning = _repetition_guard.check(
            conn, draft_text=text, draft_kind="standalone"
        )
        if similarity_warning is not None:
            conn.execute(
                "UPDATE agent_drafts SET similarity_warning_json = ? WHERE id = ?",
                (json.dumps(similarity_warning), draft_id),
            )

        # Phase 5.9 / §28.21 — personality lore invocation scan. Only
        # runs for personality drafts; over-counting acceptable per spec.
        invoked_lore_ids: list[int] = []
        if ct == "personality":
            invoked_lore_ids = _personality_lore.scan_and_increment_invocations(
                conn, draft_text=text
            )

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "draft_url": f"/?draft_id={draft_id}",
        "prepublish_label": score_row.composite_label,
        "similarity_warning": similarity_warning,
        "invoked_lore_ids": invoked_lore_ids,
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
    content_type: str | None = None,
    hypothesis: str | None = None,  # noqa: ARG001 — Session 2 stores on classifications
    voice_self_score: dict[str, int] | None = None,
    iwh_attempt_index: int = 1,
    session_id: str | None = None,
    conversation_id: int | None = None,
    agent_reasoning: str | None = None,
    confidence_label: str | None = None,
    reply_quality_lint_passed: bool | None = None,
) -> dict[str, Any]:
    # Phase 5.9 / §28.17 — required, same enforcement as posts.
    ct = _content_types.validate_for_save(content_type)
    # Phase 5.9 / §28.18 — persistence: None when the lint wasn't run
    # (dispatcher didn't inject), 1/0 from the dispatcher-injected
    # decision.reply_quality_result.passed.
    rq_persist = (
        None if reply_quality_lint_passed is None
        else (1 if reply_quality_lint_passed else 0)
    )
    with transaction(conn):
        draft_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 target_post_url, target_post_text, agent_reasoning,
                 voice_self_score, iwh_attempt_index, status,
                 confidence_label, content_type, reply_quality_lint_passed)
            VALUES (?, ?, 'reply', ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
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
                confidence_label,
                ct,
                rq_persist,
            ),
        )
        draft_id = int(draft_cur.lastrowid)

        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id, content_type)
            VALUES (datetime('now'), date('now'), ?, 'reply',
                    'agent_assisted', 'draft', ?, ?)
            """,
            (text, draft_id, ct),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, draft_id),
        )

        # Phase 5.8 / §28.11 — same scoring pass for replies. Includes
        # the reply_substance dimension keyed off target_post_text.
        score_row = _prepublish_scorer.score(
            draft_text=text,
            draft_kind="reply",
            pillar=pillar,
            cta=None,
            target_post_text=target_post_text,
            active_voice_profile=_voice_profile.get_active(conn),
        )
        _prepublish_scorer.insert_score_row(
            conn, agent_draft_id=draft_id, row=score_row
        )

        # Phase 5.8 / §28.13 — repetition guard, same degradation contract.
        similarity_warning = _repetition_guard.check(
            conn, draft_text=text, draft_kind="reply"
        )
        if similarity_warning is not None:
            conn.execute(
                "UPDATE agent_drafts SET similarity_warning_json = ? WHERE id = ?",
                (json.dumps(similarity_warning), draft_id),
            )

        # Phase 5.9 / §28.21 — personality lore invocation scan, mirrors
        # the _save_draft_post path. content_type='personality' is a
        # permitted value on replies; the scan must run there too or the
        # over-reliance banner undercounts. P59A-W1.
        invoked_lore_ids: list[int] = []
        if ct == "personality":
            invoked_lore_ids = _personality_lore.scan_and_increment_invocations(
                conn, draft_text=text
            )

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "target_post_url": target_post_url,
        "prepublish_label": score_row.composite_label,
        "similarity_warning": similarity_warning,
        "invoked_lore_ids": invoked_lore_ids,
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
               iwh_attempt_index, content_type, confidence_label,
               reply_quality_lint_passed
        FROM agent_drafts WHERE id = ?
        """,
        (int(draft_post_id),),
    ).fetchone()
    if src is None:
        return {"error": f"draft {draft_post_id} not found"}

    new_index = int(src["iwh_attempt_index"]) + 1
    post_type = "reply" if src["draft_kind"] == "reply" else "standalone"
    # P59A-C1: propagate the Phase 5.8 / 5.9 per-draft annotations from
    # the source. Without this, every IWH revision lands NULL content_type
    # and silently drops out of v_content_type_performance. The original
    # _revise_draft predates the Phase 5.9 columns; the orchestrator's
    # "refuse unspecified" promise was bypassed via this path.
    src_content_type = src["content_type"]
    with transaction(conn):
        rev_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 audience, cta, target_post_url, target_post_text,
                 voice_self_score, iwh_attempt_index, status,
                 revision_of, user_feedback,
                 content_type, confidence_label, reply_quality_lint_passed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)
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
                src_content_type,
                src["confidence_label"],
                src["reply_quality_lint_passed"],
            ),
        )
        new_id = int(rev_cur.lastrowid)

        # C2: mint a corresponding posts row so the publish modal can find
        # it via `SELECT id FROM posts WHERE agent_draft_id = ?`. Without
        # this, every IWH revision produced a draft that could not be
        # published — the modal raised "Internal: agent_drafts row has no
        # linked posts row." `_save_draft_post`/`_save_draft_reply` already
        # do this; the revise path was the missing case.
        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id, content_type)
            VALUES (datetime('now'), date('now'), ?, ?, 'agent_assisted',
                    'draft', ?, ?)
            """,
            (new_text, post_type, new_id, src_content_type or "unspecified"),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, new_id),
        )

        conn.execute(
            "UPDATE agent_drafts SET status = 'superseded' WHERE id = ?",
            (int(src["id"]),),
        )
    return {
        "new_draft_id": new_id,
        "post_id": post_id,
        "iwh_attempt_index": new_index,
        "superseded_draft_id": int(src["id"]),
    }


# ---------------------------------------------------------------------------
# 15. record_reply_target (§28.4 #7) — writes to the §29.6 reply_targets row.
#
# Idempotent on target_post_url (the §29.6 unique index): if Daniel or the
# agent re-records the same URL, the existing row is returned. Optional
# enrichment fields are layered on as a partial update so re-recording with
# more context (likes / replies / author follower count discovered later)
# refreshes the row without re-scoring it — scoring lives on
# score_reply_candidates per §29.8.
#
# CONTRACT (/review-2 🟡 #3):
#   * Pass an explicit value to set a column, including 0.
#   * Pass None (the default for every keyword) to leave the existing
#     column untouched on the re-record path, or to leave it NULL on insert.
#   * Callers must NOT use ``int(x) or None`` to mean "field omitted" —
#     a legitimate 0 (e.g. a candidate genuinely at 0 likes) is data and
#     should land as 0, not NULL.
# ---------------------------------------------------------------------------
def _record_reply_target(
    conn: sqlite3.Connection,
    *,
    target_post_url: str,
    target_post_text: str | None = None,
    target_user: str | None = None,
    target_author_follower_count: int | None = None,
    like_count: int | None = None,
    reply_count: int | None = None,
    repost_count: int | None = None,
    quote_count: int | None = None,
    post_age_minutes: int | None = None,
    pillar: str | None = None,
    audience: str | None = None,
    reply_intent: str | None = None,
    agent_reasoning: str | None = None,
    agent_priority_score: int | None = None,  # noqa: ARG001 — V1.1+; kept for API compat
    discovered_via: str = "agent_score",
    created_via_agent_message_id: int | None = None,
) -> dict[str, Any]:
    target_post_url = target_post_url.strip()
    if not target_post_url:
        return {"error": "target_post_url is required"}

    if reply_intent is not None and reply_intent not in REPLY_INTENT_ENUM:
        return {
            "error": (
                f"reply_intent={reply_intent!r} not in §29.5 enum "
                f"{REPLY_INTENT_ENUM}"
            )
        }

    existing = conn.execute(
        "SELECT id FROM reply_targets WHERE target_post_url = ?", (target_post_url,)
    ).fetchone()
    if existing is not None:
        rt_id = int(existing["id"])
        # Best-effort enrichment — leave existing values intact when caller
        # didn't supply a new one.
        conn.execute(
            """
            UPDATE reply_targets SET
                target_text                   = COALESCE(?, target_text),
                target_author_handle          = COALESCE(?, target_author_handle),
                target_author_follower_count  = COALESCE(?, target_author_follower_count),
                like_count                    = COALESCE(?, like_count),
                reply_count                   = COALESCE(?, reply_count),
                repost_count                  = COALESCE(?, repost_count),
                quote_count                   = COALESCE(?, quote_count),
                post_age_minutes              = COALESCE(?, post_age_minutes),
                pillar                        = COALESCE(?, pillar),
                audience                      = COALESCE(?, audience),
                reply_intent                  = COALESCE(?, reply_intent),
                notes                         = COALESCE(?, notes),
                last_checked_at_utc           = datetime('now')
            WHERE id = ?
            """,
            (
                target_post_text, target_user, target_author_follower_count,
                like_count, reply_count, repost_count, quote_count,
                post_age_minutes, pillar, audience, reply_intent,
                agent_reasoning, rt_id,
            ),
        )
        return {
            "reply_target_id": rt_id,
            "target_post_url": target_post_url,
            "created": False,
            "note": "candidate already in queue — enrichment applied.",
        }

    cur = conn.execute(
        """
        INSERT INTO reply_targets
            (discovered_via, target_post_url, target_x_post_id,
             target_author_handle, target_text,
             target_author_follower_count,
             like_count, reply_count, repost_count, quote_count,
             post_age_minutes, pillar, audience, reply_intent,
             notes, created_via_agent_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            discovered_via,
            target_post_url,
            _parse_x_post_id(target_post_url),
            target_user or "unknown",
            target_post_text,
            target_author_follower_count,
            like_count, reply_count, repost_count, quote_count,
            post_age_minutes,
            pillar, audience, reply_intent,
            agent_reasoning,
            created_via_agent_message_id,
        ),
    )
    rt_id = int(cur.lastrowid)
    # P58R-13 — compute expiry from the row's actual last_checked_at_utc
    # (the timestamp basis expire_stale_candidates uses in
    # app/jobs/reply_target_maintenance.py) so the agent's quoted
    # expiry can't drift from the DB-side policy. The prior
    # `datetime('now', '+N hours')` SELECT re-computed the timestamp
    # at SELECT time, which under contention could differ from the
    # INSERT's timestamp by milliseconds-to-seconds.
    expires_row = conn.execute(
        """
        SELECT datetime(
            last_checked_at_utc,
            '+' || COALESCE(
                (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
                   FROM settings WHERE key = 'reply_target_expiry_hours'),
                24
            ) || ' hours'
        ) AS expires_at_utc
        FROM reply_targets WHERE id = ?
        """,
        (rt_id,),
    ).fetchone()
    return {
        "reply_target_id": rt_id,
        "target_post_url": target_post_url,
        "created": True,
        "expires_at_utc": expires_row["expires_at_utc"] if expires_row else None,
    }


# ===========================================================================
# Profile Audit tool wrapper — runs audit() + save() inside one tool
# invocation so chat-driven calls get the persisted audit id back (§28.25).
# Failures surface as {"status": "failed"} so the smoke test holds.
# ===========================================================================
def _audit_profile_to_dict(
    conn: sqlite3.Connection,
    *,
    bio_text: str,
    pinned_post_text: str,
    recent_post_window_days: int | None,
    pinned_post_id: int | None,
) -> dict[str, Any]:
    try:
        analysis, snapshot = _profile_audit.audit(
            conn,
            bio_text=bio_text,
            pinned_post_text=pinned_post_text,
            recent_post_window_days=recent_post_window_days,
        )
    except _profile_audit.ProfileAuditError as exc:
        # P510R-20: log before returning the failure dict so the
        # operator grepping `data/logs/` has the full stack, not just
        # the truncated error string the model sees in its tool result.
        _LOG.warning("audit_profile tool failed: %s", exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}

    audit_id = _profile_audit.save(
        conn,
        analysis=analysis,
        bio_snapshot=bio_text,
        pinned_post_id=pinned_post_id,
        pinned_post_text=pinned_post_text,
        snapshot=snapshot,
    )
    return {
        "status": "saved",
        "audit_id": audit_id,
        "overall_consistency_score": analysis.overall_consistency_score,
        "top_three_actions": list(analysis.top_three_actions),
        "tokens_used": analysis.tokens_used,
        "audit": analysis.to_dict(),
        "note": (
            "Audit is append-only history (§28.25). Cadence reminder "
            "fires at profile_audit_cadence_reminder_days (default 90); "
            "audits NEVER auto-run."
        ),
    }


# ===========================================================================
# Account Researcher tool wrapper — runs analyze() + save() inside one tool
# invocation so chat-driven calls get the persisted report id back (§28.24).
# Failures surface as a `status='failed'` dict so the handler contract
# stays "always returns a dict".
# ===========================================================================
def _analyze_account_to_dict(
    conn: sqlite3.Connection,
    *,
    target_handle: str,
    target_bio_text: str,
    target_recent_posts_text: str,
    target_url: str | None,
    target_display_name: str | None,
) -> dict[str, Any]:
    # Read niche from settings so the prompt's niche_alignment_with_
    # daniel field has the right context. Niche unset → analysis still
    # runs but the alignment rationale carries "(niche not yet defined)".
    from app.agent import niche as _niche

    niche = _niche.get_niche(conn)
    try:
        analysis = _account_research.analyze(
            target_handle=target_handle,
            target_bio_text=target_bio_text,
            target_recent_posts_text=target_recent_posts_text,
            daniel_niche_problem=niche.problem,
            daniel_niche_person=niche.person,
            target_url=target_url,
            target_display_name=target_display_name,
        )
    except _account_research.AccountResearchError as exc:
        # P510R-20: log full stack for operator diagnosis; tool result
        # gets just the message.
        _LOG.warning("analyze_account tool failed: %s", exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}

    report_id = _account_research.save(
        conn,
        analysis=analysis,
        target_bio_snapshot=target_bio_text,
        target_recent_posts_text=target_recent_posts_text,
        target_url=target_url,
        target_display_name=target_display_name,
    )
    return {
        "status": "saved",
        "report_id": report_id,
        "target_handle": analysis.target_handle,
        "analysis": analysis.to_dict(),
        "tokens_used": analysis.tokens_used,
        "note": (
            "Reply target NOT created automatically. Daniel clicks "
            "'Generate reply target' in §29.7 Account Researcher tab to "
            "promote (§28.24)."
        ),
    }


# ===========================================================================
# Brain Dump tool wrapper — converts BrainDumpResult to a JSON-serializable
# dict for the agent's tool-result message (§28.22).
# ===========================================================================
def _brain_dump_process_to_dict(
    conn: sqlite3.Connection, brain_dump_id: int
) -> dict[str, Any]:
    try:
        result = _brain_dump.process(conn, brain_dump_id)
    except _brain_dump.BrainDumpError as exc:
        # P510R-20: log full stack for operator diagnosis; tool result
        # gets just the message + the row id so the model can suggest
        # a retry.
        _LOG.warning(
            "process_brain_dump tool failed (brain_dump_id=%s): %s",
            brain_dump_id, exc, exc_info=True,
        )
        return {"status": "failed", "brain_dump_id": brain_dump_id, "error": str(exc)}
    return {
        "status": "processed",
        "brain_dump_id": result.brain_dump_id,
        "clarifying_questions": result.clarifying_questions,
        "candidate_drafts": [c.to_dict() for c in result.candidate_drafts],
        "tokens_used": result.tokens_used,
        "note": (
            "candidate_drafts are NOT auto-promoted. Daniel must click "
            "'Send to drafts' on each candidate in §14.9 to invoke "
            "_save_draft_post (§28.22)."
        ),
    }


# AGENT_TOOLS — the registered tool catalog (21 entries after Phase 5.10).
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
            "Score reply candidates against the four MVP dimensions from "
            "§29.3 (relevance, engagement_surface, saturation, reply_opportunity). "
            "Accepts either a list of candidate dicts (creates/refreshes "
            "reply_targets rows) or a reply_target_id (re-scores in place). "
            "engagement_surface and saturation are computed from metrics; "
            "relevance and reply_opportunity are your judgments — supply "
            "them on each candidate. Without both judgments the row is "
            "recorded but recommended_action_label is left NULL."
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
                            "target_post_url": {"type": "string"},
                            "text": {"type": "string"},
                            "author_handle": {"type": "string"},
                            "target_author_follower_count": {"type": "integer"},
                            "like_count": {"type": "integer"},
                            "reply_count": {"type": "integer"},
                            "repost_count": {"type": "integer"},
                            "quote_count": {"type": "integer"},
                            "post_age_minutes": {"type": "integer"},
                            "relevance_score": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 3,
                                "description": "Your judgment of §29.3 relevance dimension.",
                            },
                            "reply_opportunity_score": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 3,
                                "description": "Your judgment of §29.3 reply opportunity dimension.",
                            },
                            "pillar": {"type": "string"},
                            "audience": {"type": "string"},
                            "reply_intent": {
                                "type": "string",
                                "enum": list(REPLY_INTENT_ENUM),
                            },
                            "score_rationale": {"type": "string"},
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
            "bounce as revisions. Do NOT call until the user has approved. "
            "Pass `hypothesis_id` (integer) when the draft tests an experiment "
            "from get_open_hypotheses; pass free-text `hypothesis` for "
            "post_classifications context. Both can coexist."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "cta": {"type": "string"},
                "hypothesis": {
                    "type": "string",
                    "description": (
                        "Free-text hypothesis context stored on "
                        "post_classifications.hypothesis."
                    ),
                },
                "hypothesis_id": {
                    "type": "integer",
                    "description": (
                        "FK to experiments.id — set when the draft tests a "
                        "specific open hypothesis from get_open_hypotheses."
                    ),
                },
                "content_type": {
                    "type": "string",
                    "enum": list(_content_types.CONTENT_TYPES),
                    "description": (
                        "V/G/P/P axis per §28.17. Required. The orchestrator "
                        "refuses 'unspecified' even though the CHECK permits "
                        "it. Pick the one that describes the post's PURPOSE."
                    ),
                },
                "why_posted": {"type": "string"},
                "expected_signal": {"type": "string"},
                "agent_reasoning": {"type": "string"},
            },
            "required": ["text", "pillar", "audience", "cta", "content_type"],
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
                "content_type": {
                    "type": "string",
                    "enum": list(_content_types.CONTENT_TYPES),
                    "description": (
                        "V/G/P/P axis per §28.17. Required. The orchestrator "
                        "refuses 'unspecified'."
                    ),
                },
                "hypothesis": {"type": "string"},
                "agent_reasoning": {"type": "string"},
            },
            "required": ["text", "target_post_url", "content_type"],
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
        name="get_content_type_gaps",
        description=(
            "Counts per V/G/P/P content type over the last window_days "
            "(default 7 from `content_type_recommendation_window_days`). "
            "Returns the under-represented type with a one-line rationale "
            "the agent can quote when Daniel asks 'what should I post "
            "today?'. Read-only (§28.17)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "window_days": {"type": "integer", "default": 7},
            },
        },
        handler=lambda conn, window_days=7: _content_types.get_content_type_gaps(
            conn, window_days=int(window_days)
        ),
    ),
    ToolDef(
        name="score_replier_pool",
        description=(
            "Replier-under-thread discovery path (§28.20, MVP paste flow). "
            "Daniel pastes a thread URL plus a list of replier handles or "
            "text excerpts (one per line, '@handle: excerpt' or plain "
            "handles). Each replier is scored deterministically on the "
            "§29.3 4-dim model PLUS thread_context_fit_score (0-3, "
            "measures niche_person overlap with the replier's text). "
            "Rows land in reply_targets with source='replier_under_thread'. "
            "V1.1+ adds a programmatic top-N reply scan via X API — same "
            "tool signature, optional auto_scan flag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "thread_url": {"type": "string"},
                "replier_handles_or_excerpts_json": {
                    "type": "string",
                    "description": (
                        "Newline-separated replier handles or "
                        "'@handle: excerpt' lines. Multi-line excerpts "
                        "are blank-line-separated."
                    ),
                },
                "lookback_minutes": {"type": "integer", "default": 60},
            },
            "required": ["thread_url", "replier_handles_or_excerpts_json"],
        },
        handler=lambda conn, *, thread_url, replier_handles_or_excerpts_json, lookback_minutes=60: (
            _replier_pool.score_replier_pool(
                conn,
                thread_url=thread_url,
                replier_handles_or_excerpts=replier_handles_or_excerpts_json,
                lookback_minutes=int(lookback_minutes),
            )
        ),
    ),
    ToolDef(
        name="get_velocity_projection",
        description=(
            "Latest v_follower_velocity row: 7d/30d velocity, current "
            "milestone target, distance, and projected hit dates. ALL "
            "projection fields are NULL when |delta_7d| < "
            "velocity_projection_noise_floor_followers (default 10) OR "
            "velocity <= 0 OR the milestone is met — never display a "
            "precise date when the input is noise (§28.19). Use this to "
            "ground velocity questions in real data instead of guessing."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda conn: (
            _velocity.get_velocity_projection(conn).to_dict()
            if _velocity.get_velocity_projection(conn) is not None
            else {"error": "no snapshots — velocity projection unavailable"}
        ),
    ),
    ToolDef(
        name="record_reply_target",
        description=(
            "Add (or enrich) a candidate target in the reply queue. "
            "Idempotent on target_post_url per §29.6 unique index; "
            "re-recording the same URL refreshes the existing row's "
            "metadata without changing scores. After recording, call "
            "score_reply_candidates with reply_target_id to score it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_post_url": {"type": "string"},
                "target_post_text": {"type": "string"},
                "target_user": {"type": "string"},
                "target_author_follower_count": {"type": "integer"},
                "like_count": {"type": "integer"},
                "reply_count": {"type": "integer"},
                "repost_count": {"type": "integer"},
                "quote_count": {"type": "integer"},
                "post_age_minutes": {"type": "integer"},
                "pillar": {"type": "string"},
                "audience": {"type": "string"},
                "reply_intent": {
                    "type": "string",
                    "enum": list(REPLY_INTENT_ENUM),
                },
                "agent_reasoning": {"type": "string"},
                "agent_priority_score": {"type": "integer"},
            },
            "required": ["target_post_url"],
        },
        handler=_record_reply_target,
    ),
    ToolDef(
        name="process_brain_dump",
        description=(
            "Process a brain_dumps row (raw_text → clarifying_questions "
            "+ ≤N candidate_drafts). Writes results back to the same "
            "row; raw_text is NEVER modified. Used by both the §14.9 "
            "Brain Dump view's Process button AND chat-driven invocation "
            "('process my last brain dump'). Candidates are NOT auto-"
            "saved as agent_drafts — promotion is an explicit Daniel "
            "click that runs the full Phase 5.8 pipeline (§28.22)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "brain_dump_id": {"type": "integer"},
            },
            "required": ["brain_dump_id"],
        },
        handler=lambda conn, *, brain_dump_id: _brain_dump_process_to_dict(
            conn, int(brain_dump_id)
        ),
    ),
    ToolDef(
        name="analyze_account",
        description=(
            "Strategic analysis of a target X account (§28.24). Daniel "
            "pastes the target's handle + bio + recent posts text "
            "(one post per `---` separator) and this tool runs a "
            "structured-output Claude pass returning posting patterns, "
            "positioning, reply-strategy entry points, and niche "
            "alignment with Daniel (overlap_score 0-3). Persists to "
            "account_research_reports; the schema permits multiple "
            "reports per handle so each call is a point-in-time "
            "snapshot. External content is wrapped per §28.2 prompt-"
            "injection-defense convention."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_handle": {"type": "string"},
                "target_bio_text": {"type": "string"},
                "target_recent_posts_text": {"type": "string"},
                "target_url": {"type": "string"},
                "target_display_name": {"type": "string"},
            },
            "required": [
                "target_handle",
                "target_recent_posts_text",
            ],
        },
        handler=lambda conn, *, target_handle, target_recent_posts_text,
        target_bio_text="", target_url=None, target_display_name=None: (
            _analyze_account_to_dict(
                conn,
                target_handle=target_handle,
                target_bio_text=target_bio_text,
                target_recent_posts_text=target_recent_posts_text,
                target_url=target_url,
                target_display_name=target_display_name,
            )
        ),
    ),
    ToolDef(
        name="audit_profile",
        description=(
            "Run a comprehensive Profile Audit (§28.25) on Daniel's "
            "X surface: bio + pinned post + recent posts + active "
            "voice profile + niche definition. Reads them together "
            "and returns scored consistency analysis with a load-"
            "bearing top_three_actions field. Daniel pastes bio + "
            "pinned-post text; recent posts are loaded automatically "
            "from the posts table (default 30-day window). Persists "
            "to profile_audits as append-only history."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bio_text": {"type": "string"},
                "pinned_post_text": {"type": "string"},
                "recent_post_window_days": {
                    "type": "integer",
                    "default": 30,
                },
                "pinned_post_id": {"type": "integer"},
            },
            "required": ["bio_text", "pinned_post_text"],
        },
        handler=lambda conn, *, bio_text, pinned_post_text,
        recent_post_window_days=None, pinned_post_id=None: (
            _audit_profile_to_dict(
                conn,
                bio_text=bio_text,
                pinned_post_text=pinned_post_text,
                recent_post_window_days=recent_post_window_days,
                pinned_post_id=pinned_post_id,
            )
        ),
    ),
]


def get_tool(name: str) -> ToolDef:
    """Lookup a registered tool by name. Raises ``KeyError`` on miss."""
    for t in AGENT_TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"unknown agent tool: {name!r}")
