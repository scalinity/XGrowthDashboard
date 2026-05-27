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
from app.agent import autonomy as _autonomy
from app.agent import blog_drafting as _blog_drafting
from app.agent import blog_repurposing as _blog_repurposing
from app.agent import brain_dump as _brain_dump
from app.agent import campaigns as _campaigns
from app.agent import inspiration as _inspiration
from app.agent import monthly_review as _monthly_review
from app.agent import profile_audit as _profile_audit
from app.agent import content_types as _content_types
from app.agent import personality_lore as _personality_lore
from app.agent import prepublish_scorer as _prepublish_scorer
from app.agent import repetition_guard as _repetition_guard
from app.agent import replier_pool as _replier_pool
from app.agent import velocity as _velocity
from app.agent import voice_profile as _voice_profile
from app.agent.lint import (
    is_thread_classifier_lint_enabled,
    thread_classifier_lint,
)
from app.agent.reply_targets import (
    ACTION_TO_SCORE,
    REPLY_INTENT_ENUM,
    ReplyTargetSnapshot,
    apply_velocity_timing_modifiers,
    engagement_surface_score,
    engagement_surface_thresholds,
    resolve_recommended_action,
    saturation_score as _saturation_score_helper,
    timing_score,
    velocity_score,
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
    precomputed_lint=None,  # type: ignore[no-untyped-def]
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

    # ----- §29.10 thread-classifier lint -----
    # Phase 7: classify the target post's thread quality BEFORE Daniel
    # starts drafting. Gated by reply_target_lint_enabled (default true).
    # Distinct from §28.18 reply_quality_lint which fires at draft-save
    # time on Daniel's reply text. The two lints don't see each other.
    #
    # RV2-20: prefer the caller-precomputed lint result so the Haiku
    # round-trip (typical ~500ms) happens OUTSIDE the transaction.
    # Falling back to inline computation preserves backward compatibility
    # for callers that haven't been refactored.
    #
    # Phase 10 follow-up — when precomputed_lint is supplied, skip the
    # settings reads + metrics-dict build entirely. The local
    # lint_enabled / niche_problem / observed_metrics are ONLY used as
    # arguments to thread_classifier_lint, which is bypassed on the
    # precompute path. Pre-fix this was O(N) settings reads per
    # candidate in _score_reply_candidates' batch-scoring loop even
    # though every candidate already had a precomputed lint result.
    if precomputed_lint is not None:
        lint = precomputed_lint
    else:
        lint_setting_row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'reply_target_lint_enabled'"
        ).fetchone()
        lint_enabled = is_thread_classifier_lint_enabled(
            lint_setting_row[0] if lint_setting_row else None
        )
        niche_row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'niche_problem'"
        ).fetchone()
        niche_problem = None
        if niche_row and niche_row[0]:
            try:
                niche_problem = json.loads(niche_row[0])
            except (TypeError, json.JSONDecodeError):
                niche_problem = None
        observed_metrics = {
            "like_count": row["like_count"],
            "reply_count": row["reply_count"],
            "repost_count": row["repost_count"],
        }
        lint = thread_classifier_lint(
            target_post_text=row["target_text"] or "",
            target_author_handle=row["target_author_handle"] or "",
            observed_metrics=observed_metrics,
            niche_problem=niche_problem,
            enabled=lint_enabled,
        )
    lint_blocked = 1 if lint.is_blocking else 0
    lint_category = lint.primary_category
    lint_json = lint.to_json()

    # ----- §29.10 signal-only flags reduce reply_opportunity_score by 1 each -----
    # meme_with_no_serious_reply_path and low_quality_reply_thread are
    # NOT blocks but they degrade the reply opportunity. Each fires
    # subtracts 1 from the persisted score, floored at 0. Only applies
    # when the agent supplied a reply_opportunity_score on this call
    # (we never mutate Daniel's stored value silently).
    if reply_opportunity is not None:
        opp_adjustment = 0
        if lint.meme_with_no_serious_reply_path:
            opp_adjustment += 1
        if lint.low_quality_reply_thread:
            opp_adjustment += 1
        if opp_adjustment > 0:
            reply_opportunity = max(0, int(reply_opportunity) - opp_adjustment)

    # ----- Phase 7 velocity / timing dimensions -----
    # velocity_score derives from reply_target_snapshots history (NULL
    # until the metrics-refresh job has produced ≥2 snapshots).
    # timing_score derives from post_age_minutes + author follower count.
    snap_rows = conn.execute(
        """
        SELECT checked_at_utc, computed_likes_per_hour, computed_replies_per_hour
          FROM reply_target_snapshots
         WHERE reply_target_id = ?
         ORDER BY checked_at_utc DESC
         LIMIT 5
        """,
        (int(reply_target_id),),
    ).fetchall()
    snapshots = [
        ReplyTargetSnapshot(
            checked_at_utc=str(r["checked_at_utc"]),
            computed_likes_per_hour=(
                float(r["computed_likes_per_hour"])
                if r["computed_likes_per_hour"] is not None
                else None
            ),
            computed_replies_per_hour=(
                float(r["computed_replies_per_hour"])
                if r["computed_replies_per_hour"] is not None
                else None
            ),
        )
        for r in reversed(snap_rows)
    ]
    vel = velocity_score(snapshots)
    tim = timing_score(
        int(row["post_age_minutes"] or 0),
        row["target_author_follower_count"],
    )

    # Resolver only runs when all four MVP scores are present. Without
    # relevance + reply_opportunity from the agent, persist what we have
    # and leave recommended_action_* NULL.
    if relevance is not None and reply_opportunity is not None:
        base_label = resolve_recommended_action(
            int(relevance), eng, sat, int(reply_opportunity)
        )
        # §29.3 trailing modifiers — apply velocity/timing AFTER the base
        # ladder. The base resolver's engagement_surface input stays at
        # `eng`; the modifier may bump the surfaced score up one tier
        # AND/OR downgrade the action label.
        adj_eng, label = apply_velocity_timing_modifiers(
            base_engagement_surface=eng,
            base_recommended_action=base_label,
            velocity=vel,
            timing=tim,
        )
        action_score = ACTION_TO_SCORE[label]
        # The persisted engagement_surface_score reflects the bumped tier
        # so the Queue's ORDER BY ranks accelerating threads correctly.
        eng = adj_eng
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
            velocity_score           = ?,
            timing_score             = ?,
            recommended_action_label = ?,
            recommended_action_score = ?,
            score_rationale          = COALESCE(?, score_rationale),
            reply_intent             = COALESCE(?, reply_intent),
            pillar                   = COALESCE(?, pillar),
            audience                 = COALESCE(?, audience),
            lint_thread_classification_json = ?,
            lint_category            = ?,
            lint_blocked             = ?,
            last_checked_at_utc      = datetime('now')
        WHERE id = ?
        """,
        (
            relevance, eng, sat, reply_opportunity,
            vel, tim,
            label, action_score, rationale,
            reply_intent, pillar, audience,
            lint_json, lint_category, lint_blocked,
            int(reply_target_id),
        ),
    )

    return {
        "reply_target_id": int(reply_target_id),
        "relevance_score": relevance if relevance is not None else row["relevance_score"],
        "engagement_surface_score": eng,
        "saturation_score": sat,
        "reply_opportunity_score": reply_opportunity if reply_opportunity is not None else row["reply_opportunity_score"],
        "velocity_score": vel,
        "timing_score": tim,
        "recommended_action_label": label,
        "recommended_action_score": action_score,
        "score_rationale": rationale or row["score_rationale"],
        "lint_blocked": bool(lint_blocked),
        "lint_category": lint_category,
        "lint_rationale": lint.rationale,
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

    # RV2-20: pre-compute the §29.10 thread-classifier lint OUTSIDE the
    # transaction so the Haiku round-trip doesn't hold BEGIN IMMEDIATE
    # across N candidates × ~500ms per call. The setting + niche reads
    # are also moved here so the per-candidate inner block stays
    # write-only.
    _lint_setting_row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'reply_target_lint_enabled'"
    ).fetchone()
    _lint_enabled = is_thread_classifier_lint_enabled(
        _lint_setting_row[0] if _lint_setting_row else None
    )
    _niche_row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'niche_problem'"
    ).fetchone()
    _niche_problem = None
    if _niche_row and _niche_row[0]:
        try:
            _niche_problem = json.loads(_niche_row[0])
        except (TypeError, json.JSONDecodeError):
            _niche_problem = None

    scored: list[dict[str, Any]] = []
    errors: list[str] = []
    for c in candidates:
        url = (c.get("url") or c.get("target_post_url") or "").strip()
        if not url:
            errors.append("candidate missing url")
            continue
        # RV2-20: lint the candidate BEFORE opening the transaction.
        precomputed_lint = thread_classifier_lint(
            target_post_text=(c.get("text") or c.get("target_text") or ""),
            target_author_handle=(
                c.get("author_handle") or c.get("target_author_handle") or ""
            ),
            observed_metrics={
                "like_count": c.get("like_count"),
                "reply_count": c.get("reply_count"),
                "repost_count": c.get("repost_count"),
            },
            niche_problem=_niche_problem,
            enabled=_lint_enabled,
        )
        # /review-2 🔴 #2 — INSERT + metric-refresh + score UPDATE all run
        # inside one transaction so a CHECK failure on the inner UPDATE
        # rolls back the just-minted row instead of orphaning it.
        # /review-2 🟡 #2 — error dicts go to `errors`, not `scored`,
        # so callers reading scored[i]["recommended_action_label"] don't
        # KeyError.
        # RV2-31: track which phase fails so the error string distinguishes
        # INSERT vs UPDATE vs SCORE failures. Pre-RV2-31 the catch-all
        # appended just str(exc); Daniel had no signal for which step
        # broke in a multi-candidate paste.
        phase = "insert"
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
                    phase = "update"
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

                phase = "score"
                result = _compute_and_persist_scores_locked(
                    conn,
                    reply_target_id=rt_id,
                    relevance=c.get("relevance_score"),
                    reply_opportunity=c.get("reply_opportunity_score"),
                    rationale=c.get("score_rationale"),
                    reply_intent=c.get("reply_intent"),
                    pillar=c.get("pillar"),
                    audience=c.get("audience"),
                    precomputed_lint=precomputed_lint,
                )
        except Exception as exc:  # noqa: BLE001 — wrap any DB error per candidate
            # RV2-10: log the full stack for operator diagnosis; the tool
            # result gets just the message. Matches the discipline in
            # _audit_profile_to_dict / _analyze_account_to_dict.
            # RV2-31: phase tag tells Daniel which step failed.
            _LOG.warning(
                "score_reply_candidates %s phase failure for url=%r: %s",
                phase, url, exc, exc_info=True,
            )
            errors.append(f"candidate {url!r}: {phase} failed: {exc}")
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


# Phase 5.11 §28.29 — inspiration transform + plagiarism score tool
# wrappers. transform_inspiration surfaces TransformError as a
# {"status": "failed"} dict so the smoke test holds without
# ANTHROPIC_API_KEY. score_inspiration_plagiarism_risk is pure read,
# never raises in practice.
def _transform_inspiration_to_dict(
    conn: sqlite3.Connection,
    *,
    saved_inspiration_id: int,
    mode: str,
) -> dict[str, Any]:
    try:
        result = _inspiration.transform(
            conn,
            saved_inspiration_id=int(saved_inspiration_id),
            mode=mode,  # type: ignore[arg-type]
        )
    except (_inspiration.InspirationError, _inspiration.TransformError) as exc:
        _LOG.warning(
            "transform_inspiration tool failed (saved_inspiration_id=%s, mode=%s): %s",
            saved_inspiration_id, mode, exc, exc_info=True,
        )
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "transform_id": result.transform_id,
        "saved_inspiration_id": result.saved_inspiration_id,
        "mode": result.transform_mode,
        "output_text": result.output_text,
        "ai_reported_risk_label": result.ai_reported_risk_label,
        "plagiarism_risk_label": result.plagiarism_risk_label,
        "jaccard_similarity": result.jaccard_similarity,
        "longest_shared_ngram_length": result.longest_shared_ngram_length,
        "tokens_used": result.tokens_used,
    }


def _score_inspiration_plagiarism_to_dict(
    conn: sqlite3.Connection,
    *,
    source_text: str,
    output_text: str,
) -> dict[str, Any]:
    read = _inspiration.compute_plagiarism_risk(conn, source_text, output_text)
    return {
        "jaccard_similarity": read.jaccard_similarity,
        "longest_shared_ngram_length": read.longest_shared_ngram_length,
        "deterministic_risk_label": read.deterministic_risk_label,
    }


# Phase 6 §28.32 — blog drafting tool wrappers. Each surfaces
# BlogDraftingError subclasses as {"status": "failed"} dicts so the
# smoke test holds without ANTHROPIC_API_KEY and so a niche-undefined
# refusal lands as data instead of an exception bubbling up.
def _outline_blog_to_dict(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    daniel_notes: str | None = None,
) -> dict[str, Any]:
    try:
        result = _blog_drafting.outline_blog(
            conn, blog_id=int(blog_id), daniel_notes=daniel_notes
        )
    except _blog_drafting.BlogDraftingError as exc:
        _LOG.warning("outline_blog tool failed (blog_id=%s): %s", blog_id, exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "blog_id": result.blog_id,
        "version_id": result.version_id,
        "version_number": result.version_number,
        "outline_markdown": result.outline_markdown,
        "section_count": result.section_count,
        "estimated_length_words": result.estimated_length_words,
        "confidence_label": result.confidence_label,
        "rationale": result.rationale,
        "tokens_used": result.tokens_used,
    }


def _draft_blog_to_dict(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    target_length_words: int | None = None,
) -> dict[str, Any]:
    try:
        result = _blog_drafting.draft_blog(
            conn,
            blog_id=int(blog_id),
            target_length_words=(
                int(target_length_words) if target_length_words is not None else None
            ),
        )
    except _blog_drafting.BlogDraftingError as exc:
        _LOG.warning("draft_blog tool failed (blog_id=%s): %s", blog_id, exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "blog_id": result.blog_id,
        "version_id": result.version_id,
        "version_number": result.version_number,
        "body_markdown": result.body_markdown,
        "word_count": result.word_count,
        "sections_used": list(result.sections_used),
        "confidence_label": result.confidence_label,
        "notes": result.notes,
        "tokens_used": result.tokens_used,
    }


def _suggest_blog_edits_to_dict(
    conn: sqlite3.Connection, *, blog_id: int
) -> dict[str, Any]:
    try:
        result = _blog_drafting.suggest_blog_edits(conn, blog_id=int(blog_id))
    except _blog_drafting.BlogDraftingError as exc:
        _LOG.warning(
            "suggest_blog_edits tool failed (blog_id=%s): %s", blog_id, exc, exc_info=True
        )
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "ok",
        "blog_id": result.blog_id,
        "suggestions": [
            {
                "paragraph_anchor": s.paragraph_anchor,
                "suggested_replacement": s.suggested_replacement,
                "rationale": s.rationale,
                "confidence_label": s.confidence_label,
            }
            for s in result.suggestions
        ],
        "overall_confidence_label": result.overall_confidence_label,
        "summary": result.summary,
        "tokens_used": result.tokens_used,
    }


def _repurpose_blog_to_x_to_dict(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    mode: str,
    override_plagiarism: bool = False,
) -> dict[str, Any]:
    try:
        result = _blog_repurposing.repurpose_blog_to_x(
            conn,
            blog_id=int(blog_id),
            mode=mode,  # type: ignore[arg-type]
            override_plagiarism=bool(override_plagiarism),
        )
    except _blog_repurposing.PlagiarismBlockedError as exc:
        _LOG.info(
            "repurpose_blog_to_x blocked by plagiarism guard (blog_id=%s): %d items",
            blog_id, len(exc.blocked_outputs),
        )
        return {
            "status": "plagiarism_blocked",
            "blocked_outputs": exc.blocked_outputs,
            "error": str(exc),
        }
    except _blog_repurposing.BlogRepurposingError as exc:
        _LOG.warning(
            "repurpose_blog_to_x tool failed (blog_id=%s, mode=%s): %s",
            blog_id, mode, exc, exc_info=True,
        )
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "blog_id": result.blog_id,
        "mode": result.mode,
        "drafts": [
            {
                "draft_id": d.draft_id,
                "text": d.text,
                "section_anchor": d.section_anchor,
                "confidence_label": d.confidence_label,
                "plagiarism_risk_label": d.plagiarism_risk_label,
                "jaccard_similarity": d.jaccard_similarity,
                "longest_shared_ngram_length": d.longest_shared_ngram_length,
                "plagiarism_override_used": d.plagiarism_override_used,
            }
            for d in result.drafts
        ],
        "overall_confidence_label": result.overall_confidence_label,
        "rationale": result.rationale,
        "tokens_used": result.tokens_used,
    }


def _repurpose_x_to_blog_idea_to_dict(
    conn: sqlite3.Connection, *, post_id: int
) -> dict[str, Any]:
    try:
        result = _blog_repurposing.repurpose_x_to_blog_idea(
            conn, post_id=int(post_id)
        )
    except _blog_repurposing.BlogRepurposingError as exc:
        _LOG.warning(
            "repurpose_x_to_blog_idea tool failed (post_id=%s): %s",
            post_id, exc, exc_info=True,
        )
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "post_id": result.post_id,
        "new_blog_id": result.new_blog_id,
        "title": result.title,
        "outline_markdown": result.outline_markdown,
        "target_length_words": result.target_length_words,
        "pillar_recommendation": result.pillar_recommendation,
        "audience_recommendation": result.audience_recommendation,
        "confidence_label": result.confidence_label,
        "rationale": result.rationale,
        "blog_to_post_link_id": result.blog_to_post_link_id,
        "tokens_used": result.tokens_used,
    }


def _generate_blog_seo_metadata_to_dict(
    conn: sqlite3.Connection, *, blog_id: int
) -> dict[str, Any]:
    try:
        result = _blog_drafting.generate_blog_seo_metadata(conn, blog_id=int(blog_id))
    except _blog_drafting.BlogDraftingError as exc:
        _LOG.warning(
            "generate_blog_seo_metadata tool failed (blog_id=%s): %s",
            blog_id, exc, exc_info=True,
        )
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "saved",
        "blog_id": result.blog_id,
        "seo_title": result.seo_title,
        "seo_description": result.seo_description,
        "seo_tags": list(result.seo_tags),
        "confidence_label": result.confidence_label,
        "rationale": result.rationale,
        "tokens_used": result.tokens_used,
    }


# Phase 5.11 §28.27 — mirror of the weekly draft tool for monthly
# reviews. Same stub status as the weekly version; the Anthropic call
# wiring lands when Session 2 of the agent draft pipeline ships.
def _draft_monthly_review_section(
    conn: sqlite3.Connection,
    *,
    section_name: str,
    iso_month: str,
) -> dict[str, Any]:
    allowed = {
        "interpretation",
        "lesson",
        "counterfactual",
        "next_month_experiment",
        "campaigns_retro",
    }
    if section_name not in allowed:
        return {"error": f"unknown section_name {section_name!r}"}
    try:
        _monthly_review.parse_iso_month(iso_month)
    except _monthly_review.InvalidIsoMonthError as exc:
        return {"error": str(exc)}
    # Surface auto-filled context so the (future) Session-2 prompt has
    # everything it needs to draft. Pure read; safe to call here.
    auto_filled = _monthly_review.compute_auto_filled_fields(conn, iso_month)
    return {
        "section_name": section_name,
        "iso_month": iso_month,
        "draft_text": None,
        "auto_filled": {
            "follower_delta": auto_filled.follower_delta,
            "posts_shipped": auto_filled.posts_shipped,
            "downloads": auto_filled.downloads,
            "strongest_pillar_candidate": auto_filled.strongest_pillar_candidate,
            "strongest_content_type": auto_filled.strongest_content_type,
            "weakest_content_type": auto_filled.weakest_content_type,
            "campaigns_completed_json": auto_filled.campaigns_completed_json,
        },
        "note": (
            "Session-1 stub: section name validated, auto-fill payload "
            "surfaced for Session-2 prompt wiring per §28.27."
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
        #
        # Phase 10 C2 fix — screenshot_test (10th dim) is the only
        # network-bound dimension. We SKIP it inside the transaction
        # via the skip_screenshot_caller sentinel (returns None) so the
        # writer lock isn't held across the Haiku call; the post-commit
        # follow-up below fires the call out-of-band and UPDATEs the
        # row.
        score_row = _prepublish_scorer.score(
            draft_text=text,
            draft_kind="standalone",
            pillar=pillar,
            cta=cta,
            target_post_text=None,
            active_voice_profile=_voice_profile.get_active(conn),
            conn=conn,  # Phase 10 / §28.11 — read screenshot floor from settings.
            screenshot_test_caller=_prepublish_scorer.skip_screenshot_caller,
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

    # Phase 10 C2 fix — screenshot Haiku call OUTSIDE the write
    # transaction. update_screenshot_score may take up to 60s on a
    # transient API hiccup; running it here means the writer lock is
    # released first so concurrent launchd jobs / Streamlit reruns
    # don't SQLITE_BUSY. The follow-up UPDATE is itself a narrow tx.
    ss_score, ss_label = _prepublish_scorer.update_screenshot_score(
        conn,
        agent_draft_id=draft_id,
        draft_text=text,
        active_voice_profile=_voice_profile.get_active(conn),
    )
    # Return the post-commit label when the screenshot signal landed —
    # otherwise the original composite_label from inside the tx wins.
    prepublish_label = ss_label or score_row.composite_label

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "draft_url": f"/?draft_id={draft_id}",
        "prepublish_label": prepublish_label,
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
    reply_quality_lint_failure_mode: str | None = None,
    reply_intent: str | None = None,  # noqa: ARG001 — Phase 10 §29.5 promotion; dispatcher validates BEFORE this handler runs, so the value just rides through for the existing reply_targets wiring (no new column on agent_drafts).
) -> dict[str, Any]:
    import re as _re

    # Phase 5.9 / §28.17 — required, same enforcement as posts.
    ct = _content_types.validate_for_save(content_type)
    # Phase 5.9 / §28.18 — persistence: None when the lint wasn't run
    # (dispatcher didn't inject), 1/0 from the dispatcher-injected
    # decision.reply_quality_result.passed.
    rq_persist = (
        None if reply_quality_lint_passed is None
        else (1 if reply_quality_lint_passed else 0)
    )
    # Phase 10 / §28.18 — failure_mode persistence. Per spec: populated
    # only when passed=False; NULL on pass. The dispatcher omits the
    # injection when passed=True so the default None argument keeps
    # the column NULL via the schema CHECK contract.
    #
    # Phase 10 W2 — defense in depth: validate against the canonical
    # eleven-value enum before the INSERT. If the value isn't recognized
    # we LOG and coerce to NULL rather than letting an IntegrityError
    # roll back the entire draft + post + scorer + repetition-guard
    # transaction. Mirrors the validate_for_save(content_type) pattern
    # above; matches how reply_intent is validated by the dispatcher.
    from app.agent import lint as _lint  # local — avoid circular import at module load
    rq_failure_mode_persist: str | None = (
        reply_quality_lint_failure_mode if rq_persist == 0 else None
    )
    if (
        rq_failure_mode_persist is not None
        and rq_failure_mode_persist not in _lint.REPLY_QUALITY_FAILURE_MODES
    ):
        _LOG.warning(
            "_save_draft_reply: rejecting unknown reply_quality_lint_failure_mode=%r "
            "(valid values: %s); coercing to NULL to preserve the draft.",
            rq_failure_mode_persist, list(_lint.REPLY_QUALITY_FAILURE_MODES),
        )
        rq_failure_mode_persist = None
    with transaction(conn):
        draft_cur = conn.execute(
            """
            INSERT INTO agent_drafts
                (session_id, conversation_id, draft_kind, text, pillar,
                 target_post_url, target_post_text, agent_reasoning,
                 voice_self_score, iwh_attempt_index, status,
                 confidence_label, content_type, reply_quality_lint_passed,
                 reply_quality_lint_failure_mode)
            VALUES (?, ?, 'reply', ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
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
                rq_failure_mode_persist,
            ),
        )
        draft_id = int(draft_cur.lastrowid)

        # P8R-2: extract the target's X post id from target_post_url so
        # posts.in_reply_to_post_id matches the column's TEXT-snowflake
        # contract (migrations/001 line 111 + app/forms/post_log.py). The
        # Phase 8 publish wrapper reads this column directly to build the
        # X API reply body. Falls back to NULL when the URL doesn't match
        # /status/<id>/ — publish.py's _resolve_reply_target_x_post_id
        # has a secondary parse-target_post_url fallback for that case.
        _status_id_match = _re.search(
            r"/status(?:es)?/(\d+)", target_post_url or ""
        )
        in_reply_to_x_id = _status_id_match.group(1) if _status_id_match else None

        post_cur = conn.execute(
            """
            INSERT INTO posts
                (created_at_utc, created_date, text, type, posted_via,
                 manual_confirmation_status, agent_draft_id, content_type,
                 in_reply_to_post_id)
            VALUES (datetime('now'), date('now'), ?, 'reply',
                    'agent_assisted', 'draft', ?, ?, ?)
            """,
            (text, draft_id, ct, in_reply_to_x_id),
        )
        post_id = int(post_cur.lastrowid)

        conn.execute(
            "UPDATE agent_drafts SET final_post_id = ? WHERE id = ?",
            (post_id, draft_id),
        )

        # Phase 5.8 / §28.11 — same scoring pass for replies. Includes
        # the reply_substance dimension keyed off target_post_text.
        # Phase 10 C2 fix — skip the screenshot Haiku call inside the
        # write transaction (see _save_draft_post for the rationale);
        # the post-commit update_screenshot_score below fires it.
        score_row = _prepublish_scorer.score(
            draft_text=text,
            draft_kind="reply",
            pillar=pillar,
            cta=None,
            target_post_text=target_post_text,
            active_voice_profile=_voice_profile.get_active(conn),
            conn=conn,  # Phase 10 / §28.11 — read screenshot floor from settings.
            screenshot_test_caller=_prepublish_scorer.skip_screenshot_caller,
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

    # Phase 10 C2 fix — screenshot Haiku call OUTSIDE the write
    # transaction. Mirror of the _save_draft_post post-commit path.
    ss_score, ss_label = _prepublish_scorer.update_screenshot_score(
        conn,
        agent_draft_id=draft_id,
        draft_text=text,
        active_voice_profile=_voice_profile.get_active(conn),
    )
    prepublish_label = ss_label or score_row.composite_label

    return {
        "draft_id": draft_id,
        "post_id": post_id,
        "iwh_attempt_index": int(iwh_attempt_index),
        "target_post_url": target_post_url,
        "prepublish_label": prepublish_label,
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
               reply_quality_lint_passed, reply_quality_lint_failure_mode
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
                 content_type, confidence_label, reply_quality_lint_passed,
                 reply_quality_lint_failure_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
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
                # Phase 10 W1 — propagate the §28.18 failure_mode so a
                # revised draft preserves the parent's lint audit trail.
                # The dispatcher only saves drafts when lint passed, so
                # parent rows usually carry NULL here, but _save_draft_reply
                # is callable directly (Phase 10 tests do this).
                src["reply_quality_lint_failure_mode"],
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
    bio_text: str = "",
    pinned_post_text: str,
    recent_post_window_days: int | None,
    pinned_post_id: int | None,
    auto_pull_bio: bool = False,
) -> dict[str, Any]:
    # Phase 7 §28.25 — when auto_pull_bio=True AND bio_text is empty,
    # fetch Daniel's bio via xurl /2/users/by/username/<daniel_handle>
    # ?user.fields=description. Pinned post, recent posts, voice profile,
    # and niche remain Daniel-supplied or read from settings — the
    # other audit composition steps are unaffected by Phase 7 (per §28.25).
    if auto_pull_bio and not bio_text.strip():
        from app import x_client  # local — Phase 7-only dependency

        # Resolve Daniel's handle from settings. RV2-8: validate before
        # interpolating into the xurl URL path.
        handle_row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'x_handle'"
        ).fetchone()
        daniel_handle: str = "dannyscalant"
        if handle_row and handle_row[0]:
            try:
                raw = json.loads(handle_row[0]) or daniel_handle
                daniel_handle = x_client.validate_x_handle(raw)
            except (TypeError, json.JSONDecodeError, ValueError):
                # Fall back to the hardcoded default rather than letting
                # an invalid x_handle setting compromise the URL path.
                pass

        try:
            resp = x_client.request(
                f"/2/users/by/username/{daniel_handle}?user.fields=description",
                method="GET",
                conn=conn,
                log_source="xurl",
                log_notes="audit_profile auto_pull_bio",
            )
        except x_client.XApiError as exc:
            return {
                "status": "failed",
                "error": (
                    f"auto_pull_bio failed for @{daniel_handle} "
                    f"({type(exc).__name__}: {exc}); fall back to paste."
                ),
            }
        body = resp.body if isinstance(resp.body, dict) else {}
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            bio_text = str(data.get("description") or "").strip()
        if not bio_text:
            return {
                "status": "failed",
                "error": (
                    f"auto_pull_bio returned empty description for @{daniel_handle}; "
                    "fall back to paste."
                ),
            }

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
    target_bio_text: str = "",
    target_recent_posts_text: str = "",
    target_url: str | None = None,
    target_display_name: str | None = None,
    auto_pull: bool = False,
) -> dict[str, Any]:
    # Phase 7 §28.24 programmatic auto-pull. When auto_pull=True, fetch
    # bio + recent posts via xurl and replace any empty paste fields.
    # Paste fields supplied explicitly always win over auto-pull —
    # Daniel can mix-and-match (auto-pull recent posts, paste a richer
    # bio annotated with his own context, etc.). The fall-back-to-paste
    # discipline lives at the tool boundary: a failed X API call returns
    # a 'failed' status dict so the caller can prompt for paste.
    from app.agent import niche as _niche

    if auto_pull:
        from app import x_client  # local — Phase 7-only dependency

        # RV2-8: validate handle shape at the tool boundary so we never
        # interpolate '/', '?', '&', '..', '%' into the xurl URL path.
        try:
            handle_clean = x_client.validate_x_handle(target_handle)
        except ValueError as exc:
            return {
                "status": "failed",
                "error": f"auto_pull requires a valid X handle: {exc}",
            }

        # Endpoint 1 — bio + follower count.
        try:
            user_resp = x_client.request(
                f"/2/users/by/username/{handle_clean}"
                f"?user.fields=description,public_metrics,name",
                method="GET",
                conn=conn,
                log_source="xurl",
                log_notes="analyze_account auto_pull (user)",
            )
        except x_client.XApiError as exc:
            return {
                "status": "failed",
                "error": (
                    f"auto_pull user lookup failed ({type(exc).__name__}: {exc}); "
                    "fall back to manual paste."
                ),
            }
        user_body = user_resp.body if isinstance(user_resp.body, dict) else {}
        user_data = user_body.get("data") if isinstance(user_body, dict) else None
        if not isinstance(user_data, dict):
            return {
                "status": "failed",
                "error": (
                    f"auto_pull user lookup returned no 'data' object for "
                    f"@{handle_clean}; fall back to manual paste."
                ),
            }
        if not target_bio_text.strip():
            target_bio_text = str(user_data.get("description") or "").strip()
        if target_display_name is None:
            target_display_name = user_data.get("name") or None
        target_user_id = str(user_data.get("id") or "")

        # Endpoint 2 — recent posts.
        if target_user_id and not target_recent_posts_text.strip():
            try:
                tweets_resp = x_client.request(
                    f"/2/users/{target_user_id}/tweets"
                    f"?max_results=20&tweet.fields=created_at,text",
                    method="GET",
                    conn=conn,
                    log_source="xurl",
                    log_notes="analyze_account auto_pull (recent posts)",
                )
            except x_client.XApiError as exc:
                return {
                    "status": "failed",
                    "error": (
                        f"auto_pull tweets lookup failed for @{handle_clean} "
                        f"({type(exc).__name__}: {exc}); fall back to manual paste."
                    ),
                }
            tweets_body = (
                tweets_resp.body if isinstance(tweets_resp.body, dict) else {}
            )
            tweets = tweets_body.get("data") if isinstance(tweets_body, dict) else None
            if isinstance(tweets, list):
                joined = "\n---\n".join(
                    str(t.get("text") or "").strip()
                    for t in tweets
                    if isinstance(t, dict) and t.get("text")
                )
                target_recent_posts_text = joined

        if not target_recent_posts_text.strip():
            return {
                "status": "failed",
                "error": (
                    f"auto_pull yielded no recent posts for @{handle_clean}; "
                    "fall back to manual paste of one post per --- separator."
                ),
            }

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


