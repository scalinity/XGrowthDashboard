"""FastAPI application factory for the sidecar (§31.3).

``create_app`` builds an app with:

- a per-request SQLite connection (via the project's ``app.db.connect``),
- per-launch bearer-token auth on every non-health route,
- the §28 startup invariants run once at boot (same guarantees as ``streamlit run``).

Endpoints are added incrementally through Phase 11.0. This module owns the
HTTP shape only; all reads/writes delegate to existing backend code.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.agent import invariants
from app.agent.client import AgentClient, start_conversation
from app.db import DEFAULT_DB_PATH, apply_migrations, connect
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


def _default_conn_factory() -> sqlite3.Connection:
    """Open the real DB and ensure migrations are applied (sidecar default)."""
    conn = connect(DEFAULT_DB_PATH)
    apply_migrations(conn)
    return conn


def create_app(
    *,
    token: str,
    conn_factory: ConnFactory | None = None,
    agent_client_factory: AgentClientFactory | None = None,
    run_invariants: bool = True,
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
    """
    factory = conn_factory or _default_conn_factory
    agent_factory = agent_client_factory or (lambda: AgentClient())
    if run_invariants:
        invariants.run_all()

    app = FastAPI(title="X Growth Dashboard — local service", version=SERVICE_VERSION)
    auth = BearerTokenAuth(token)

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

        Same canonical views the page and the agent's ``query_dashboard_state``
        tool use (``v_daily_reps`` latest + last-7 ``v_account_daily``).
        """
        daily_reps = conn.execute(
            "SELECT * FROM v_daily_reps ORDER BY activity_date DESC LIMIT 1"
        ).fetchall()
        account_last_7 = conn.execute(
            "SELECT * FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 7"
        ).fetchall()
        return {
            "slice": "today",
            "daily_reps": [dict(r) for r in daily_reps],
            "account_last_7": [dict(r) for r in account_last_7],
        }

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

    return app
