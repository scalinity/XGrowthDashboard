"""FastAPI application factory for the sidecar (§31.3).

``create_app`` builds an app with:

- a per-request SQLite connection (via the project's ``app.db.connect``),
- per-launch bearer-token auth on every non-health route,
- the §28 startup invariants run once at boot (same guarantees as ``streamlit run``).

Endpoints are added incrementally through Phase 11.0. This module owns the
HTTP shape only; all reads/writes delegate to existing backend code.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date as _date_t
from datetime import timedelta as _timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent import _internal_tools, confirmation, invariants
from app.agent.client import AgentClient, start_conversation
from app.agent.content_types import get_content_type_gaps, get_recommendation_window_days
from app.agent.velocity import (
    get_noise_floor,
    get_velocity_projection,
)
from app.components.charts.follower_trend import FollowerPoint, follower_trend_chart
from app.components.charts.funnel import (
    APP_STORE_GAP_LABEL,
    WHAT_WE_KNOW_TABLE_ROWS,
    build_funnel_stages,
    funnel_chart,
)
from app.components.charts.lane_grid import confidence_color_for_ui_label, count_rankable_lanes, lane_rows_from_sql
from app.components.badges.confidence_label import ui_label_for_db_label
from app.agent.reply_targets import engagement_footnote as _engagement_footnote
from app.agent.tools import (
    _load_engagement_surface_settings,
    _score_reply_candidates,
    _find_reply_targets,
    _save_draft_reply,
    _outline_blog_to_dict,
    _draft_blog_to_dict,
    _suggest_blog_edits_to_dict,
)
from app.agent import brain_dump as _brain_dump
from app.agent import blogs as _blogs
from app.agent import calendar as _calendar
from app.agent import campaigns as _campaigns
from app.agent import inspiration as _inspiration
from app.db import apply_migrations, connect
from app.forms import FormError, get_setting, set_setting
from app.forms.correction import submit_correction
from app.forms.post_log import submit_post
from app.forms.snapshot import submit_snapshot
from app.forms.classify import submit_classification
from app.forms.daily_reps import submit_daily_activity
from app.forms.queues import needs_post_id, needs_tagging
from app.forms.stir_event import submit_stir_event
from app.forms.stir_tester import submit_tester
from app.forms.weekly_review import submit_weekly_review
from app.paths import resolve_db_path
from app.secret_store import resolve_secret, store_secret
from app.service.security import BearerTokenAuth

def _weekly_review_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Weekly Review view (§14.6) needs.

    Mirrors the Streamlit page: auto-filled summary metrics for this week,
    existing review row, counterfactual_required setting, and past review
    history — all computed server-side (§31.10).
    """


    today = _date_t.today()
    # Monday-anchored week.
    week_start = today - _timedelta(days=today.weekday())
    week_end = week_start + _timedelta(days=6)

    # 1. Summary metrics (same SQL as _summary_for_week in 6_Weekly_Review.py).
    ws_iso = week_start.isoformat()
    we_iso = week_end.isoformat()

    foll_row = conn.execute(
        """
        SELECT
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date >= ? AND snapshot_date <= ?
              ORDER BY snapshot_date ASC LIMIT 1) AS followers_start,
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date <= ? AND snapshot_date >= ?
              ORDER BY snapshot_date DESC LIMIT 1) AS followers_end
        """,
        (ws_iso, we_iso, we_iso, ws_iso),
    ).fetchone()
    followers_start = foll_row["followers_start"]
    followers_end = foll_row["followers_end"]
    follower_delta = (
        (followers_end or 0) - (followers_start or 0)
        if followers_start is not None and followers_end is not None
        else None
    )

    reps = conn.execute(
        """
        SELECT
            COALESCE(SUM(posts_shipped), 0)            AS posts_shipped,
            COALESCE(SUM(replies_shipped), 0)          AS replies_shipped,
            COALESCE(SUM(reply_sessions_completed), 0) AS reply_sessions_completed,
            COALESCE(SUM(minimum_reps_completed), 0)   AS daily_reps_days_completed
        FROM v_daily_reps
        WHERE activity_date BETWEEN ? AND ?
        """,
        (ws_iso, we_iso),
    ).fetchone()

    funnel = conn.execute(
        """
        SELECT
            COALESCE(SUM(downloads), 0)             AS downloads,
            COALESCE(SUM(qualified_icp_testers), 0) AS qualified_icp_testers
        FROM v_funnel_daily
        WHERE event_date BETWEEN ? AND ?
        """,
        (ws_iso, we_iso),
    ).fetchone()

    # Strongest pillar candidate.
    lanes = conn.execute(
        """
        SELECT pillar, median_impressions, confidence_label
        FROM v_lane_performance
        ORDER BY median_impressions DESC NULLS LAST
        """
    ).fetchall()
    eligible = [
        r for r in lanes
        if ui_label_for_db_label(r["confidence_label"]) in {"tentative", "confident"}
    ]
    strongest_pillar_candidate = (
        f"{eligible[0]['pillar']} ({eligible[0]['confidence_label']})"
        if eligible
        else None
    )

    summary = {
        "follower_delta": follower_delta,
        "posts_shipped": int(reps["posts_shipped"]),
        "replies_shipped": int(reps["replies_shipped"]),
        "reply_sessions_completed": int(reps["reply_sessions_completed"]),
        "daily_reps_days_completed": int(reps["daily_reps_days_completed"]),
        "downloads": int(funnel["downloads"]),
        "qualified_icp_testers": int(funnel["qualified_icp_testers"]),
        "strongest_pillar_candidate": strongest_pillar_candidate,
    }

    # 2. Existing review for this week.
    existing_row = conn.execute(
        "SELECT * FROM weekly_reviews WHERE week_start_date = ?",
        (ws_iso,),
    ).fetchone()
    existing_review = dict(existing_row) if existing_row else None

    # 3. counterfactual_required setting.
    counterfactual_required = bool(get_setting(conn, "counterfactual_required", True))

    # 4. Past reviews history.
    history_rows = conn.execute(
        """
        SELECT
            id, week_start_date, week_end_date, follower_delta,
            posts_shipped, replies_shipped, downloads,
            counterfactual_note, lesson, what_moved, what_got_stuck,
            next_week_experiment, qualified_icp_testers,
            reply_sessions_completed, daily_reps_days_completed
        FROM weekly_reviews
        ORDER BY week_start_date DESC
        """
    ).fetchall()
    past_reviews = [dict(r) for r in history_rows]

    return {
        "slice": "weekly_review",
        "week_start": ws_iso,
        "week_end": we_iso,
        "summary": summary,
        "existing_review": existing_review,
        "counterfactual_required": counterfactual_required,
        "past_reviews": past_reviews,
    }


def _reply_queue_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Reply Target Queue view (§29.7) needs."""
    eng_settings = _load_engagement_surface_settings(conn)

    # Counters.
    ctr_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END) AS candidates,
            SUM(CASE WHEN status = 'drafted' THEN 1 ELSE 0 END) AS drafted,
            SUM(CASE WHEN status = 'posted'
                      AND DATE(last_checked_at_utc) = DATE('now') THEN 1 ELSE 0 END) AS posted_today,
            SUM(CASE WHEN status = 'skipped'
                      AND DATE(last_checked_at_utc) = DATE('now') THEN 1 ELSE 0 END) AS skipped_today
        FROM reply_targets
        """
    ).fetchone()
    counters = {
        "candidates": int(ctr_row["candidates"] or 0),
        "drafted": int(ctr_row["drafted"] or 0),
        "posted_today": int(ctr_row["posted_today"] or 0),
        "skipped_today": int(ctr_row["skipped_today"] or 0),
    }

    # Candidate rows (default: status='candidate', sorted by action score).
    rows = conn.execute(
        """SELECT id, target_post_url, target_text, target_author_handle,
                  target_author_follower_count, like_count, reply_count,
                  repost_count, relevance_score, engagement_surface_score,
                  saturation_score, reply_opportunity_score,
                  recommended_action_label, recommended_action_score,
                  score_rationale, pillar, reply_intent, status,
                  discovered_at_utc, discovered_via
           FROM reply_targets
           WHERE status = 'candidate'
           ORDER BY COALESCE(recommended_action_score, -1) DESC,
                    last_checked_at_utc DESC
           LIMIT 50"""
    ).fetchall()

    items = []
    for r in rows:
        handle = (r["target_author_handle"] or "unknown").lstrip("@")
        text = (r["target_text"] or "").strip().replace("\n", " ")
        if len(text) > 220:
            text = text[:219] + "…"
        items.append({
            "id": int(r["id"]),
            "handle": handle,
            "text_excerpt": text or None,
            "target_post_url": r["target_post_url"],
            "like_count": int(r["like_count"] or 0),
            "reply_count": int(r["reply_count"] or 0),
            "repost_count": int(r["repost_count"] or 0),
            "relevance_score": r["relevance_score"],
            "engagement_surface_score": r["engagement_surface_score"],
            "saturation_score": r["saturation_score"],
            "reply_opportunity_score": r["reply_opportunity_score"],
            "recommended_action_label": r["recommended_action_label"],
            "score_rationale": r["score_rationale"],
            "pillar": r["pillar"],
            "reply_intent": r["reply_intent"],
            "discovered_via": r["discovered_via"],
            "engagement_footnote": _engagement_footnote(
                r["target_author_follower_count"], eng_settings
            ),
        })

    return {
        "slice": "reply_queue",
        "counters": counters,
        "items": items,
    }


