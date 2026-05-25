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
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date as _date_t
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent import _internal_tools, confirmation, invariants
from app.agent.client import AgentClient, start_conversation
from app.agent.content_types import get_content_type_gaps, get_recommendation_window_days
from app.db import apply_migrations, connect
from app.forms import FormError, get_setting, set_setting
from app.forms.correction import submit_correction
from app.forms.post_log import submit_post
from app.forms.snapshot import submit_snapshot
from app.paths import resolve_db_path
from app.service.security import BearerTokenAuth

ConnFactory = Callable[[], sqlite3.Connection]
AgentClientFactory = Callable[[], AgentClient]

SERVICE_NAME = "x-growth-dashboard-service"
SERVICE_VERSION = "0.1.0"


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
    """Open the resolved DB (§31.5) and ensure migrations are applied.

    Resolves the path fresh on each call so a runtime migration to Application
    Support (performed by the sidecar before it serves) is picked up — the
    import-time ``DEFAULT_DB_PATH`` constant would be stale across that move.
    """
    conn = connect(resolve_db_path())
    apply_migrations(conn)
    return conn


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

    # 3. Velocity gating (§13 rule 6).
    delta_7d = snapshot["delta_7d"] if snapshot else None
    velocity_measurable = delta_7d is not None and abs(delta_7d) >= 10
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

    # 9. Account last 7 (kept for the account table at the bottom).
    account_last_7 = conn.execute(
        "SELECT * FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 7"
    ).fetchall()

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
        "account_last_7": [dict(r) for r in account_last_7],
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
        DEV-ONLY. When set, allow these browser origins to call the loopback
        sidecar cross-origin (the §31 Step 0 screenshot-diff loop). The packaged
        app and ``app.service.__main__`` NEVER set this, so production emits no
        CORS headers — the §31.10 "no new network surface" guarantee is intact
        (auth is still the per-launch bearer token; CORS only relaxes the
        *browser's* same-origin check, not the loopback bind).
    """
    factory = conn_factory or _default_conn_factory
    agent_factory = agent_client_factory or (lambda: AgentClient())
    if run_invariants:
        invariants.run_all()

    app = FastAPI(title="X Growth Dashboard — local service", version=SERVICE_VERSION)
    auth = BearerTokenAuth(token)

    if dev_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=dev_cors_origins,
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
        rows = conn.execute("SELECT * FROM v_lane_performance").fetchall()
        return {"slice": "next_rep", "lane_performance": [dict(r) for r in rows]}

    @app.get("/views/validation", dependencies=[Depends(auth)])
    def view_validation(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.5 Funnel — last-7 validation funnel (Stir conversion events)."""
        rows = conn.execute(
            "SELECT * FROM v_funnel_daily ORDER BY event_date DESC LIMIT 7"
        ).fetchall()
        return {"slice": "validation_status", "funnel_last_7": [dict(r) for r in rows]}

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

    # ----- Settings read/update (§14.7) -----

    @app.get("/settings", dependencies=[Depends(auth)])
    def read_settings(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        rows = conn.execute("SELECT key FROM settings ORDER BY key").fetchall()
        return {"settings": {r["key"]: get_setting(conn, r["key"]) for r in rows}}

    @app.put("/settings/{key}", dependencies=[Depends(auth)])
    def update_setting(
        key: str, body: SettingValue, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        # set_setting handles the JSON encode + §28.30 audit-log write-through.
        set_setting(conn, key, body.value)
        return {"ok": True, "key": key, "value": body.value}

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
            "SELECT * FROM agent_conversations ORDER BY id DESC"
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

        Emits lifecycle events: ``start`` → ``assistant`` (full text) → one
        ``tool_call`` per dispatched tool → ``done`` (token/cost totals), or
        ``error``. The agent client is synchronous today (client.py S11), so
        this frames the completed turn rather than incremental tokens; true
        token-by-token streaming is a follow-up that swaps _call_model for the
        Anthropic streaming API. The frontend consumes the same event shape
        either way.

        Opens its own connection inside the generator (not via the request
        dependency) so the connection outlives the handler return for the
        duration of the stream.
        """
        client = agent_factory()

        def event_gen() -> Iterator[str]:
            conn = factory()
            try:
                yield _sse("start", {"conversation_id": conversation_id})
                turn = client.send_message_sync(
                    conn, conversation_id=conversation_id, user_text=body.text
                )
                if turn.error:
                    yield _sse("error", {"error": turn.error})
                else:
                    yield _sse("assistant", {"text": turn.assistant_text})
                    for tc in turn.tool_calls:
                        yield _sse("tool_call", tc)
                yield _sse(
                    "done",
                    {
                        "input_tokens": turn.input_tokens,
                        "output_tokens": turn.output_tokens,
                        "cost_usd": turn.cost_usd,
                        "error": turn.error,
                    },
                )
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
