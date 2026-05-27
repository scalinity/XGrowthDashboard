
"""Route registration for the FastAPI sidecar."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import date as _date_t
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import _internal_tools, confirmation
from app.agent import blogs as _blogs
from app.agent import brain_dump as _brain_dump
from app.agent import campaigns as _campaigns
from app.agent import inspiration as _inspiration
from app.agent.client import delete_conversation, start_conversation
from app.agent.tools import (
    _draft_blog_to_dict,
    _outline_blog_to_dict,
    _record_reply_target,
    _save_draft_reply,
    _score_reply_candidates,
    _suggest_blog_edits_to_dict,
)
from app.forms import FormError, get_setting, set_setting
from app.forms.classify import submit_classification
from app.forms.correction import submit_correction
from app.forms.daily_reps import submit_daily_activity
from app.forms.post_log import add_post_id, submit_post
from app.forms.queues import needs_post_id, needs_tagging
from app.forms.snapshot import submit_snapshot
from app.forms.stir_event import submit_stir_event
from app.forms.stir_tester import submit_tester
from app.forms.weekly_review import submit_weekly_review
from app.jobs import agent_ops, post_classification_sync, x_activity_sync
from app.secret_store import resolve_secret, store_secret
from scripts import collect_account_snapshot
from app.service.agent_status import build_agent_mode, build_capabilities
from app.service.constants import SERVICE_NAME, SERVICE_VERSION
from app.service.diagnostics import (
    build_diagnostics_payload,
    build_health_details,
    format_diagnostics_text,
)
from app.service.helpers import _automation_queue_row, _form_error, _sse
from app.service.models import (
    AgentModeResponse,
    CapabilitiesResponse,
    ConversationsResponse,
    DiagnosticsCopyResponse,
    HealthDetailsResponse,
    MessagesResponse,
    PublishBody,
    PublishResponse,
    ReplyQueueResponse,
    SecretBody,
    SecretsResponse,
    SendMessageBody,
    SettingsResponse,
    SettingValue,
    StartConversationBody,
    TodayResponse,
)
from app.service.settings_schema import (
    MANAGED_SECRETS,
    assert_known_secret_name,
    assert_known_setting_key,
    assert_valid_setting_value,
)
from app.service.legacy_handlers import (
    _account_researcher_slice,
    _blog_detail_slice,
    _blogs_slice,
    _brain_dump_slice,
    _campaigns_slice,
    _coach_turn,
    _coach_turn_stream,
    _content_calendar_slice,
    _content_performance_slice,
    _follower_trend_figure,
    _funnel_chart_figure,
    _funnel_daily_chart_figure,
    _funnel_slice,
    _inspiration_slice,
    _lane_scatter_figure,
    _next_rep_slice,
    _progress_slice,
    _reply_queue_slice,
    _today_slice,
    _weekly_review_slice,
)


def register_routes(app, auth, get_conn, agent_factory, conn_factory):
        @app.get("/health")
        def health() -> dict[str, Any]:
            """Liveness probe for the Tauri shell's sidecar handshake. Unauthenticated."""
            return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

        @app.get("/health/details", dependencies=[Depends(auth)], response_model=HealthDetailsResponse)
        def health_details(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
            """Structured non-sensitive sidecar readiness details for the native shell."""
            return build_health_details(conn, service_version=SERVICE_VERSION)

        @app.get("/diagnostics/copy", dependencies=[Depends(auth)], response_model=DiagnosticsCopyResponse)
        def diagnostics_copy(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
            payload = build_diagnostics_payload(conn, service_version=SERVICE_VERSION)
            return {
                "diagnostics": payload,
                "text": format_diagnostics_text(payload),
            }

        @app.get("/api/user-metrics", dependencies=[Depends(auth)])
        def get_user_metrics(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            """Fetch and persist today's account metrics from X API (Phase 7).

            The desktop Today view treats this as the foreground refresh path: it
            writes an immutable API snapshot when possible, then returns the same
            metric shape the form can display. If a manual snapshot already exists,
            the scheduled-job helper skips the insert and we return the canonical
            row that is already driving Today.
            """
            try:
                summary = collect_account_snapshot.run(conn)
                today_iso = _date_t.today().isoformat()
                row = conn.execute(
                    """
                    SELECT username, profile_url, x_user_id, followers_count,
                           following_count, post_count, listed_count
                    FROM account_snapshots
                    WHERE snapshot_date = ?
                    ORDER BY collected_at_utc ASC
                    LIMIT 1
                    """,
                    (today_iso,),
                ).fetchone()
                if row is None:
                    if summary.get("error"):
                        raise RuntimeError(str(summary["error"]))
                    return {
                        **summary,
                        "username": get_setting(conn, "x_handle", "") or "",
                        "profile_url": get_setting(conn, "profile_url", "") or "",
                        "x_user_id": get_setting(conn, "x_user_id"),
                        "followers_count": None,
                        "following_count": None,
                        "post_count": None,
                        "listed_count": None,
                    }
                return {**summary, **dict(row)}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=502,
                    detail=f"X API fetch failed: {exc}",
                ) from exc

        @app.post("/api/sync-today", dependencies=[Depends(auth)])
        def sync_today_from_x(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            """Import owned X activity and reconcile today's manual logging rows."""
            try:
                return x_activity_sync.run(conn)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=502,
                    detail=f"X activity sync failed: {type(exc).__name__}: {exc}",
                ) from exc

        @app.get("/views/today", dependencies=[Depends(auth)], response_model=TodayResponse)
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

        @app.get("/views/reply-queue", dependencies=[Depends(auth)], response_model=ReplyQueueResponse)
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
                return agent_ops.score_pending_reply_targets(conn)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @app.post("/agent/grok-sweep", dependencies=[Depends(auth)])
        def run_grok_sweep(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            """Run the configured Grok discovery sweep for reply-target candidates."""
            try:
                from app.jobs import grok_discovery_sweep

                summary = grok_discovery_sweep.run_once_locked(conn)
                severity, message = grok_discovery_sweep.format_sweep_summary_for_ui(summary)
                return {
                    "ok": severity != "error",
                    "severity": severity,
                    "message": message,
                    "summary": summary,
                }
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @app.post("/agent/classify-posts", dependencies=[Depends(auth)])
        def classify_posts(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            """Automatically classify untagged imported posts into the v1 taxonomy."""
            try:
                return post_classification_sync.run(conn)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @app.post("/agent/find-reply-targets", dependencies=[Depends(auth)])
        def find_reply_targets(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            """Load active target accounts used by reply discovery workflows."""
            try:
                return agent_ops.find_reply_targets(conn)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        @app.post("/reply-targets", dependencies=[Depends(auth)])
        def create_reply_target(
            payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
        ) -> dict[str, Any]:
            """Record a manual reply-target candidate from the native queue view."""

            def clean_str(key: str) -> str | None:
                value = payload.get(key)
                if value is None:
                    return None
                cleaned = str(value).strip()
                return cleaned or None

            def clean_nonnegative_int(key: str) -> int | None:
                value = payload.get(key)
                if value is None or value == "":
                    return None
                try:
                    parsed = int(value)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail=f"{key} must be an integer") from exc
                if parsed < 0:
                    raise HTTPException(status_code=400, detail=f"{key} must be >= 0")
                return parsed

            target_post_url = clean_str("target_post_url")
            if target_post_url is None:
                raise HTTPException(status_code=400, detail="target_post_url is required")

            try:
                result = _record_reply_target(
                    conn,
                    target_post_url=target_post_url,
                    target_post_text=clean_str("target_post_text"),
                    target_user=clean_str("target_user"),
                    target_author_follower_count=clean_nonnegative_int("target_author_follower_count"),
                    like_count=clean_nonnegative_int("like_count"),
                    reply_count=clean_nonnegative_int("reply_count"),
                    repost_count=clean_nonnegative_int("repost_count"),
                    quote_count=clean_nonnegative_int("quote_count"),
                    pillar=clean_str("pillar"),
                    audience=clean_str("audience"),
                    reply_intent=clean_str("reply_intent"),
                    agent_reasoning=clean_str("notes"),
                    discovered_via="manual",
                )
                if "error" in result:
                    raise HTTPException(status_code=400, detail=result["error"])
                reply_target_id = int(result["reply_target_id"])
                score_result = _score_reply_candidates(conn, reply_target_id=reply_target_id)
                return {"ok": True, **result, **score_result}
            except HTTPException:
                raise
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

        @app.put("/forms/post-id", dependencies=[Depends(auth)])
        def put_post_id(
            payload: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)
        ) -> dict[str, Any]:
            try:
                post_id = int(payload.get("post_id"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="post_id must be an integer") from exc
            try:
                add_post_id(
                    conn,
                    post_id,
                    str(payload.get("x_post_id") or ""),
                    manual_url=payload.get("manual_url"),
                )
            except FormError as exc:
                raise _form_error(exc) from exc
            return {"ok": True, "post_id": post_id}

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
                cid = submit_classification(
                    conn, payload, allow_overwrite=bool(payload.get("allow_overwrite"))
                )
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
            return {"posts": [_automation_queue_row(r) for r in rows]}

        @app.get("/views/needs-post-id", dependencies=[Depends(auth)])
        def view_needs_post_id(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            rows = needs_post_id(conn)
            return {"posts": [_automation_queue_row(r) for r in rows]}

        # ----- Settings read/update (§14.7) -----

        @app.get("/settings", dependencies=[Depends(auth)], response_model=SettingsResponse)
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
            assert_known_setting_key(key, known_keys)
            current_value = get_setting(conn, key)
            assert_valid_setting_value(key, body.value, current_value=current_value)
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

        @app.get("/settings/secrets", dependencies=[Depends(auth)], response_model=SecretsResponse)
        def read_secrets() -> dict[str, Any]:
            return {
                "secrets": {
                    name: {"present": bool(resolve_secret(name))}
                    for name in sorted(MANAGED_SECRETS)
                }
            }

        @app.put("/settings/secrets/{name}", dependencies=[Depends(auth)])
        def update_secret(name: str, body: SecretBody) -> dict[str, Any]:
            assert_known_secret_name(name)
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

        @app.get("/agent/mode", dependencies=[Depends(auth)], response_model=AgentModeResponse)
        def agent_mode(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
            return build_agent_mode(conn)

        @app.get("/capabilities", dependencies=[Depends(auth)], response_model=CapabilitiesResponse)
        def capabilities(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
            return build_capabilities(conn)

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

        @app.get("/agent/conversations", dependencies=[Depends(auth)], response_model=ConversationsResponse)
        def list_conversations(
            conn: sqlite3.Connection = Depends(get_conn),
        ) -> dict[str, Any]:
            rows = conn.execute(
                "SELECT * FROM agent_conversations ORDER BY id DESC LIMIT 100"
            ).fetchall()
            return {"conversations": [dict(r) for r in rows]}

        @app.delete("/agent/conversations/{conversation_id}", dependencies=[Depends(auth)])
        def delete_agent_conversation(
            conversation_id: int, conn: sqlite3.Connection = Depends(get_conn)
        ) -> dict[str, Any]:
            deleted = delete_conversation(conn, conversation_id=conversation_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return {"ok": True, "conversation_id": conversation_id}

        @app.get(
            "/agent/conversations/{conversation_id}/messages",
            dependencies=[Depends(auth)],
            response_model=MessagesResponse,
        )
        def list_messages(
            conversation_id: int, conn: sqlite3.Connection = Depends(get_conn)
        ) -> dict[str, Any]:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls_json, tool_call_id, model,
                       input_tokens, output_tokens, confidence_label, evidence_citations_json
                FROM agent_messages
                WHERE conversation_id = ?
                  AND role != 'tool_result'
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
            tool_result_rows = conn.execute(
                """
                SELECT tool_call_id, content
                FROM agent_messages
                WHERE conversation_id = ?
                  AND role = 'tool_result'
                  AND tool_call_id IS NOT NULL
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
            tool_results_by_id = {
                r["tool_call_id"]: r["content"] for r in tool_result_rows
            }
            messages: list[dict[str, Any]] = []
            for row in rows:
                msg = dict(row)
                msg["tool_results_json"] = None
                if msg.get("tool_calls_json"):
                    try:
                        calls = json.loads(msg["tool_calls_json"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        calls = []
                    results = [
                        {"tool_call_id": call.get("id"), "content": tool_results_by_id[call.get("id")]}
                        for call in calls
                        if isinstance(call, dict) and call.get("id") in tool_results_by_id
                    ]
                    if results:
                        msg["tool_results_json"] = json.dumps(results)
                messages.append(msg)
            return {
                "conversation_id": conversation_id,
                "messages": messages,
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
            """SSE surface for Agent Chat using the canonical agent turn path.

            The stream endpoint must share the same injected AgentClient as
            ``/messages`` so tests, persistence, tool dispatch, cost ceiling, and
            missing-key errors all behave identically. It emits coarse-grained SSE
            frames around that turn so the desktop UI never looks hung.
            """

            def event_gen() -> Iterator[str]:
                conn = conn_factory()
                try:
                    yield _sse("start", {"conversation_id": conversation_id})
                    yield _sse("user", {"text": body.text})
                    yield _sse("thinking_delta", {"text": "Preparing agent context..."})

                    ctx = conn.execute(
                        "SELECT context_seed FROM agent_conversations WHERE id = ?",
                        (conversation_id,),
                    ).fetchone()
                    if ctx and ctx["context_seed"] == "coach":
                        for event_type, payload in _coach_turn_stream(
                            conn, conversation_id, body.text
                        ):
                            yield _sse(event_type, payload)
                            if event_type == "error":
                                return
                        return

                    client = agent_factory()
                    for event_type, payload in client.send_message_stream_sync(
                        conn, conversation_id=conversation_id, user_text=body.text
                    ):
                        yield _sse(event_type, payload)
                        if event_type == "error":
                            return
                    return
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

        @app.post("/publish", dependencies=[Depends(auth)], response_model=PublishResponse)
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

