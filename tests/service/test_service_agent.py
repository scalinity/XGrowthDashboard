"""Phase 11.0 agent-endpoint tests for the FastAPI sidecar (§14.8, §31.3).

Uses a stub AgentClient (overrides _call_model) so the round trip is exercised
end-to-end through the service without touching the Anthropic API. Confirms the
endpoints reuse the existing client.send_message_sync persistence path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.client import AgentClient
from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "agent-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _StubClient(AgentClient):
    """An AgentClient whose model call is deterministic and offline."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        return ("stub assistant reply", [], 12, 7)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "agent_svc.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)  # cost-ceiling rows the send path reads
    conn.close()

    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    app = create_app(
        token=TOKEN,
        conn_factory=conn_factory,
        agent_client_factory=_StubClient,
    )
    return TestClient(app)


def test_agent_endpoints_require_token(client: TestClient) -> None:
    assert client.post("/agent/conversations", json={}).status_code == 401
    assert client.get("/agent/conversations").status_code == 401


def test_start_conversation_and_send_message(client: TestClient) -> None:
    started = client.post(
        "/agent/conversations", json={"title": "strategy chat"}, headers=AUTH
    )
    assert started.status_code == 200
    cid = started.json()["conversation_id"]
    assert isinstance(cid, int)

    sent = client.post(
        f"/agent/conversations/{cid}/messages",
        json={"text": "what should I post today?"},
        headers=AUTH,
    )
    assert sent.status_code == 200
    turn = sent.json()
    assert turn["assistant_text"] == "stub assistant reply"
    assert turn["error"] is None
    assert turn["input_tokens"] == 12
    assert turn["output_tokens"] == 7


def test_messages_are_persisted_and_listable(client: TestClient) -> None:
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]
    client.post(
        f"/agent/conversations/{cid}/messages",
        json={"text": "hello"},
        headers=AUTH,
    )
    listed = client.get(f"/agent/conversations/{cid}/messages", headers=AUTH)
    assert listed.status_code == 200
    msgs = listed.json()["messages"]
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "assistant" in roles
    assert any(m["content"] == "stub assistant reply" for m in msgs)


def test_list_conversations(client: TestClient) -> None:
    client.post("/agent/conversations", json={"title": "one"}, headers=AUTH)
    client.post("/agent/conversations", json={"title": "two"}, headers=AUTH)
    convos = client.get("/agent/conversations", headers=AUTH).json()["conversations"]
    titles = {c.get("title") for c in convos}
    assert {"one", "two"} <= titles
