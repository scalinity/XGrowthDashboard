"""Phase 11.0 agent-endpoint tests for the FastAPI sidecar (§14.8, §31.3).

Uses a stub AgentClient (overrides _call_model) so the round trip is exercised
end-to-end through the service without touching the Anthropic API. Confirms the
endpoints reuse the existing client.send_message_sync persistence path.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

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


class _ToolUsingStubClient(AgentClient):
    """A deterministic client that asks for one tool, then summarizes it."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")
        self.calls = 0

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return (
                "I'll inspect the reply lane first.",
                [
                    {
                        "id": "toolu_dashboard_state",
                        "name": "query_dashboard_state",
                        "input": {"slice": "next_rep"},
                    }
                ],
                12,
                7,
            )
        return ("No queued reply targets are ready yet.", [], 8, 11)


class _StreamingStubClient(AgentClient):
    """A deterministic client that exposes token deltas for the SSE surface."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        return ("stub assistant reply", [], 12, 7)

    def _call_model_stream(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        yield ("text_delta", {"text": "stub assistant "})
        yield ("text_delta", {"text": "reply"})
        return ("stub assistant reply", [], 12, 7)


class _ToolStreamingStubClient(AgentClient):
    """A deterministic client that streams around a tool-use round."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")
        self.calls = 0

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return (
                "I'll inspect the reply lane first.",
                [
                    {
                        "id": "toolu_dashboard_state",
                        "name": "query_dashboard_state",
                        "input": {"slice": "next_rep"},
                    }
                ],
                12,
                7,
            )
        return ("No queued reply targets are ready yet.", [], 8, 11)

    def _call_model_stream(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            yield ("text_delta", {"text": "I'll inspect "})
            yield ("text_delta", {"text": "the reply lane first."})
            yield (
                "tool_call",
                {
                    "id": "toolu_dashboard_state",
                    "name": "query_dashboard_state",
                    "input": {"slice": "next_rep"},
                    "status": "requested",
                },
            )
            return (
                "I'll inspect the reply lane first.",
                [
                    {
                        "id": "toolu_dashboard_state",
                        "name": "query_dashboard_state",
                        "input": {"slice": "next_rep"},
                    }
                ],
                12,
                7,
            )
        yield ("text_delta", {"text": "No queued reply targets "})
        yield ("text_delta", {"text": "are ready yet."})
        return ("No queued reply targets are ready yet.", [], 8, 11)


class _MalformedToolStreamingStubClient(AgentClient):
    """A streaming path that emits a malformed tool-call shape on round one."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")
        self.calls = 0

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        # Not used by the stream code path, but kept for interface parity.
        self.calls += 1
        if self.calls == 1:
            return (
                "I'll recover from malformed calls.",
                [
                    {
                        "id": "toolu_invalid",
                        # name omitted on purpose — should become "invalid tool call".
                        "input": {},
                    }
                ],
                10,
                5,
            )
        return ("Done.", [], 8, 7)

    def _call_model_stream(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            yield ("text_delta", {"text": "I'll recover from "})
            yield ("tool_call", {"id": "toolu_invalid", "name": None, "input": []})
            return (
                "Recovered after malformed tool input.",
                [{"id": "toolu_invalid"}],
                12,
                7,
            )
        yield ("text_delta", {"text": "Done."})
        return ("Done.", [], 8, 7)


class _FetchXPostStreamingStubClient(AgentClient):
    """Streams a fetch_x_post round trip before the final assistant reply."""

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")
        self.calls = 0

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return (
                "I'll fetch the post first.",
                [
                    {
                        "id": "toolu_fetch_x_post",
                        "name": "fetch_x_post",
                        "input": {
                            "url": "https://x.com/ClaudeDevs/status/2059701677981413812",
                        },
                    }
                ],
                14,
                6,
            )
        return ("Draft reply ready after fetch.", [], 9, 12)

    def _call_model_stream(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            yield ("text_delta", {"text": "I'll fetch the post first."})
            yield (
                "tool_call",
                {
                    "id": "toolu_fetch_x_post",
                    "name": "fetch_x_post",
                    "input": {
                        "url": "https://x.com/ClaudeDevs/status/2059701677981413812",
                    },
                    "status": "requested",
                },
            )
            return (
                "I'll fetch the post first.",
                [
                    {
                        "id": "toolu_fetch_x_post",
                        "name": "fetch_x_post",
                        "input": {
                            "url": "https://x.com/ClaudeDevs/status/2059701677981413812",
                        },
                    }
                ],
                14,
                6,
            )
        yield ("text_delta", {"text": "Draft reply ready after fetch."})
        return ("Draft reply ready after fetch.", [], 9, 12)


class _ToolCatalogSpyClient(AgentClient):
    """Captures the Anthropic tool catalog passed on the first model call."""

    captured_tool_names: list[str] = []

    def __init__(self) -> None:
        super().__init__(api_key="stub-key")
        self.captured_tool_names = []

    def _call_model(self, conn, *, conversation_id):  # type: ignore[no-untyped-def]
        from app.agent import tools

        self.captured_tool_names = [t.name for t in tools.AGENT_TOOLS]
        return ("catalog captured", [], 3, 2)


def _build_client_with_agent(db_path: Path, agent_factory) -> TestClient:  # type: ignore[no-untyped-def]
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)  # cost-ceiling rows the send path reads
    conn.close()

    def conn_factory() -> sqlite3.Connection:
        return connect(db_path)

    app = create_app(
        token=TOKEN,
        conn_factory=conn_factory,
        agent_client_factory=agent_factory,
    )
    return TestClient(app)


def _build_client(db_path: Path) -> TestClient:
    return _build_client_with_agent(db_path, _StubClient)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return _build_client(tmp_path / "agent_svc.db")


def test_agent_endpoints_require_token(client: TestClient) -> None:
    assert client.post("/agent/conversations", json={}).status_code == 401
    assert client.get("/agent/conversations").status_code == 401
    assert client.delete("/agent/conversations/1").status_code == 401


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


def test_messages_endpoint_omits_internal_tool_result_rows(tmp_path: Path) -> None:
    client = _build_client_with_agent(tmp_path / "agent_svc_tools.db", _ToolUsingStubClient)
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]
    sent = client.post(
        f"/agent/conversations/{cid}/messages",
        json={"text": "Find reply opportunities"},
        headers=AUTH,
    )
    assert sent.status_code == 200
    assert sent.json()["assistant_text"] == "No queued reply targets are ready yet."

    listed = client.get(f"/agent/conversations/{cid}/messages", headers=AUTH)
    assert listed.status_code == 200
    msgs = listed.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    tool_message = next(m for m in msgs if m["tool_calls_json"])
    assert "query_dashboard_state" in tool_message["tool_calls_json"]
    assert all(m["role"] != "tool_result" for m in msgs)


def test_list_conversations(client: TestClient) -> None:
    client.post("/agent/conversations", json={"title": "one"}, headers=AUTH)
    client.post("/agent/conversations", json={"title": "two"}, headers=AUTH)
    convos = client.get("/agent/conversations", headers=AUTH).json()["conversations"]
    titles = {c.get("title") for c in convos}
    assert {"one", "two"} <= titles


def test_delete_conversation_removes_history_and_preserves_drafts(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_svc_delete.db"
    client = _build_client(db_path)
    cid = client.post(
        "/agent/conversations", json={"title": "old chat"}, headers=AUTH
    ).json()["conversation_id"]
    client.post(
        f"/agent/conversations/{cid}/messages",
        json={"text": "hello"},
        headers=AUTH,
    )

    conn = connect(db_path)
    try:
        draft_id = conn.execute(
            """
            INSERT INTO agent_drafts (conversation_id, draft_kind, text)
            VALUES (?, 'standalone', 'preserved draft')
            """,
            (cid,),
        ).lastrowid
    finally:
        conn.close()

    deleted = client.delete(f"/agent/conversations/{cid}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "conversation_id": cid}

    conn = connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_conversations WHERE id = ?", (cid,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE conversation_id = ?", (cid,)
        ).fetchone()[0] == 0
        draft = conn.execute(
            "SELECT conversation_id, text FROM agent_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        assert draft["conversation_id"] is None
        assert draft["text"] == "preserved draft"
    finally:
        conn.close()


def test_stream_endpoint_requires_token(client: TestClient) -> None:
    assert (
        client.post("/agent/conversations/1/stream", json={"text": "hi"}).status_code
        == 401
    )


def test_stream_message_emits_sse_events(client: TestClient) -> None:
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]
    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "what's next?"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "event: start" in text
    assert "event: assistant" in text
    assert "stub assistant reply" in text
    assert "event: done" in text


def test_stream_message_echoes_user_before_agent_work(client: TestClient) -> None:
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]
    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "Find reply opportunities"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    text = resp.text
    assert "event: user" in text
    assert "Find reply opportunities" in text
    assert text.index("event: user") < text.index("event: assistant")


def test_stream_message_emits_text_deltas_before_final_assistant(tmp_path: Path) -> None:
    client = _build_client_with_agent(tmp_path / "agent_svc_streaming.db", _StreamingStubClient)
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "stream this"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: text_delta" in text
    assert "stub assistant reply" in text
    assert text.index("event: text_delta") < text.index("event: assistant")


def test_stream_message_emits_tool_progress_before_final_summary(tmp_path: Path) -> None:
    client = _build_client_with_agent(
        tmp_path / "agent_svc_tool_streaming.db", _ToolStreamingStubClient
    )
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "Find reply opportunities"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: tool_call" in text
    assert "event: tool_result" in text
    assert "No queued reply targets are ready yet." in text
    assert text.index("event: tool_call") < text.index("event: tool_result")
    assert text.index("event: tool_result") < text.rindex("event: text_delta")
    assert text.rindex("event: text_delta") < text.index("event: done")


def test_stream_message_handles_empty_tool_calls_and_completes(tmp_path: Path) -> None:
    client = _build_client_with_agent(
        tmp_path / "agent_svc_tool_streaming_malformed.db",
        _MalformedToolStreamingStubClient,
    )
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "Try the broken tool"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: assistant" in text
    assert "event: tool_call" in text
    assert "event: tool_result" in text
    assert "event: done" in text
    assert "event: error" not in text
    assert "invalid tool call" in text


def test_stream_message_streams_coach_text_before_final_assistant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _FakeStream:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def __iter__(self):  # type: ignore[no-untyped-def]
            yield SimpleNamespace(type="text", text="coach says ")
            yield SimpleNamespace(type="text", text="move now")

        def get_final_message(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="coach says move now")],
                usage=SimpleNamespace(input_tokens=11, output_tokens=5),
            )

    class _FakeMessages:
        def create(self, **kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="coach says move now")],
                usage=SimpleNamespace(input_tokens=11, output_tokens=5),
            )

        def stream(self, **kwargs):  # type: ignore[no-untyped-def]
            assert "tools" not in kwargs
            return _FakeStream()

    class _FakeAnthropic:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.messages = _FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key")
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=_FakeAnthropic),
    )
    client = _build_client(tmp_path / "coach_streaming.db")
    cid = client.post(
        "/agent/conversations",
        json={"title": "Coach session", "context_seed": "coach"},
        headers=AUTH,
    ).json()["conversation_id"]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "What should I do next?"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: text_delta" in text
    assert "coach says move now" in text
    assert text.index("event: text_delta") < text.index("event: assistant")
    assert "event: done" in text

    listed = client.get(f"/agent/conversations/{cid}/messages", headers=AUTH)
    assert listed.status_code == 200
    messages = listed.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "coach says move now"
    assert messages[1]["input_tokens"] == 11
    assert messages[1]["output_tokens"] == 5


def test_coach_stream_leaves_no_orphan_user_row_on_api_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failed Coach API calls must not persist a user-only turn."""

    class _FailingMessages:
        def stream(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated outage")

    class _FailingAnthropic:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.messages = _FailingMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key")
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=_FailingAnthropic),
    )
    client = _build_client(tmp_path / "coach_api_failure.db")
    cid = client.post(
        "/agent/conversations",
        json={"title": "Coach session", "context_seed": "coach"},
        headers=AUTH,
    ).json()["conversation_id"]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={"text": "What should I do next?"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "simulated outage" in resp.text

    listed = client.get(f"/agent/conversations/{cid}/messages", headers=AUTH)
    assert listed.status_code == 200
    assert listed.json()["messages"] == []


def test_stream_message_emits_fetch_x_post_tool_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app import x_client

    def fake_request(endpoint: str, **kwargs):  # noqa: ARG001
        return x_client.XApiResponse(
            status_code=200,
            body={
                "data": {
                    "id": "2059701677981413812",
                    "text": "Ship day update from ClaudeDevs",
                    "author_id": "999",
                    "created_at": "2026-05-27T12:00:00.000Z",
                    "conversation_id": "2059701677981413812",
                    "public_metrics": {
                        "like_count": 4,
                        "reply_count": 1,
                        "retweet_count": 0,
                        "quote_count": 0,
                    },
                },
                "includes": {
                    "users": [
                        {
                            "id": "999",
                            "username": "ClaudeDevs",
                            "name": "Claude Devs",
                            "public_metrics": {"followers_count": 500},
                        }
                    ]
                },
            },
            raw_response_id=91,
            endpoint=endpoint,
            method="GET",
            elapsed_seconds=0.01,
        )

    monkeypatch.setattr(x_client, "request", fake_request)

    client = _build_client_with_agent(
        tmp_path / "agent_svc_fetch_x_post_stream.db",
        _FetchXPostStreamingStubClient,
    )
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]

    resp = client.post(
        f"/agent/conversations/{cid}/stream",
        json={
            "text": (
                "Write me a reply for https://x.com/ClaudeDevs/status/"
                "2059701677981413812"
            )
        },
        headers=AUTH,
    )

    assert resp.status_code == 200
    text = resp.text
    assert "event: tool_call" in text
    assert "fetch_x_post" in text
    assert "event: tool_result" in text
    assert "Ship day update from ClaudeDevs" in text
    assert "Draft reply ready after fetch." in text
    assert text.index("event: tool_call") < text.index("event: tool_result")
    assert text.index("event: tool_result") < text.index("event: done")


def test_agent_chat_registry_includes_operator_tools(tmp_path: Path) -> None:
    spy = _ToolCatalogSpyClient()
    client = _build_client_with_agent(
        tmp_path / "agent_svc_tool_catalog.db", lambda: spy
    )
    cid = client.post("/agent/conversations", json={}, headers=AUTH).json()[
        "conversation_id"
    ]
    sent = client.post(
        f"/agent/conversations/{cid}/messages",
        json={"text": "hello"},
        headers=AUTH,
    )
    assert sent.status_code == 200
    assert "fetch_x_post" in spy.captured_tool_names
    assert "query_x_api" in spy.captured_tool_names
    assert "run_local_bash" in spy.captured_tool_names