def _content_calendar_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Content Calendar view (§14.11) needs."""


    today = _date_t.today()
    # Two weeks back + two weeks forward.
    window_start = today - _timedelta(days=14)
    window_end = today + _timedelta(days=14)

    cells = _calendar.get_calendar_window(
        conn,
        start_date=window_start,
        end_date=window_end,
    )

    # Group cells by date.
    by_date: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        entry = {
            "provenance": cell.provenance,
            "source_id": cell.source_id,
            "slot": cell.slot,
            "pillar": cell.pillar,
            "content_type": cell.content_type,
            "title": cell.title,
            "campaign_id": cell.campaign_id,
        }
        by_date.setdefault(cell.date, []).append(entry)

    # Active campaigns in this window.
    active_campaigns = _calendar.get_active_campaigns_in_window(
        conn, start_date=window_start, end_date=window_end
    )

    return {
        "slice": "content_calendar",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "by_date": by_date,
        "active_campaigns": active_campaigns,
    }


def _campaigns_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Campaigns view (§14.12) needs."""
    all_campaigns = _campaigns.list_campaigns(conn)

    by_status: dict[str, list[dict[str, Any]]] = {
        s: [] for s in ("active", "planning", "completed", "abandoned")
    }
    for camp in all_campaigns:
        items = _campaigns.list_items(conn, campaign_id=camp.id)
        progress_row = conn.execute(
            """SELECT items_total, items_shipped, percent_shipped, days_until_end
               FROM v_campaign_progress WHERE campaign_id = ?""",
            (camp.id,),
        ).fetchone()
        pct = (
            progress_row["percent_shipped"]
            if progress_row and progress_row["percent_shipped"] is not None
            else None
        )
        items_shipped = int(progress_row["items_shipped"] or 0) if progress_row else 0
        items_total = int(progress_row["items_total"] or 0) if progress_row else 0
        days_until_end = (
            int(progress_row["days_until_end"])
            if progress_row and progress_row["days_until_end"] is not None
            else None
        )

        success_lines = []
        for stream in ("distribution", "validation"):
            for entry in camp.success_criteria.get(stream, []):
                actual = entry.get("actual")
                target = entry.get("target")
                metric = entry.get("metric")
                success_lines.append({
                    "stream": stream,
                    "metric": metric,
                    "target": target,
                    "actual": actual,
                })

        item_list = []
        for it in items:
            item_list.append({
                "id": it.id,
                "item_type": it.item_type,
                "status": it.status,
                "planned_for_date": it.planned_for_date,
                "planned_text": (it.planned_text or "")[:200] or None,
            })

        camp_dict = {
            "id": camp.id,
            "name": camp.name,
            "theme": camp.theme,
            "hypothesis": camp.hypothesis,
            "start_date": camp.start_date,
            "end_date": camp.end_date,
            "status": camp.status,
            "pillar": camp.pillar,
            "content_type": camp.content_type,
            "items_shipped": items_shipped,
            "items_total": items_total,
            "percent_shipped": pct,
            "days_until_end": days_until_end,
            "success_criteria": success_lines,
            "items": item_list,
            "lesson": camp.lesson,
            "counterfactual_note": camp.counterfactual_note,
            "abandon_reason": camp.abandon_reason,
        }
        by_status[camp.status].append(camp_dict)

    return {
        "slice": "campaigns",
        "by_status": by_status,
        "summary": {s: len(v) for s, v in by_status.items()},
    }


def _inspiration_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Inspiration Library view (§14.13) needs."""
    items = _inspiration.list_inspirations(conn, status="active")

    # For each item, also grab its transforms.
    for item in items:
        transforms = _inspiration.list_transforms(
            conn, saved_inspiration_id=item["id"]
        )
        item["transforms"] = transforms

    return {
        "slice": "inspiration",
        "items": items,
    }


def _blogs_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Blogs view (§14.14) needs."""
    rows = _blogs.list_blogs(conn)
    return {
        "slice": "blogs",
        "blogs": rows,
    }


def _blog_detail_slice(conn: sqlite3.Connection, blog_id: int) -> dict[str, Any]:
    """Gather every data slice the Blog Editor view (§14.15) needs."""
    blog = _blogs.get_blog(conn, blog_id)
    versions = _blogs.list_versions(conn, blog_id=blog_id)
    return {
        "slice": "blog_detail",
        "blog": {
            "id": blog.id,
            "slug": blog.slug,
            "title": blog.title,
            "status": blog.status,
            "pillar": blog.pillar,
            "audience": blog.audience,
            "current_body_markdown": blog.current_body_markdown,
            "outline_markdown": blog.outline_markdown,
            "actual_length_words": blog.actual_length_words,
            "target_length_words": blog.target_length_words,
            "agent_assisted": blog.agent_assisted,
            "created_at_utc": blog.created_at_utc,
            "updated_at_utc": blog.updated_at_utc,
        },
        "versions": [
            {
                "id": v.id,
                "version_number": v.version_number,
                "title_at_version": v.title_at_version,
                "status_at_version": v.status_at_version,
                "created_by": v.created_by,
                "confidence_label_at_version": v.confidence_label_at_version,
                "created_at_utc": v.created_at_utc,
            }
            for v in versions
        ],
    }


def _brain_dump_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather data for the Brain Dump view (§14.9)."""
    # Recent brain-dump conversations.
    rows = conn.execute(
        """SELECT id, title, context_seed, created_at
           FROM agent_conversations
           WHERE context_seed = 'brain_dump' OR title LIKE '%brain%dump%'
           ORDER BY id DESC LIMIT 20"""
    ).fetchall()
    conversations = [dict(r) for r in rows]

    # Recent agent drafts marked as brain_dump.
    drafts = conn.execute(
        """SELECT ad.id, ad.text, ad.draft_kind, ad.pillar,
                  ad.similarity_warning_json, ps.composite_label, ad.status
           FROM agent_drafts ad
           LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
           WHERE ad.draft_kind = 'brain_dump'
           ORDER BY ad.id DESC LIMIT 10"""
    ).fetchall()
    draft_list = []
    for d in drafts:
        preview = (d["text"] or "").strip()
        draft_list.append({
            "id": d["id"],
            "text": preview,
            "pillar": d["pillar"],
            "composite_label": d["composite_label"],
            "status": d["status"],
            "similarity_warning_json": d["similarity_warning_json"],
        })

    return {
        "slice": "brain_dump",
        "conversations": conversations,
        "drafts": draft_list,
    }


def _account_researcher_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather data for the Account Researcher view (§28.24)."""
    has_ata = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_target_accounts'"
    ).fetchone()
    accounts = []
    if has_ata:
        rows = conn.execute(
            """SELECT x_handle, display_name, lane, priority, notes,
                      is_active, last_engaged_at, created_at
               FROM agent_target_accounts
               ORDER BY created_at DESC LIMIT 30"""
        ).fetchall()
        accounts = [dict(r) for r in rows]

    return {
        "slice": "account_researcher",
        "accounts": accounts,
    }


ConnFactory = Callable[[], sqlite3.Connection]
AgentClientFactory = Callable[[], AgentClient]

SERVICE_NAME = "x-growth-dashboard-service"
SERVICE_VERSION = "0.1.0"

# The native shell serves the React frontend from the Tauri custom-scheme
# origin (``tauri://localhost`` on macOS; ``http(s)://tauri.localhost`` on
# Windows), so the webview's fetch() to the loopback sidecar is *cross-origin*
# and WebKit enforces CORS. Production must echo CORS for this origin or the app
# cannot read its own service. This does NOT widen the network surface (§31.10):
# the sidecar still binds 127.0.0.1 only and every protected route still
# requires the per-launch bearer token — CORS merely lifts the webview's
# same-origin *read* check, which a remote page can't satisfy anyway.
TAURI_WEBVIEW_ORIGIN_REGEX = r"^(tauri://localhost|https?://tauri\.localhost)$"


class StartConversationBody(BaseModel):
    """POST /agent/conversations request body (§14.8)."""

    title: str | None = None
    context_seed: str | None = None


class SendMessageBody(BaseModel):
    """POST /agent/conversations/{id}/messages request body."""

    text: str


class PublishBody(BaseModel):
    """POST /publish request body (§28.10). ``confirm`` must equal 'confirm'."""

    post_id: int
    text: str
    confirm: str
    message_id: int | None = None


class SettingValue(BaseModel):
    """PUT /settings/{key} request body. ``value`` is any JSON-serializable value."""

    value: Any


class SecretBody(BaseModel):
    """PUT /settings/secrets/{name} request body — a write-only secret value."""

    value: str


