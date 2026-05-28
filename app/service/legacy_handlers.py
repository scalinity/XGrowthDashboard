"""View slice builders and coach handlers for the FastAPI sidecar (§31.3).

HTTP route registration lives in ``app.service.routes.registry``. Shared
read models for Today, Progress, Weekly Review, Content Performance, and
Reply Queue live in ``app/read_models/`` and are re-exported here for
backward-compatible imports.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import date as _date_t
from datetime import timedelta as _timedelta
from typing import Any


from app.agent import coach as _coach
from app.components.charts.follower_trend import FollowerPoint, follower_trend_chart
from app.components.charts.funnel import (
    APP_STORE_GAP_LABEL,
    WHAT_WE_KNOW_TABLE_ROWS,
    build_funnel_stages,
    funnel_chart,
)
from app.agent.reply_targets import engagement_footnote as _engagement_footnote
from app.agent.tools import (
    _load_engagement_surface_settings,
)
from app.agent import blogs as _blogs
from app.agent import calendar as _calendar
from app.agent import campaigns as _campaigns
from app.agent import inspiration as _inspiration
from app.forms import get_setting
from app.read_models.content_performance import (
    build_content_performance_read_model as _content_performance_slice,
)
from app.read_models.progress import build_progress_read_model as _progress_slice
from app.read_models.reply_queue import build_reply_queue_read_model as _reply_queue_slice
from app.read_models.today import build_today_read_model as _today_slice
from app.read_models.weekly_review import build_weekly_review_read_model as _weekly_review_slice


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
        result = _coach.enforce(
            assistant_text,
            conn,
            refuse_without_evidence=bool(get_setting(conn, "coach_refuse_without_evidence", True)),
        )

        # Persist assistant message.
        conn.execute(
            "INSERT INTO agent_messages "
            "(conversation_id, role, content, model, input_tokens, output_tokens, evidence_citations_json) "
            "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
            (
                conversation_id,
                result.clean_text,
                model,
                in_tok,
                out_tok,
                json.dumps([c.to_dict() for c in result.surviving]) if result.surviving else None,
            ),
        )
        conn.commit()
        return {
            "user_text": user_text, "assistant_text": result.clean_text,
            "tool_calls": [], "input_tokens": in_tok, "output_tokens": out_tok,
            "cost_usd": None, "model": model, "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — surface all errors to the UI
        return {
            "user_text": user_text, "assistant_text": None, "tool_calls": [],
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
            "model": None, "error": f"{type(exc).__name__}: {exc}",
        }


def _coach_turn_stream(
    conn: sqlite3.Connection, conversation_id: int, user_text: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Coach SSE turn: advice-only streaming text with no tool surface."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield (
            "error",
            {
                "error": "Anthropic API key not configured. Set it in Settings → API keys."
            },
        )
        return

    from app.agent import prompt_builder  # lazy to avoid circular imports

    import anthropic

    conn.execute(
        "INSERT INTO agent_messages (conversation_id, role, content) VALUES (?, 'user', ?)",
        (conversation_id, user_text),
    )
    conn.commit()

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
        with client.messages.stream(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        ) as stream:
            for event in stream:
                if getattr(event, "type", None) == "text":
                    yield ("text_delta", {"text": getattr(event, "text", "")})
            resp = stream.get_final_message()

        text_parts = [
            getattr(b, "text", "")
            for b in resp.content
            if getattr(b, "type", None) == "text"
        ]
        assistant_text = "".join(text_parts).strip()
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        result = _coach.enforce(
            assistant_text,
            conn,
            refuse_without_evidence=bool(get_setting(conn, "coach_refuse_without_evidence", True)),
        )

        conn.execute(
            "INSERT INTO agent_messages "
            "(conversation_id, role, content, model, input_tokens, output_tokens, evidence_citations_json) "
            "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
            (
                conversation_id,
                result.clean_text,
                model,
                in_tok,
                out_tok,
                json.dumps([c.to_dict() for c in result.surviving]) if result.surviving else None,
            ),
        )
        conn.commit()
        yield (
            "assistant",
            {
                "text": result.clean_text,
                "model": model,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
            },
        )
        yield (
            "done",
            {
                "user_text": user_text,
                "assistant_text": result.clean_text,
                "tool_calls": [],
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost_usd": None,
                "model": model,
                "error": None,
            },
        )
    except Exception as exc:  # noqa: BLE001 — surface all errors to the UI
        yield ("error", {"error": f"{type(exc).__name__}: {exc}"})


__all__ = [
    "_account_researcher_slice",
    "_blog_detail_slice",
    "_blogs_slice",
    "_brain_dump_slice",
    "_campaigns_slice",
    "_coach_turn",
    "_coach_turn_stream",
    "_content_calendar_slice",
    "_content_performance_slice",
    "_follower_trend_figure",
    "_funnel_chart_figure",
    "_funnel_daily_chart_figure",
    "_funnel_slice",
    "_inspiration_slice",
    "_lane_scatter_figure",
    "_next_rep_slice",
    "_progress_slice",
    "_reply_queue_slice",
    "_today_slice",
    "_weekly_review_slice",
]