# AGENT_TOOLS — the registered tool catalog. The list has grown across
# phases; keep tests source-of-truth on the actual registry rather than a
# hard-coded count.
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
            "IWH + lint preflight as save_draft_post. "
            "You MUST declare reply_intent before drafting per §29.5 — "
            "this is the orthogonal fourth axis (alongside pillar / "
            "audience / cta) that names your strategic goal for THIS "
            "specific reply. If you don't know which intent applies, "
            "SKIP the reply — that's a valid choice. The "
            "reply_intent_required setting (§29.5 Phase 10) toggles "
            "this enforcement off only as a calibration escape hatch."
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
                # Phase 10 / §29.5 — reply_intent promoted to a required
                # tool argument. Dispatcher validates the value against
                # REPLY_INTENT_ENUM before the handler runs. The
                # reply_intent_required setting (default ON) gates the
                # enforcement; when OFF, the dispatcher accepts NULL
                # and writes it through.
                "reply_intent": {
                    "type": "string",
                    "enum": list(REPLY_INTENT_ENUM),
                    "description": (
                        "§29.5 strategic goal for THIS reply. One of: "
                        + ", ".join(REPLY_INTENT_ENUM) + ". Required when "
                        "reply_intent_required setting is true (default). "
                        "Skip the reply if you genuinely cannot pick an "
                        "intent — drafting without one indicates the "
                        "reply isn't worth posting."
                    ),
                },
                "hypothesis": {"type": "string"},
                "agent_reasoning": {"type": "string"},
            },
            "required": ["text", "target_post_url", "content_type", "reply_intent"],
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
                        "are blank-line-separated. Empty allowed when "
                        "auto_scan=true (Phase 7 X API path)."
                    ),
                },
                "lookback_minutes": {"type": "integer", "default": 60},
                # RV2-1: declare the Phase 7 §28.20 auto-pull flag in the
                # schema so the agent can actually trigger the xurl path.
                "auto_scan": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, calls xurl /2/tweets/search/recent?"
                        "query=conversation_id:<id> instead of consuming "
                        "the paste payload. Manual paste remains the "
                        "always-available fallback when auto_scan=false."
                    ),
                },
            },
            # Paste payload only required when auto_scan=False; the
            # function defaults it to empty string when auto_scan=True.
            "required": ["thread_url"],
        },
        handler=lambda conn, *, thread_url, replier_handles_or_excerpts_json="",
        lookback_minutes=60, auto_scan=False: (
            _replier_pool.score_replier_pool(
                conn,
                thread_url=thread_url,
                replier_handles_or_excerpts=replier_handles_or_excerpts_json,
                lookback_minutes=int(lookback_minutes),
                auto_scan=bool(auto_scan),
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
                # RV2-1: declare the Phase 7 §28.24 auto-pull flag.
                "auto_pull": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, calls xurl /2/users/by/username/<handle> "
                        "+ /2/users/<id>/tweets to populate bio + recent "
                        "posts instead of using paste payload. Paste fields "
                        "supplied explicitly always win over auto-pull. "
                        "Manual paste remains the always-available fallback."
                    ),
                },
            },
            # target_recent_posts_text only required when auto_pull=False.
            "required": ["target_handle"],
        },
        handler=lambda conn, *, target_handle, target_recent_posts_text="",
        target_bio_text="", target_url=None, target_display_name=None,
        auto_pull=False: (
            _analyze_account_to_dict(
                conn,
                target_handle=target_handle,
                target_bio_text=target_bio_text,
                target_recent_posts_text=target_recent_posts_text,
                target_url=target_url,
                target_display_name=target_display_name,
                auto_pull=bool(auto_pull),
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
                # RV2-1: declare the Phase 7 §28.25 auto-pull flag.
                "auto_pull_bio": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true AND bio_text is empty, calls xurl "
                        "/2/users/by/username/<daniel_handle>?user.fields="
                        "description to populate the bio snapshot. "
                        "Manual paste remains the always-available fallback; "
                        "explicit bio_text always wins over auto-pull."
                    ),
                },
            },
            # bio_text only required when auto_pull_bio=False.
            "required": ["pinned_post_text"],
        },
        handler=lambda conn, *, pinned_post_text, bio_text="",
        recent_post_window_days=None, pinned_post_id=None,
        auto_pull_bio=False: (
            _audit_profile_to_dict(
                conn,
                bio_text=bio_text,
                pinned_post_text=pinned_post_text,
                recent_post_window_days=recent_post_window_days,
                pinned_post_id=pinned_post_id,
                auto_pull_bio=bool(auto_pull_bio),
            )
        ),
    ),
    # ----- #22 draft_monthly_review_section (Phase 5.11 §28.27) -----
    # Mirror of #9 draft_weekly_review_section. Validates section_name
    # (incl. the new `campaigns_retro` section that pulls from
    # campaigns_completed_json) and returns a stub draft pending the
    # Session-2 wiring of the Anthropic call — same shape as the
    # weekly tool, which is itself still a Session-1 stub.
    ToolDef(
        name="draft_monthly_review_section",
        description=(
            "Draft one section of a Monthly AI review (§28.27): "
            "'interpretation', 'lesson', 'counterfactual', "
            "'next_month_experiment', or 'campaigns_retro' (the new "
            "section that pulls from campaigns_completed_json). "
            "Returns a structured payload with the section name + a "
            "draft_text field. Emits <confidence>fact|inference|"
            "speculation|mixed</confidence> tags per §28.14."
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
                        "next_month_experiment",
                        "campaigns_retro",
                    ],
                },
                "iso_month": {
                    "type": "string",
                    "description": "YYYY-MM (e.g. '2026-05').",
                },
            },
            "required": ["section_name", "iso_month"],
        },
        handler=lambda conn, *, section_name, iso_month: (
            _draft_monthly_review_section(
                conn, section_name=section_name, iso_month=iso_month
            )
        ),
    ),
    # ----- #23 transform_inspiration (Phase 5.11 §28.29) -----
    # Runs one transform mode against a saved inspiration. Persists
    # the row and the final plagiarism_risk_label (max of AI-reported
    # + deterministic). Surfaces TransformError as a {status: failed}
    # dict so the smoke test holds without ANTHROPIC_API_KEY.
    ToolDef(
        name="transform_inspiration",
        description=(
            "Run one transform mode against a saved inspiration "
            "(§28.29). Modes: structure | hook_pattern | counterpoint | "
            "original_version | voice_profile_version | expand | "
            "compress. Persists the row and computes the deterministic "
            "+ AI-reported plagiarism guard; the FINAL "
            "plagiarism_risk_label is max(ai_reported, deterministic) "
            "so the AI cannot underreport when token overlap is high."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "saved_inspiration_id": {"type": "integer"},
                "mode": {
                    "type": "string",
                    "enum": list(_inspiration.TRANSFORM_MODES),
                },
            },
            "required": ["saved_inspiration_id", "mode"],
        },
        handler=lambda conn, *, saved_inspiration_id, mode: (
            _transform_inspiration_to_dict(
                conn, saved_inspiration_id=saved_inspiration_id, mode=mode
            )
        ),
    ),
    # ----- #24 score_inspiration_plagiarism_risk (Phase 5.11 §28.29) -----
    # Pure read-only sanity-check tool. Lets the agent score a
    # candidate output against a source independently of the transform
    # path — useful when the model is deciding whether to call
    # transform_inspiration at all.
    ToolDef(
        name="score_inspiration_plagiarism_risk",
        description=(
            "Compute the deterministic plagiarism read between an "
            "external source and a candidate output (§28.29). Returns "
            "jaccard_similarity, longest_shared_ngram_length, and the "
            "deterministic_risk_label (low / medium / high). Read-only "
            "— no persistence. Use to sanity-check a candidate before "
            "spending tokens on transform_inspiration."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_text": {"type": "string"},
                "output_text": {"type": "string"},
            },
            "required": ["source_text", "output_text"],
        },
        handler=lambda conn, *, source_text, output_text: (
            _score_inspiration_plagiarism_to_dict(
                conn, source_text=source_text, output_text=output_text
            )
        ),
    ),
    # ----- #21 analyze_campaign_progress (Phase 5.11 §28.26) -----
    # Read-only structured progress payload for one campaign. Powers
    # the §14.12 "Ask the agent for ideas" affordance and any chat-
    # driven "how's campaign N going?" question. The agent doesn't
    # transition campaign state via tools — every transition is a
    # Daniel-click in the §14.12 view that goes through the audit-
    # logged server-side path.
    ToolDef(
        name="analyze_campaign_progress",
        description=(
            "Return a structured read-only progress report for one "
            "campaign (§28.26): status, days_remaining, item counts, "
            "linked-posts summary, and success-criteria progress with "
            "on_track flags. Use to ground 'how's this campaign going' "
            "questions and to propose new items for the agent-chat "
            "'Ask the agent for ideas' flow. Read-only — campaign "
            "state changes happen via the Campaigns view, not the agent."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer"},
            },
            "required": ["campaign_id"],
        },
        handler=lambda conn, *, campaign_id: _campaigns.analyze_progress(
            conn, campaign_id=int(campaign_id)
        ),
    ),
    # ----- #25 outline_blog (Phase 6 §28.32) -----
    # Generates a structured Markdown outline for one blog. Persists
    # via blogs.save_blog(... agent_action='outline'). Refuses when
    # niche is undefined (§28.16 rule #15). Emits <confidence> tags.
    ToolDef(
        name="outline_blog",
        description=(
            "Produce a structured Markdown outline for one of Daniel's "
            "long-form blogs (§28.32). Reads the unified identity stack "
            "(niche, voice profile, voice samples, lore) AND the blog's "
            "current title/pillar/audience/notes. Writes a blog_versions "
            "row with agent_action='outline'. Refuses if niche is "
            "undefined. Emit <confidence> tags."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "blog_id": {"type": "integer"},
                "daniel_notes": {"type": "string"},
            },
            "required": ["blog_id"],
        },
        handler=lambda conn, *, blog_id, daniel_notes=None: (
            _outline_blog_to_dict(conn, blog_id=blog_id, daniel_notes=daniel_notes)
        ),
    ),
    # ----- #26 draft_blog (Phase 6 §28.32) -----
    # Full draft body from the current outline. Refuses if no outline
    # exists. Writes a blog_versions row with agent_action='draft'.
    ToolDef(
        name="draft_blog",
        description=(
            "Produce a full long-form blog draft from the current "
            "outline (§28.32). Reads identity stack + outline + prior "
            "body if any. Writes a blog_versions row with "
            "agent_action='draft'. Requires the blog to have an "
            "outline_markdown populated; refuses otherwise. Emit "
            "<confidence> tags."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "blog_id": {"type": "integer"},
                "target_length_words": {"type": "integer"},
            },
            "required": ["blog_id"],
        },
        handler=lambda conn, *, blog_id, target_length_words=None: (
            _draft_blog_to_dict(
                conn, blog_id=blog_id, target_length_words=target_length_words
            )
        ),
    ),
    # ----- #27 suggest_blog_edits (Phase 6 §28.32) -----
    # Per-paragraph rewrite suggestions. NEVER auto-applies — UI
    # surfaces with Accept / Reject / Modify. No version row from this
    # tool itself; Accept calls save_blog(... agent_action='edit_suggestion_applied').
    ToolDef(
        name="suggest_blog_edits",
        description=(
            "Survey a blog draft and propose per-paragraph rewrites "
            "(§28.32). Returns a structured list — does NOT auto-apply. "
            "Each suggestion carries paragraph_anchor + "
            "suggested_replacement + rationale + confidence_label. The "
            "editor's UI accepts/rejects/modifies each one; only "
            "accepted edits write to the body via save_blog."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "blog_id": {"type": "integer"},
            },
            "required": ["blog_id"],
        },
        handler=lambda conn, *, blog_id: _suggest_blog_edits_to_dict(
            conn, blog_id=blog_id
        ),
    ),
    # ----- #28 generate_blog_seo_metadata (Phase 6 §28.32) -----
    # SEO sidecar. Writes blogs.seo_* columns directly; NO version row
    # (SEO is metadata, not content).
    ToolDef(
        name="generate_blog_seo_metadata",
        description=(
            "Generate SEO metadata (title, description, tags) from a "
            "blog body + niche context (§28.32). Writes blogs.seo_title "
            "/ seo_description / seo_tags_json DIRECTLY — no version "
            "row is created because SEO metadata is sidecar, not content. "
            "Emit <confidence> tags."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "blog_id": {"type": "integer"},
            },
            "required": ["blog_id"],
        },
        handler=lambda conn, *, blog_id: _generate_blog_seo_metadata_to_dict(
            conn, blog_id=blog_id
        ),
    ),
    # ----- #29 repurpose_blog_to_x (Phase 6 §28.34) -----
    # blog → X. Three modes. Plagiarism guard mandatory (§28.29 floor)
    # against the source blog body; high overlap blocks the
    # drafts-pipeline insert until Daniel sets override_plagiarism=True.
    # Linkage rows in blog_to_post_links land at SHIP time, not draft
    # time (drafts may be discarded).
    ToolDef(
        name="repurpose_blog_to_x",
        description=(
            "Convert one of Daniel's blogs into X drafts (§28.34). "
            "Modes: thread_from_sections (one post per H2), "
            "single_post_summary (one post), teaser_with_link (hook + "
            "URL). Every output runs through the §28.29 plagiarism "
            "floor against the source blog body — high overlap returns "
            "status='plagiarism_blocked' until Daniel passes "
            "override_plagiarism=true. Drafts flow into agent_drafts "
            "via the standard pipeline; blog_to_post_links rows land "
            "at ship time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "blog_id": {"type": "integer"},
                "mode": {
                    "type": "string",
                    "enum": list(_blog_repurposing.VALID_REPURPOSE_MODES),
                },
                "override_plagiarism": {"type": "boolean", "default": False},
            },
            "required": ["blog_id", "mode"],
        },
        handler=lambda conn, *, blog_id, mode, override_plagiarism=False: (
            _repurpose_blog_to_x_to_dict(
                conn, blog_id=blog_id, mode=mode,
                override_plagiarism=override_plagiarism,
            )
        ),
    ),
    # ----- #30 repurpose_x_to_blog_idea (Phase 6 §28.34) -----
    # X post → new blog row with status='idea' + outline + niche
    # snapshots + immediate blog_to_post_links row.
    ToolDef(
        name="repurpose_x_to_blog_idea",
        description=(
            "Expand a shipped X post into a new blog idea (§28.34). "
            "Creates a blogs row with status='idea', seeds the outline "
            "via the agent's structured output, snapshots niche, and "
            "inserts a blog_to_post_links(direction='post_to_blog') row "
            "immediately (linkage is unambiguous at idea creation)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "post_id": {"type": "integer"},
            },
            "required": ["post_id"],
        },
        handler=lambda conn, *, post_id: _repurpose_x_to_blog_idea_to_dict(
            conn, post_id=post_id
        ),
    ),
    # ----- autonomous operator tools (local-only; publish remains internal-only) -----
    ToolDef(
        name="run_local_bash",
        description=(
            "Run a project-scoped local bash command on Daniel's machine for "
            "XGrowth work. Use this when the work requires invoking uv, scripts, "
            "sqlite-utils, git inspection, or other local project commands. The "
            "tool does not ask for per-command permission, but cwd is confined to "
            "the project root, runtime/output are bounded, env-file access is "
            "blocked, and destructive machine-level commands are refused."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "default": "."},
                "timeout_seconds": {"type": "number", "default": 30},
                "purpose": {"type": "string"},
            },
            "required": ["command"],
        },
        handler=lambda conn, *, command, cwd=".", timeout_seconds=30, purpose=None: (
            _autonomy.run_bash_command(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                purpose=purpose,
            )
        ),
    ),
    ToolDef(
        name="query_x_api",
        description=(
            "Run a read-only X API v2 GET request through the existing xurl "
            "client and raw-response audit path. Use this to fetch live X data "
            "when dashboard rows are stale or missing. This tool never publishes; "
            "POST /2/tweets remains internal-only and confirmation-gated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "endpoint": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 30},
            },
            "required": ["endpoint"],
        },
        handler=lambda conn, *, endpoint, timeout_seconds=30: _autonomy.query_x_api_get(
            conn,
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
        ),
    ),
]


def get_tool(name: str) -> ToolDef:
    """Lookup a registered tool by name. Raises ``KeyError`` on miss."""
    for t in AGENT_TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"unknown agent tool: {name!r}")