def _form_error(exc: FormError) -> HTTPException:
    """Map a forms-layer FormError to a 400 with structured field errors."""
    return HTTPException(
        status_code=400,
        detail={"message": str(exc), "field_errors": exc.field_errors},
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame (text/event-stream)."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _default_conn_factory() -> sqlite3.Connection:
    """Open a connection to the resolved DB (§31.5).

    Resolves the path fresh on each call so a runtime migration to Application
    Support (performed by the sidecar before it serves) is picked up — the
    import-time ``DEFAULT_DB_PATH`` constant would be stale across that move.

    RV5-W3: migrations are now applied once at startup (in create_app), not
    per-request — avoids ~2-5ms overhead per call and concurrent-migration races.
    """
    return connect(resolve_db_path())


def _today_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Today view (§14.1) needs.

    All business logic stays here (§31.10: "no business logic in the frontend"):
    milestone progress pct, velocity gating, high-engagement mix target, text
    preview truncation. The frontend only renders.
    """
    today_iso = _date_t.today().isoformat()

    # 1. Today's snapshot row from v_account_daily.
    snap = conn.execute(
        "SELECT * FROM v_account_daily WHERE snapshot_date = ?", (today_iso,)
    ).fetchone()
    snapshot = dict(snap) if snap else None

    # 2. Baseline + milestone.
    baseline = int(get_setting(conn, "baseline_followers", 61) or 61)
    target = int(get_setting(conn, "current_milestone", 100) or 100)
    ms_row = conn.execute(
        "SELECT * FROM milestones WHERE category = 'distribution' AND target_value = ? LIMIT 1",
        (target,),
    ).fetchone()
    milestone = dict(ms_row) if ms_row else None
    milestone_progress_pct: float | None = None
    if snapshot and milestone:
        start = int(milestone.get("start_value") or baseline)
        end = int(milestone.get("target_value") or target)
        foll = int(snapshot["followers_count"])
        milestone_progress_pct = max(0.0, min(1.0, (foll - start) / max(1, end - start)))

    # 3. Velocity gating (§13 rule 6). Uses get_noise_floor so the threshold
    # is consistent with Progress view (RV5-C3 fix — was hardcoded at 10).
    delta_7d = snapshot["delta_7d"] if snapshot else None
    noise_floor = get_noise_floor(conn)
    velocity_measurable = delta_7d is not None and abs(delta_7d) >= noise_floor
    velocity_7d = snapshot["velocity_7d_per_day"] if snapshot else None

    # 4. Content-type recommendation (§28.17).
    ct_window = get_recommendation_window_days(conn)
    ct_gap = get_content_type_gaps(conn, window_days=ct_window)

    # 5. Daily reps + §29.9 mix.
    reps_snap = conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = ?", (today_iso,)
    ).fetchone()
    reps_row = dict(reps_snap) if reps_snap else None
    post_target = int(get_setting(conn, "daily_post_target", 1) or 1)
    reply_target_val = int(get_setting(conn, "daily_reply_target", 12) or 12)
    session_target = int(get_setting(conn, "daily_reply_session_target", 1) or 1)
    high_eng_mix_pct = float(get_setting(conn, "reply_high_engagement_mix_pct", 0.5) or 0.5)
    cand_review_target = int(get_setting(conn, "reply_candidate_review_daily_target", 15) or 15)
    mix: dict[str, Any] = {}
    if reps_row:
        replies_shipped = int(reps_row.get("replies_shipped") or 0)
        high_eng = int(reps_row.get("high_engagement_replies_shipped") or 0)
        high_eng_target = max(1, int(round(high_eng_mix_pct * max(1, replies_shipped))))
        mix = {
            "high_eng": high_eng,
            "icp_intent": int(reps_row.get("icp_intent_replies_shipped") or 0),
            "candidates_rev": int(reps_row.get("candidates_reviewed_today") or 0),
            "high_eng_target": high_eng_target,
            "high_eng_met": replies_shipped > 0 and high_eng >= high_eng_target,
            "cand_target": cand_review_target,
            "cand_met": int(reps_row.get("candidates_reviewed_today") or 0) >= cand_review_target,
        }

    # 6. Pending agent drafts (today, proposed).
    pending_rows = conn.execute(
        """
        SELECT ad.id, ad.text, ad.draft_kind, ad.created_at,
               ad.similarity_warning_json,
               ps.composite_label
        FROM agent_drafts ad
        LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
        WHERE date(ad.created_at) = date('now')
          AND ad.status = 'proposed'
        ORDER BY ad.id DESC LIMIT 5
        """,
    ).fetchall()
    pending_drafts = []
    for d in pending_rows:
        preview = (d["text"] or "").strip().replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:157] + "…"
        pending_drafts.append({
            "id": d["id"],
            "text_preview": preview,
            "draft_kind": d["draft_kind"],
            "composite_label": d["composite_label"],
            "similarity_warning_json": d["similarity_warning_json"],
        })

    # 7. Recent posts (today, last 5).
    recent_rows = conn.execute(
        """
        SELECT p.id, p.created_at_utc, p.text, p.type,
               p.manual_confirmation_status,
               pc.pillar, pc.audience, pc.cta
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        WHERE p.created_date = ?
        ORDER BY p.created_at_utc DESC LIMIT 5
        """,
        (today_iso,),
    ).fetchall()
    recent_posts = []
    for r in recent_rows:
        preview = (r["text"] or "").strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "…"
        recent_posts.append({
            "id": r["id"],
            "type": r["type"],
            "text_preview": preview,
            "pillar": r["pillar"],
            "audience": r["audience"],
            "cta": r["cta"],
            "confirm_status": r["manual_confirmation_status"],
        })

    # 8. Snapshot form defaults (so frontend can pre-fill without another call).
    snap_defaults = {
        "username": get_setting(conn, "x_handle", "") or "",
        "profile_url": get_setting(conn, "profile_url", "") or "",
        "baseline_followers": baseline,
        "x_user_id": get_setting(conn, "x_user_id"),
    }

    # S2: removed unused account_last_7 query (no frontend consumer).

    return {
        "slice": "today",
        "today_iso": today_iso,
        "snapshot": snapshot,
        "baseline_followers": baseline,
        "current_milestone_target": target,
        "milestone": milestone,
        "milestone_progress_pct": milestone_progress_pct,
        "velocity_measurable": velocity_measurable,
        "velocity_7d_per_day": velocity_7d,
        "content_type_reco": {
            "under_represented": ct_gap.get("under_represented"),
            "rationale": ct_gap.get("rationale", ""),
        },
        "daily_reps": {
            "row": reps_row,
            "targets": {
                "post_target": post_target,
                "reply_target": reply_target_val,
                "session_target": session_target,
                "high_engagement_mix_pct": high_eng_mix_pct,
                "candidate_review_daily_target": cand_review_target,
            },
            "mix": mix,
        },
        "pending_drafts": pending_drafts,
        "recent_posts": recent_posts,
        "snapshot_defaults": snap_defaults,
    }


def _progress_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Progress view (§14.3) needs."""
    # 1. Milestones.
    dist = conn.execute(
        "SELECT * FROM milestones WHERE category = 'distribution' ORDER BY ladder_position ASC"
    ).fetchall()
    val = conn.execute(
        "SELECT * FROM milestones WHERE category = 'validation' ORDER BY ladder_position ASC"
    ).fetchall()

    # 2. Current followers.
    latest = conn.execute(
        "SELECT followers_count FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    current_followers = int(latest["followers_count"]) if latest else None

    # Compute milestone progress for distribution milestones (server-side §31.10).
    dist_out = []
    for m in dist:
        md = dict(m)
        target = m["target_value"]
        start = m["start_value"] or 0
        if m["status"] == "achieved":
            md["progress"] = 1.0
            md["progress_label"] = "achieved"
        elif target and current_followers is not None and target > start:
            p = max(0.0, min(1.0, (current_followers - start) / max(1, target - start)))
            md["progress"] = p
            md["progress_label"] = f"{p * 100:.1f}%"
        else:
            md["progress"] = 0.0
            md["progress_label"] = "not yet"
        dist_out.append(md)

    val_out = []
    for m in val:
        md = dict(m)
        md["progress"] = 1.0 if m["status"] == "achieved" else 0.0
        md["progress_label"] = "achieved" if m["status"] == "achieved" else "not yet"
        val_out.append(md)

    # 3. Velocity projection.
    proj = get_velocity_projection(conn)
    noise_floor = get_noise_floor(conn)

    # 4. Weekly post/reply counts (last 8 ISO weeks).
    today = _date_t.today()
    monday = today - _timedelta(days=today.weekday())
    earliest = (monday - _timedelta(weeks=7)).isoformat()
    latest_date = (monday + _timedelta(days=6)).isoformat()
    week_rows = conn.execute(
        """
        SELECT
            DATE(created_date,
                 '-' || ((CAST(strftime('%w', created_date) AS INTEGER) + 6) % 7)
                 || ' days') AS week_start,
            SUM(CASE WHEN type IN ('standalone','thread_root','thread_child','quote')
                      THEN 1 ELSE 0 END) AS posts,
            SUM(CASE WHEN type = 'reply' THEN 1 ELSE 0 END) AS replies
        FROM posts
        WHERE created_date BETWEEN ? AND ?
        GROUP BY week_start ORDER BY week_start ASC
        """,
        (earliest, latest_date),
    ).fetchall()
    by_week = {r["week_start"]: (int(r["posts"] or 0), int(r["replies"] or 0)) for r in week_rows}
    weekly: list[dict[str, Any]] = []
    for w in range(7, -1, -1):
        ws = (monday - _timedelta(weeks=w)).isoformat()
        posts, replies = by_week.get(ws, (0, 0))
        weekly.append({"week_start": ws, "posts": posts, "replies": replies})

    # 5. Settings.
    post_target = int(get_setting(conn, "daily_post_target", 1) or 1)
    reply_target = int(get_setting(conn, "daily_reply_target", 12) or 12)
    session_target = int(get_setting(conn, "daily_reply_session_target", 1) or 1)
    operational_ceiling = int(get_setting(conn, "operational_ceiling", 5000) or 5000)
    long_arc = int(get_setting(conn, "long_arc_reminder", 500000) or 500000)

    return {
        "slice": "progress",
        "current_followers": current_followers,
        "distribution_milestones": dist_out,
        "validation_milestones": val_out,
        "velocity_projection": proj.to_dict() if proj else None,
        "noise_floor": noise_floor,
        "weekly_counts": weekly,
        "targets": {
            "post_target": post_target,
            "reply_target": reply_target,
            "session_target": session_target,
        },
        "operational_ceiling": operational_ceiling,
        "long_arc_reminder": long_arc,
    }


def _content_performance_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Content Performance view (§14.4) needs."""

    # 1. Lane performance rows.
    raw_rows = conn.execute(
        "SELECT * FROM v_lane_performance ORDER BY post_count DESC"
    ).fetchall()
    lane_data = lane_rows_from_sql(raw_rows)
    rankable_count = count_rankable_lanes(lane_data)

    lanes = []
    for lr in lane_data:
        ui_label = ui_label_for_db_label(lr.db_confidence_label)
        chip_bg = confidence_color_for_ui_label(ui_label)
        # Format median+IQR server-side (§31.10).
        if ui_label == "insufficient" or lr.median_impressions is None:
            median_display = "—"
        elif lr.iqr_low is None or lr.iqr_high is None:
            median_display = f"{int(round(lr.median_impressions)):,}"
        else:
            median_display = (
                f"{int(round(lr.median_impressions)):,} "
                f"[{int(round(lr.iqr_low)):,}–{int(round(lr.iqr_high)):,}]"
            )
        lanes.append({
            "pillar": lr.pillar, "audience": lr.audience, "cta": lr.cta,
            "post_count": lr.post_count, "days_covered": lr.days_covered,
            "median_display": median_display,
            "median_impressions": lr.median_impressions,
            "iqr_low": lr.iqr_low, "iqr_high": lr.iqr_high,
            "total_bookmarks": lr.total_bookmarks,
            "total_replies": lr.total_replies,
            "stir_signal_count": lr.stir_signal_count,
            "ui_label": ui_label, "chip_bg": chip_bg,
        })

    # 2. Best lane (§14.4 anti-overfitting gate).
    best = None
    if rankable_count >= 3:
        rankable = [
            lr for lr in lane_data
            if ui_label_for_db_label(lr.db_confidence_label) in {"tentative", "confident"}
            and lr.median_impressions is not None
        ]
        if rankable:
            b = max(rankable, key=lambda r: r.median_impressions or 0)
            ui_label = ui_label_for_db_label(b.db_confidence_label)
            best = {
                "lane": f"{b.pillar} · {b.audience} · {b.cta}",
                "median_impressions": int(b.median_impressions or 0),
                "iqr_low": int(b.iqr_low or 0), "iqr_high": int(b.iqr_high or 0),
                "ui_label": ui_label,
                "chip_bg": confidence_color_for_ui_label(ui_label),
            }

    # 3. V/G/P/P content type table.
    ct_rows = conn.execute(
        """SELECT content_type, post_count, days_covered,
                  median_impressions, iqr_impressions_low, iqr_impressions_high,
                  median_engagement_rate, confidence_label
           FROM v_content_type_performance
           ORDER BY CASE content_type
              WHEN 'value' THEN 0 WHEN 'growth' THEN 1
              WHEN 'personality' THEN 2 WHEN 'proof' THEN 3 ELSE 9 END"""
    ).fetchall()
    content_types = []
    for r in ct_rows:
        ul = ui_label_for_db_label(r["confidence_label"] or "insufficient sample")
        content_types.append({
            "content_type": r["content_type"],
            "post_count": int(r["post_count"] or 0),
            "days_covered": int(r["days_covered"] or 0),
            "median_impressions": r["median_impressions"],
            "median_engagement_rate": r["median_engagement_rate"],
            "ui_label": ul,
            "chip_bg": confidence_color_for_ui_label(ul),
        })

    # 4. Pre-publish scorer calibration.
    cal_rows = conn.execute(
        """SELECT ps.composite_label, COUNT(*) AS n,
                  AVG(plm.impressions) AS avg_impressions,
                  AVG(plm.engagement_rate) AS avg_engagement_rate,
                  AVG(ps.screenshot_test_score) AS avg_screenshot_test_score,
                  COUNT(ps.screenshot_test_score) AS n_with_screenshot_score
           FROM agent_drafts ad
           JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
           JOIN posts p ON p.id = ad.final_post_id
           JOIN v_post_latest_metrics plm ON plm.post_id = p.id
           WHERE p.manual_confirmation_status = 'confirmed' AND plm.impressions IS NOT NULL
           GROUP BY ps.composite_label
           ORDER BY CASE ps.composite_label
             WHEN 'strong' THEN 0 WHEN 'viable' THEN 1 WHEN 'weak' THEN 2 ELSE 3 END
           LIMIT 10"""
    ).fetchall()
    calibration = [dict(r) for r in cal_rows]

    return {
        "slice": "content_performance",
        "lanes": lanes,
        "rankable_count": rankable_count,
        "best_lane": best,
        "content_types": content_types,
        "calibration": calibration,
    }


def _lane_scatter_figure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the lane-scatter Plotly figure (§14.4) and return JSON-safe dict."""
    import plotly.graph_objects as go

    from app.components.theme import LANE_SCATTER_COLORS, PALETTE

    cutoff = (_date_t.today() - _timedelta(days=30)).isoformat()
    rows = conn.execute(
        """SELECT plm.post_id, p.created_date, plm.impressions,
                  plm.pillar, plm.audience, plm.cta, plm.engagement_rate
           FROM v_post_latest_metrics plm JOIN posts p ON p.id = plm.post_id
           WHERE p.created_date >= ? AND plm.pillar IS NOT NULL AND plm.impressions IS NOT NULL
           ORDER BY p.created_date ASC""",
        (cutoff,),
    ).fetchall()

    fig = go.Figure()
    if not rows:
        fig.update_layout(
            paper_bgcolor=PALETTE["ink"], plot_bgcolor=PALETTE["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
            annotations=[{"text": "No classified posts with impressions in the last 30 days.",
                          "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False,
                          "font": {"family": "Fraunces, serif", "size": 14, "color": PALETTE["bone_dim"]}}],
            margin={"t": 20, "b": 60, "l": 60, "r": 20},
        )
    else:
        by_lane: dict[tuple[str, str, str], list[tuple[str, int]]] = {}
        for r in rows:
            key = (r["pillar"], r["audience"], r["cta"])
            by_lane.setdefault(key, []).append((r["created_date"], int(r["impressions"])))
        for i, (lane, posts) in enumerate(by_lane.items()):
            dates_ = [p[0] for p in posts]
            imps = [p[1] for p in posts]
            fig.add_trace(go.Scatter(
                x=dates_, y=imps, mode="markers",
                marker={"size": 10, "color": LANE_SCATTER_COLORS[i % len(LANE_SCATTER_COLORS)],
                         "line": {"color": PALETTE["bone"], "width": 0.5}, "opacity": 0.85},
                name=" · ".join(lane),
            ))
        fig.update_layout(
            paper_bgcolor=PALETTE["ink"], plot_bgcolor=PALETTE["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": PALETTE["bone"]},
            xaxis={"title": "Date", "gridcolor": PALETTE["hairline"],
                   "tickfont": {"family": "JetBrains Mono, monospace", "color": PALETTE["bone_dim"]}},
            yaxis={"title": "Impressions", "gridcolor": PALETTE["hairline"],
                   "tickfont": {"family": "JetBrains Mono, monospace", "color": PALETTE["bone_dim"]}},
            legend={"orientation": "h", "y": -0.25,
                    "font": {"family": "JetBrains Mono, monospace", "size": 10, "color": PALETTE["bone_dim"]},
                    "bgcolor": "rgba(0,0,0,0)"},
            margin={"t": 20, "b": 80, "l": 60, "r": 20}, height=440,
        )
    return json.loads(fig.to_json())


def _follower_trend_figure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the follower-trend Plotly figure and return it as a JSON-safe dict."""
    rows = conn.execute(
        "SELECT snapshot_date, followers_count FROM v_account_daily ORDER BY snapshot_date ASC"
    ).fetchall()
    points = [FollowerPoint(r["snapshot_date"], int(r["followers_count"])) for r in rows]
    fig = follower_trend_chart(points)
    return json.loads(fig.to_json())


def _funnel_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Funnel view (§14.5) needs."""


    cutoff = (_date_t.today() - _timedelta(days=30)).isoformat()
    agg_row = conn.execute(
        """SELECT COALESCE(SUM(x_impressions_estimate),0) AS x_impressions_estimate,
                  COALESCE(SUM(profile_visits),0) AS profile_visits,
                  COALESCE(SUM(link_clicks),0) AS link_clicks,
                  COALESCE(SUM(getstir_visits),0) AS getstir_visits,
                  COALESCE(SUM(downloads),0) AS downloads,
                  COALESCE(SUM(qualified_icp_testers),0) AS qualified_icp_testers,
                  COALESCE(SUM(working_parent_home_cook_testers),0) AS working_parent_home_cook_testers
           FROM v_funnel_daily WHERE event_date >= ?""",
        (cutoff,),
    ).fetchone()
    agg = {k: int(agg_row[k] or 0) for k in agg_row.keys()}

    daily_rows = conn.execute(
        """SELECT event_date, x_impressions_estimate, profile_visits, link_clicks,
                  getstir_visits, downloads, qualified_icp_testers,
                  working_parent_home_cook_testers
           FROM v_funnel_daily WHERE event_date >= ? ORDER BY event_date ASC""",
        (cutoff,),
    ).fetchall()

    return {
        "slice": "funnel",
        "aggregate": agg,
        "daily": [dict(r) for r in daily_rows],
        "app_store_gap_label": APP_STORE_GAP_LABEL,
        "what_we_know": [{"topic": t, "rule": r} for t, r in WHAT_WE_KNOW_TABLE_ROWS],
    }


def _funnel_chart_figure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the funnel Plotly figure and return as JSON-safe dict."""

    cutoff = (_date_t.today() - _timedelta(days=30)).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(x_impressions_estimate),0) AS x_impressions_estimate,
                  COALESCE(SUM(profile_visits),0) AS profile_visits,
                  COALESCE(SUM(link_clicks),0) AS link_clicks,
                  COALESCE(SUM(downloads),0) AS downloads,
                  COALESCE(SUM(qualified_icp_testers),0) AS qualified_icp_testers
           FROM v_funnel_daily WHERE event_date >= ?""",
        (cutoff,),
    ).fetchone()
    stages = build_funnel_stages(
        impressions=int(row["x_impressions_estimate"] or 0),
        profile_visits_self_reported=int(row["profile_visits"] or 0),
        app_store_clicks_self_reported=int(row["link_clicks"] or 0),
        downloads=int(row["downloads"] or 0),
        icp_testers_self_reported=int(row["qualified_icp_testers"] or 0),
    )
    fig = funnel_chart(stages)
    return json.loads(fig.to_json())


def _funnel_daily_chart_figure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the daily-breakdown stacked bar figure."""
    import plotly.graph_objects as go

    from app.components.theme import PALETTE as _P

    cutoff = (_date_t.today() - _timedelta(days=30)).isoformat()
    rows = conn.execute(
        """SELECT event_date, profile_visits, link_clicks, getstir_visits, downloads
           FROM v_funnel_daily WHERE event_date >= ? ORDER BY event_date ASC""",
        (cutoff,),
    ).fetchall()
    fig = go.Figure()
    if not rows:
        fig.update_layout(
            paper_bgcolor=_P["ink"], plot_bgcolor=_P["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": _P["bone"]},
            annotations=[{"text": "No funnel events in the last 30 days.",
                          "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False,
                          "font": {"family": "Fraunces, serif", "color": _P["bone_dim"]}}],
            margin={"t": 20, "b": 60, "l": 60, "r": 20},
        )
    else:
        dates = [r["event_date"] for r in rows]
        for label, key, color in [
            ("Profile visits", "profile_visits", _P["confidence_directional_bg"]),
            ("Link clicks", "link_clicks", _P["confidence_tentative_bg"]),
            ("getstir.app visits", "getstir_visits", _P["phosphor"]),
            ("Downloads", "downloads", _P["confidence_confident_bg"]),
        ]:
            fig.add_trace(go.Bar(
                x=dates, y=[int(r[key] or 0) for r in rows],
                name=label, marker={"color": color},
            ))
        fig.update_layout(
            barmode="stack", paper_bgcolor=_P["ink"], plot_bgcolor=_P["ink"],
            font={"family": "IBM Plex Sans, sans-serif", "color": _P["bone"]},
            xaxis={"title": "Date", "gridcolor": _P["hairline"],
                   "tickfont": {"family": "JetBrains Mono, monospace", "color": _P["bone_dim"]}},
            yaxis={"title": "Events", "gridcolor": _P["hairline"],
                   "tickfont": {"family": "JetBrains Mono, monospace", "color": _P["bone_dim"]}},
            legend={"orientation": "h", "y": -0.25,
                    "font": {"family": "JetBrains Mono, monospace", "size": 10, "color": _P["bone_dim"]},
                    "bgcolor": "rgba(0,0,0,0)"},
            margin={"t": 20, "b": 80, "l": 60, "r": 20}, height=360,
        )
    return json.loads(fig.to_json())


def _next_rep_slice(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Next Rep view (§14.2) needs."""


    # 1. Lane coverage this week (7-day window).
    cutoff = (_date_t.today() - _timedelta(days=7)).isoformat()
    cov_rows = conn.execute(
        """SELECT plm.pillar, plm.audience, plm.cta, COUNT(*) AS n
           FROM v_post_latest_metrics plm JOIN posts p ON p.id = plm.post_id
           WHERE p.created_date >= ? AND plm.pillar IS NOT NULL
           GROUP BY plm.pillar, plm.audience, plm.cta""",
        (cutoff,),
    ).fetchall()
    known = conn.execute(
        "SELECT DISTINCT pillar, audience, cta FROM v_lane_performance"
    ).fetchall()
    counts: dict[str, int] = {}
    for r in known:
        key = f"{r['pillar']}·{r['audience']}·{r['cta']}"
        counts[key] = 0
    for r in cov_rows:
        key = f"{r['pillar']}·{r['audience']}·{r['cta']}"
        counts[key] = int(r["n"])
    coverage = sorted(
        [{"lane": k, "count": v} for k, v in counts.items()],
        key=lambda x: x["count"],
    )
    biggest_gap_lane = coverage[0]["lane"] if coverage else None
    biggest_gap_pillar = biggest_gap_lane.split("·")[0] if biggest_gap_lane else None

    # 2. Open hypotheses.
    hyp_rows = conn.execute(
        """SELECT id, name, hypothesis, content_lane, target_audience,
                  success_metric, minimum_sample_size, start_date
           FROM experiments WHERE status = 'running' ORDER BY start_date ASC"""
    ).fetchall()
    hypotheses = []
    for h in hyp_rows:
        # Count posts in the hypothesis lane since start.
        if h["content_lane"] and h["target_audience"]:
            n = conn.execute(
                "SELECT COUNT(*) FROM v_post_latest_metrics plm "
                "JOIN posts p ON p.id = plm.post_id "
                "WHERE plm.pillar = ? AND plm.audience = ? AND p.created_date >= ?",
                (h["content_lane"], h["target_audience"], h["start_date"]),
            ).fetchone()[0]
        elif h["content_lane"]:
            n = conn.execute(
                "SELECT COUNT(*) FROM v_post_latest_metrics plm "
                "JOIN posts p ON p.id = plm.post_id "
                "WHERE plm.pillar = ? AND p.created_date >= ?",
                (h["content_lane"], h["start_date"]),
            ).fetchone()[0]
        else:
            n = 0
        hypotheses.append({**dict(h), "posts_in_lane": int(n or 0)})

    # 3. Reply targets (top 5 candidates, biased to biggest-gap pillar).
    rt_rows = conn.execute(
        """SELECT * FROM reply_targets
           WHERE status = 'candidate'
             AND (? IS NULL OR pillar IS NULL OR pillar = ?)
           ORDER BY COALESCE(recommended_action_score, -1) DESC,
                    last_checked_at_utc DESC LIMIT 5""",
        (biggest_gap_pillar, biggest_gap_pillar),
    ).fetchall()
    eng_settings = _load_engagement_surface_settings(conn)
    reply_targets = []
    for r in rt_rows:
        handle = (r["target_author_handle"] or "unknown").lstrip("@")
        text = (r["target_text"] or "").strip().replace("\n", " ")
        if len(text) > 80:
            text = text[:79] + "…"
        reply_targets.append({
            "id": r["id"], "handle": handle, "text_excerpt": text or None,
            "relevance_score": r["relevance_score"],
            "engagement_surface_score": r["engagement_surface_score"],
            "saturation_score": r["saturation_score"],
            "reply_opportunity_score": r["reply_opportunity_score"],
            "recommended_action_label": r["recommended_action_label"],
            "engagement_footnote": _engagement_footnote(
                r["target_author_follower_count"], eng_settings
            ),
        })

    # 4. Account leads.
    has_ata = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_target_accounts'"
    ).fetchone()
    account_leads = []
    if has_ata:
        al_rows = conn.execute(
            """SELECT x_handle, display_name, lane, priority, notes
               FROM agent_target_accounts WHERE is_active = 1
               ORDER BY priority ASC, last_engaged_at ASC NULLS FIRST LIMIT 8"""
        ).fetchall()
        account_leads = [dict(r) for r in al_rows]

    # 5. Pending agent drafts (proposed).
    pending_rows = conn.execute(
        """SELECT ad.id, ad.text, ad.draft_kind, ad.pillar,
                  ad.similarity_warning_json, ps.composite_label
           FROM agent_drafts ad
           LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
           WHERE ad.status = 'proposed' ORDER BY ad.id DESC LIMIT 5"""
    ).fetchall()
    pending_drafts = []
    for d in pending_rows:
        preview = (d["text"] or "").strip().replace("\n", " ")
        if len(preview) > 140:
            preview = preview[:137] + "…"
        pending_drafts.append({
            "id": d["id"], "text_preview": preview,
            "draft_kind": d["draft_kind"], "pillar": d["pillar"],
            "composite_label": d["composite_label"],
            "similarity_warning_json": d["similarity_warning_json"],
        })

    return {
        "slice": "next_rep",
        "coverage": coverage,
        "biggest_gap_lane": biggest_gap_lane,
        "biggest_gap_pillar": biggest_gap_pillar,
        "hypotheses": hypotheses,
        "reply_targets": reply_targets,
        "account_leads": account_leads,
        "pending_drafts": pending_drafts,
    }


def _coach_turn(
    conn: sqlite3.Connection, conversation_id: int, user_text: str
) -> dict[str, Any]:
    """Coach advice-only turn: text-in text-out, NO tools.

    The Streamlit Coach (12_Coach.py) is a separate implementation that calls
    Anthropic with no tools (enforced by ``assert_coach_excludes_write_tools``).
    This mirrors that behaviour in the sidecar. Conversations with
    ``context_seed='coach'`` route here instead of to ``AgentClient.send_message_sync``.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "user_text": user_text, "assistant_text": None, "tool_calls": [],
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
            "model": None,
            "error": "Anthropic API key not configured. Set it in Settings → API keys.",
        }

    from app.agent import prompt_builder  # lazy to avoid circular imports

    import anthropic

    # Persist user message first (matches AgentClient.send_message_sync order).
    conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) VALUES (?, 'user', ?)",
        (conversation_id, user_text),
    )
    conn.commit()

    # Build the shared system prompt + load conversation history.
    system_prompt = prompt_builder.build_system_prompt(conn)
    rows = conn.execute(
        "SELECT role, content FROM agent_messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    messages = [{"role": r["role"], "content": r["content"]} for r in rows]

    model = "claude-sonnet-4-20250514"
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        resp = client.messages.create(
            model=model, max_tokens=2048, system=system_prompt, messages=messages,
        )
        text_parts = [
            getattr(b, "text", "")
            for b in resp.content
            if getattr(b, "type", None) == "text"
        ]
        assistant_text = "".join(text_parts).strip()
        in_tok = int(getattr(resp.usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(resp.usage, "output_tokens", 0) or 0)

        # Persist assistant message.
        conn.execute(
            "INSERT INTO agent_messages "
            "(conversation_id, role, content, model, input_tokens, output_tokens) "
            "VALUES (?, 'assistant', ?, ?, ?, ?)",
            (conversation_id, assistant_text, model, in_tok, out_tok),
        )
        conn.commit()

        return {
            "user_text": user_text, "assistant_text": assistant_text,
            "tool_calls": [], "input_tokens": in_tok, "output_tokens": out_tok,
            "cost_usd": None, "model": model, "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — surface all errors to the UI
        return {
            "user_text": user_text, "assistant_text": None, "tool_calls": [],
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
            "model": None, "error": f"{type(exc).__name__}: {exc}",
        }


def create_app(
    *,
    token: str,
    conn_factory: ConnFactory | None = None,
    agent_client_factory: AgentClientFactory | None = None,
    run_invariants: bool = True,
    dev_cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    Parameters
    ----------
    token
        The per-launch bearer token required on every protected route.
    conn_factory
        Returns a fresh ``sqlite3.Connection`` per request. Defaults to the
        real DB; tests inject a tmp-DB factory.
    agent_client_factory
        Returns an ``AgentClient`` for the agent endpoints. Defaults to a
        real client (reads ANTHROPIC_API_KEY from env); tests inject a stub
        whose ``_call_model`` skips the network.
    run_invariants
        Run the §28 startup invariants at app creation. Default True.
    dev_cors_origins
        DEV-ONLY override. When set, allow these browser origins to call the
        loopback sidecar cross-origin (the §31 Step 0 screenshot-diff loop). The
        packaged app and ``app.service.__main__`` never set this; production
        instead allows the Tauri webview origin via
        ``TAURI_WEBVIEW_ORIGIN_REGEX`` (the native WKWebView serves the frontend
        from ``tauri://localhost``, so its fetch to the loopback sidecar is
        cross-origin and needs CORS). Either way the §31.10 "no new network
        surface" guarantee holds: auth is still the per-launch bearer token and
        the bind is still loopback-only; CORS only relaxes the *browser's*
        same-origin read check, not the bind.
    """
    factory = conn_factory or _default_conn_factory
    agent_factory = agent_client_factory or (lambda: AgentClient())

    # RV5-W3: apply migrations once at startup instead of per-request.
    boot_conn = factory()
    try:
        apply_migrations(boot_conn)
    finally:
        boot_conn.close()

    if run_invariants:
        invariants.run_all()

    app = FastAPI(title="X Growth Dashboard — local service", version=SERVICE_VERSION)
    auth = BearerTokenAuth(token)

    # CORS for the native webview's cross-origin fetch to the loopback sidecar.
    # In dev (browser screenshot-diffing) the caller passes explicit origins; in
    # the packaged app we allow the Tauri webview origin. Either way the loopback
    # bind + per-launch bearer token remain the security boundary (§31.10).
    if dev_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=dev_cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=TAURI_WEBVIEW_ORIGIN_REGEX,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Liveness probe for the Tauri shell's sidecar handshake. Unauthenticated."""
        return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    @app.get("/api/user-metrics", dependencies=[Depends(auth)])
    def get_user_metrics(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Fetch the authenticated user's live metrics from X API (Phase 7).

        Returns followers, following, posts, listed counts for the snapshot
        form. As a side-effect, auto-populates the ``x_handle`` and
        ``profile_url`` settings if they're empty (so subsequent snapshots
        don't fail validation).
        """
        try:
            from app.x_client import api_get_user_metrics

            metrics = api_get_user_metrics()
            # Auto-seed settings when not yet configured.
            username = metrics.get("username")
            if username:
                if not get_setting(conn, "x_handle"):
                    set_setting(conn, "x_handle", username)
                if not get_setting(conn, "profile_url"):
                    set_setting(conn, "profile_url", f"https://x.com/{username}")
            return metrics
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"X API fetch failed: {exc}",
            ) from exc

    @app.get("/views/today", dependencies=[Depends(auth)])
    def view_today(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.1 Today slice — mirrors the Streamlit page's primary reads.

        Returns every data piece the Today view needs so the frontend renders
        without any business logic (§31.10). See ``_today_slice`` for details.
        """
        return _today_slice(conn)

    @app.get("/views/next-rep", dependencies=[Depends(auth)])
    def view_next_rep(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.2 Next Rep — lane performance with graduated confidence labels."""
        return _next_rep_slice(conn)

    @app.get("/views/validation", dependencies=[Depends(auth)])
    def view_validation(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.5 Funnel — full funnel data, aggregated + daily + charts."""
        return _funnel_slice(conn)

    @app.get("/charts/funnel", dependencies=[Depends(auth)])
    def view_funnel_chart(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.5 Funnel chart — 30-day aggregate funnel figure."""
        return _funnel_chart_figure(conn)

    @app.get("/charts/funnel-daily", dependencies=[Depends(auth)])
    def view_funnel_daily_chart(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.5 Daily breakdown stacked bar chart."""
        return _funnel_daily_chart_figure(conn)

    @app.get("/views/progress", dependencies=[Depends(auth)])
    def view_progress(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.3 Progress slice — mirrors the Streamlit page's primary reads.

        Returns every data piece the Progress view needs so the frontend renders
        without any business logic (§31.10). See ``_progress_slice`` for details.
        """
        return _progress_slice(conn)

    @app.get("/charts/follower-trend", dependencies=[Depends(auth)])
    def view_follower_trend(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.4 Follower trend — mirrors the Streamlit page's primary reads.

        Returns every data piece the Follower trend view needs so the frontend renders
        without any business logic (§31.10). See ``_follower_trend_figure`` for details.
        """
        return _follower_trend_figure(conn)

    @app.get("/views/content-performance", dependencies=[Depends(auth)])
    def view_content_performance(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """§14.4 Content Performance — lane grid, V/G/P/P, calibration."""
        return _content_performance_slice(conn)

    @app.get("/charts/lane-scatter", dependencies=[Depends(auth)])
    def view_lane_scatter(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """§14.4 Lane scatter — raw evidence for the last 30 days."""
        return _lane_scatter_figure(conn)

    @app.get("/views/weekly-review", dependencies=[Depends(auth)])
    def view_weekly_review(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.6 Weekly Review — summary metrics, existing review, history."""
        return _weekly_review_slice(conn)

    @app.get("/views/reply-queue", dependencies=[Depends(auth)])
    def view_reply_queue(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§29.7 Reply Target Queue — scored candidates with R/E/S/O cluster."""
        return _reply_queue_slice(conn)

    @app.get("/views/content-calendar", dependencies=[Depends(auth)])
    def view_content_calendar(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.11 Content Calendar — posted + drafted + planned in a date grid."""
        return _content_calendar_slice(conn)

    @app.get("/views/campaigns", dependencies=[Depends(auth)])
    def view_campaigns(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.12 Campaigns — dual-stream success criteria, item state machine."""
        return _campaigns_slice(conn)

    @app.get("/views/inspiration", dependencies=[Depends(auth)])
    def view_inspiration(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.13 Inspiration Library — saved posts + transforms."""
        return _inspiration_slice(conn)

    @app.get("/views/blogs", dependencies=[Depends(auth)])
    def view_blogs(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.14 Blogs index — pipeline state, version info."""
        return _blogs_slice(conn)

    @app.get("/views/blog/{blog_id}", dependencies=[Depends(auth)])
    def view_blog_detail(blog_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.15 Blog Editor — body, outline, versions."""
        return _blog_detail_slice(conn, blog_id)

    @app.get("/views/brain-dump", dependencies=[Depends(auth)])
    def view_brain_dump(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.9 Brain Dump — recent conversations + drafts."""
        return _brain_dump_slice(conn)

    @app.get("/views/account-researcher", dependencies=[Depends(auth)])
    def view_account_researcher(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§28.24 Account Researcher — analyzed target accounts."""
        return _account_researcher_slice(conn)

    @app.post("/forms/weekly-review", dependencies=[Depends(auth)])
    def post_weekly_review(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            review_id = submit_weekly_review(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"review_id": review_id}

    # ----- Manual entry write endpoints (§15) -----
    # Wrap the pure forms submit functions. Validation + FormError semantics
    # are unchanged; a FormError becomes a 400 with per-field detail.

    @app.post("/forms/snapshot", dependencies=[Depends(auth)])
    def post_snapshot(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            snapshot_id = submit_snapshot(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"snapshot_id": snapshot_id}

    # ----- Blog write endpoints (§14.14 / §14.15) -----

    @app.post("/blogs", dependencies=[Depends(auth)])
    def create_blog_endpoint(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        blog = _blogs.create_blog(
            conn,
            title=payload.get("title", "Untitled"),
            pillar=payload.get("pillar"),
            audience=payload.get("audience"),
            target_length_words=payload.get("target_length_words"),
            notes=payload.get("notes"),
        )
        return {"blog_id": blog.id, "slug": blog.slug, "status": blog.status}

    @app.put("/blogs/{blog_id}", dependencies=[Depends(auth)])
    def save_blog_endpoint(
        blog_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        version = _blogs.save_blog(
            conn, blog_id,
            body_markdown=payload.get("body_markdown"),
            outline_markdown=payload.get("outline_markdown"),
            title=payload.get("title"),
            status=payload.get("status"),
            daniel_revision_note=payload.get("daniel_revision_note"),
        )
        if version is None:
            return {"saved": False, "reason": "no_change"}
        return {"saved": True, "version_number": version.version_number}

    @app.put("/blogs/{blog_id}/status", dependencies=[Depends(auth)])
    def transition_blog_status(
        blog_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        version = _blogs.transition_status(
            conn, blog_id, payload["new_status"],
            daniel_revision_note=payload.get("daniel_revision_note"),
            external_url=payload.get("external_url"),
        )
        return {"new_status": payload["new_status"], "version_number": version.version_number}

    @app.get("/blogs/{blog_id}/versions", dependencies=[Depends(auth)])
    def list_blog_versions(
        blog_id: int, conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        versions = _blogs.list_versions(conn, blog_id=blog_id)
        return {"blog_id": blog_id, "versions": [
            {"version_number": v.version_number, "created_at": v.created_at_utc,
             "created_by": v.created_by, "status_at_version": v.status_at_version,
             "title_at_version": v.title_at_version, "is_current": v.is_current_for_blog}
            for v in versions
        ]}

    # ----- Campaign write endpoints (§14.12) -----

    @app.post("/campaigns", dependencies=[Depends(auth)])
    def create_campaign_endpoint(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        cid = _campaigns.create_campaign(
            conn,
            name=payload["name"],
            theme=payload.get("theme"),
            hypothesis=payload.get("hypothesis"),
            start_date=payload["start_date"],
            end_date=payload["end_date"],
            success_criteria=payload.get("success_criteria", {}),
            pillar=payload.get("pillar"),
            content_type=payload.get("content_type"),
            notes=payload.get("notes"),
        )
        return {"campaign_id": cid}

    @app.put("/campaigns/{campaign_id}/activate", dependencies=[Depends(auth)])
    def activate_campaign_endpoint(
        campaign_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        _campaigns.activate_campaign(conn, campaign_id=campaign_id)
        return {"ok": True, "campaign_id": campaign_id, "status": "active"}

    # ----- Inspiration write endpoints (§14.13) -----

    @app.post("/inspirations", dependencies=[Depends(auth)])
    def save_inspiration_endpoint(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        iid = _inspiration.save_inspiration(
            conn,
            source_post_text=payload["source_post_text"],
            source_url=payload.get("source_url"),
            source_author=payload.get("source_author"),
            tags=payload.get("tags"),
            notes=payload.get("notes"),
        )
        return {"inspiration_id": iid}

    @app.put("/inspirations/{inspiration_id}/archive", dependencies=[Depends(auth)])
    def archive_inspiration_endpoint(
        inspiration_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        _inspiration.archive_inspiration(conn, inspiration_id=inspiration_id)
        return {"ok": True, "inspiration_id": inspiration_id}

    # ----- Agent action endpoints — one-click automation from views -----
    # These wrap the agent tool functions (app.agent.tools) as direct HTTP
    # calls so views can surface "Draft reply", "Score candidates", etc. as
    # buttons without going through the chat interface.

    @app.post("/agent/score-candidates", dependencies=[Depends(auth)])
    def score_candidates(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Score all pending reply-target candidates."""
        try:
            result = _score_reply_candidates(conn)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/agent/find-reply-targets", dependencies=[Depends(auth)])
    def find_reply_targets(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Discover new reply-target candidates via the agent."""
        try:
            result = _find_reply_targets(conn)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/agent/draft-reply", dependencies=[Depends(auth)])
    def draft_reply(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Draft a reply to a specific reply-target post."""
        try:
            result = _save_draft_reply(
                conn,
                text=payload["text"],
                target_post_url=payload["target_post_url"],
                target_post_text=payload.get("target_post_text"),
                pillar=payload.get("pillar"),
                content_type=payload.get("content_type"),
            )
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.put("/reply-targets/{rt_id}/skip", dependencies=[Depends(auth)])
    def skip_reply_target(
        rt_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Skip a reply target with a reason."""
        reason = payload.get("skip_reason", "not_relevant")
        conn.execute(
            "UPDATE reply_targets SET status = 'skipped', skip_reason = ? WHERE id = ?",
            (reason, rt_id),
        )
        conn.commit()
        return {"ok": True, "reply_target_id": rt_id, "status": "skipped"}

    @app.put("/reply-targets/{rt_id}/mark-posted", dependencies=[Depends(auth)])
    def mark_reply_posted(
        rt_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Mark a reply target as posted (with optional posted URL)."""
        posted_url = payload.get("posted_url")
        conn.execute(
            "UPDATE reply_targets SET status = 'posted', posted_reply_url = ? WHERE id = ?",
            (posted_url, rt_id),
        )
        conn.commit()
        return {"ok": True, "reply_target_id": rt_id, "status": "posted"}

    @app.post("/brain-dumps", dependencies=[Depends(auth)])
    def create_and_process_brain_dump(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Create a brain dump + run agent processing in one step."""
        raw_text = payload.get("raw_text", "").strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="raw_text is required")
        try:
            dump_id = _brain_dump.create_dump(conn, raw_text=raw_text)
            result = _brain_dump.process(conn, dump_id)
            return {
                "ok": True, "brain_dump_id": dump_id,
                "candidates": len(result.candidates) if result else 0,
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/blogs/{blog_id}/outline", dependencies=[Depends(auth)])
    def outline_blog(
        blog_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Agent-generate a blog outline."""
        try:
            return _outline_blog_to_dict(
                conn, blog_id=blog_id,
                daniel_notes=payload.get("notes"),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/blogs/{blog_id}/draft", dependencies=[Depends(auth)])
    def draft_blog(
        blog_id: int, payload: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Agent-draft a blog from its outline."""
        try:
            return _draft_blog_to_dict(
                conn, blog_id=blog_id,
                target_length_words=payload.get("target_length_words"),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/blogs/{blog_id}/suggest-edits", dependencies=[Depends(auth)])
    def suggest_blog_edits(
        blog_id: int, conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        """Agent-suggest edits for a blog draft."""
        try:
            return _suggest_blog_edits_to_dict(conn, blog_id=blog_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/forms/post", dependencies=[Depends(auth)])
    def post_log(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            post_id = submit_post(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"post_id": post_id}

    @app.post("/forms/correction", dependencies=[Depends(auth)])
    def post_correction(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            correction_id = submit_correction(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"correction_id": correction_id}

    @app.post("/forms/classify", dependencies=[Depends(auth)])
    def post_classify(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            cid = submit_classification(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"classification_id": cid}

    @app.post("/forms/daily-activity", dependencies=[Depends(auth)])
    def post_daily_activity(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            result = submit_daily_activity(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"result": result}

    @app.post("/forms/stir-event", dependencies=[Depends(auth)])
    def post_stir_event(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            event_id = submit_stir_event(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"event_id": event_id}

    @app.post("/forms/stir-tester", dependencies=[Depends(auth)])
    def post_stir_tester(
        payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        try:
            tester_id = submit_tester(conn, payload)
        except FormError as exc:
            raise _form_error(exc) from exc
        return {"tester_id": tester_id}

    @app.get("/views/needs-tagging", dependencies=[Depends(auth)])
    def view_needs_tagging(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        rows = needs_tagging(conn)
        return {"posts": [dict(r) for r in rows]}

    @app.get("/views/needs-post-id", dependencies=[Depends(auth)])
    def view_needs_post_id(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        rows = needs_post_id(conn)
        return {"posts": [dict(r) for r in rows]}

    # ----- Settings read/update (§14.7) -----

    @app.get("/settings", dependencies=[Depends(auth)])
    def read_settings(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        rows = conn.execute("SELECT key FROM settings ORDER BY key").fetchall()
        return {"settings": {r["key"]: get_setting(conn, r["key"]) for r in rows}}

    @app.put("/settings/{key}", dependencies=[Depends(auth)])
    def update_setting(
        key: str, body: SettingValue, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        # RV5-W9: reject unknown keys so typos surface immediately instead of
        # silently creating dead rows in the settings table.
        known = conn.execute("SELECT key FROM settings").fetchall()
        known_keys = {r["key"] for r in known}
        if key not in known_keys:
            raise HTTPException(status_code=400, detail=f"Unknown setting key: {key!r}")
        # set_setting handles the JSON encode + §28.30 audit-log write-through.
        set_setting(conn, key, body.value)
        return {"ok": True, "key": key, "value": body.value}

    # ----- Managed secrets (§31.5): write-only, stored in the OS Keychain -----
    # The packaged app can't read the repo .env (cwd is "/" under Finder), so the
    # Anthropic key lives in the macOS Keychain. These report presence (never the
    # value) and let Settings write a new key. The agent reads it from os.environ
    # at client construction; __main__ exports Keychain->env at boot, and the PUT
    # below also updates os.environ so a freshly-set key takes effect without a
    # restart.
    _MANAGED_SECRETS = {"ANTHROPIC_API_KEY"}

    @app.get("/settings/secrets", dependencies=[Depends(auth)])
    def read_secrets() -> dict[str, Any]:
        return {
            "secrets": {
                name: {"present": bool(resolve_secret(name))}
                for name in sorted(_MANAGED_SECRETS)
            }
        }

    @app.put("/settings/secrets/{name}", dependencies=[Depends(auth)])
    def update_secret(name: str, body: SecretBody) -> dict[str, Any]:
        if name not in _MANAGED_SECRETS:
            raise HTTPException(status_code=400, detail=f"Unknown secret: {name!r}")
        value = body.value.strip()
        if not value:
            raise HTTPException(
                status_code=400, detail="Secret value must not be empty."
            )
        store_secret(name, value)
        # Reflect into the running process so AgentClient() picks it up on the
        # next request without an app restart (it reads ANTHROPIC_API_KEY from env).
        os.environ[name] = value
        return {"ok": True, "name": name, "present": True}

    # ----- Agent session endpoints (§14.8, §28) -----
    # Wrap the existing AgentClient.send_message_sync. The §28.10 publish
    # tools remain unreachable from here (they are not in AGENT_TOOLS; the
    # invariants at boot guarantee it). Streaming (SSE) is a follow-up; the
    # agent client is synchronous-only today (client.py S11 note).

    @app.post("/agent/conversations", dependencies=[Depends(auth)])
    def create_conversation(
        body: StartConversationBody, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        cid = start_conversation(
            conn, title=body.title, context_seed=body.context_seed
        )
        return {"conversation_id": cid}

    @app.get("/agent/conversations", dependencies=[Depends(auth)])
    def list_conversations(
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT * FROM agent_conversations ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return {"conversations": [dict(r) for r in rows]}

    @app.get(
        "/agent/conversations/{conversation_id}/messages",
        dependencies=[Depends(auth)],
    )
    def list_messages(
        conversation_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT id, role, content, tool_calls_json, tool_call_id, model,
                   input_tokens, output_tokens, confidence_label
            FROM agent_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        return {
            "conversation_id": conversation_id,
            "messages": [dict(r) for r in rows],
        }

    @app.post(
        "/agent/conversations/{conversation_id}/messages",
        dependencies=[Depends(auth)],
    )
    def send_message(
        conversation_id: int,
        body: SendMessageBody,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict[str, Any]:
        # Coach conversations use a simplified no-tools path (§14.10):
        # advice-only text-in text-out, matching the Streamlit Coach's
        # separate implementation.
        ctx = conn.execute(
            "SELECT context_seed FROM agent_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if ctx and ctx["context_seed"] == "coach":
            return _coach_turn(conn, conversation_id, body.text)

        client = agent_factory()
        turn = client.send_message_sync(
            conn, conversation_id=conversation_id, user_text=body.text
        )
        return {
            "user_text": turn.user_text,
            "assistant_text": turn.assistant_text,
            "tool_calls": turn.tool_calls,
            "input_tokens": turn.input_tokens,
            "output_tokens": turn.output_tokens,
            "cost_usd": turn.cost_usd,
            "model": turn.model,
            "error": turn.error,
        }

    @app.post(
        "/agent/conversations/{conversation_id}/stream",
        dependencies=[Depends(auth)],
    )
    def stream_message(
        conversation_id: int, body: SendMessageBody
    ) -> StreamingResponse:
        """SSE surface for the Agent Chat view (§14.8, §31.3).

        Streams tokens in real time via the Anthropic streaming API. Events:
        ``start`` → ``text_delta`` (per chunk) → ``done`` (totals), or
        ``error``. Opens its own DB connection inside the generator so it
        outlives the handler return.
        """

        def event_gen() -> Iterator[str]:
            conn = factory()
            try:
                yield _sse("start", {"conversation_id": conversation_id})

                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if not api_key:
                    yield _sse("error", {"error": "Anthropic API key not configured. Set it in Settings → API keys."})
                    return

                import anthropic as _anthropic
                from app.agent import prompt_builder
                from app.agent import tools as _agent_tools

                # Check coach mode.
                ctx = conn.execute(
                    "SELECT context_seed FROM agent_conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                is_coach = ctx and ctx["context_seed"] == "coach"

                # Persist user message.
                conn.execute(
                    "INSERT INTO agent_messages (conversation_id, role, content) "
                    "VALUES (?, 'user', ?)",
                    (conversation_id, body.text),
                )
                conn.commit()

                # Load conversation history + system prompt.
                rows = conn.execute(
                    "SELECT role, content FROM agent_messages "
                    "WHERE conversation_id = ? ORDER BY id",
                    (conversation_id,),
                ).fetchall()
                messages = [{"role": r["role"], "content": r["content"]} for r in rows]
                system_prompt = prompt_builder.build_system_prompt(conn)
                model = "claude-sonnet-4-20250514"

                api_kwargs: dict[str, Any] = {
                    "model": model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                }
                if not is_coach:
                    api_kwargs["tools"] = [
                        t.to_anthropic_spec() for t in _agent_tools.AGENT_TOOLS
                    ]

                client_api = _anthropic.Anthropic(api_key=api_key, timeout=120.0)

                # Stream tokens.
                full_text: list[str] = []
                in_tok = 0
                out_tok = 0
                with client_api.messages.stream(**api_kwargs) as stream:
                    for text in stream.text_stream:
                        full_text.append(text)
                        yield _sse("text_delta", {"text": text})
                    final = stream.get_final_message()
                    in_tok = final.usage.input_tokens
                    out_tok = final.usage.output_tokens
                    # Report tool calls (if any — agent mode).
                    for block in final.content:
                        if getattr(block, "type", None) == "tool_use":
                            yield _sse("tool_call", {
                                "name": block.name,
                                "input": block.input,
                            })

                assistant_text = "".join(full_text)

                # Persist assistant message.
                conn.execute(
                    "INSERT INTO agent_messages "
                    "(conversation_id, role, content, model, input_tokens, output_tokens) "
                    "VALUES (?, 'assistant', ?, ?, ?, ?)",
                    (conversation_id, assistant_text, model, in_tok, out_tok),
                )
                conn.commit()

                yield _sse("done", {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cost_usd": None,
                    "error": None,
                })
            except Exception as exc:  # noqa: BLE001
                yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                conn.close()

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    # ----- Publish endpoint (§28.10) — server-side click-handler replication -----
    # This is the FastAPI equivalent of the §14.8 Streamlit click-handler. It is
    # the ONLY legitimate external caller of the internal publish tools (the
    # agent can never reach them — they are absent from AGENT_TOOLS and the boot
    # invariants prove it). The raw confirmation token is minted and consumed
    # ENTIRELY within this process and is NEVER returned to the frontend; the
    # frontend only sends the typed-'confirm' phrase + the (possibly edited) text.
    # The six-check chain + atomic transaction live unchanged in publish.py.

    @app.post("/publish", dependencies=[Depends(auth)])
    def publish(
        body: PublishBody, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        if body.confirm.strip().lower() != "confirm":
            raise HTTPException(
                status_code=400, detail="must type 'confirm' to publish"
            )
        char_count = len(body.text)
        if not 0 < char_count <= 280:
            raise HTTPException(
                status_code=400,
                detail=f"text must be 1..280 chars (got {char_count})",
            )
        # Same sequence as the Streamlit modal's confirm handler: write the
        # (possibly edited) text, invalidate stale tokens, mint a fresh
        # single-use token, publish atomically, drop the raw token.
        # RV5-W2: wrapped in try/except so failures return structured errors
        # instead of raw 500s with Python tracebacks.
        try:
            confirmation.update_post_text_for_publish(
                conn, post_id=body.post_id, new_text=body.text, message_id=body.message_id
            )
            confirmation.invalidate_unconsumed_tokens_for_post(conn, post_id=body.post_id)
            minted = confirmation.mint_confirmation_token(
                conn, post_id=body.post_id, draft_text=body.text
            )
            result = _internal_tools.publish_post_to_x(
                conn,
                post_id=body.post_id,
                confirmation_token=minted.raw_token,
                message_id=body.message_id,
            )
            del minted  # the raw token is gone from this frame.
        except (sqlite3.IntegrityError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface safely
            raise HTTPException(
                status_code=502, detail=f"Publish failed: {type(exc).__name__}: {exc}"
            ) from exc
        return {
            "success": result.success,
            "post_id": result.post_id,
            "method": result.method,
            "intent_url": result.intent_url,
            "x_post_id": result.x_post_id,
            "error": result.error,
            "error_kind": result.error_kind,
        }

    return app
