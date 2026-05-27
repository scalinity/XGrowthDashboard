"""Tests for Growth Agent autonomous operator tools (fetch_x_post, query_x_api, bash)."""

from __future__ import annotations

import json

from app import x_client
from app.agent import autonomy
from app.agent.tools import AGENT_TOOLS, get_tool


def _set_mode(db_conn, mode: str) -> None:
    db_conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'data_collection_mode'",
        (json.dumps(mode),),
    )
    db_conn.commit()


def test_parse_x_post_id_extracts_canonical_urls() -> None:
    assert (
        autonomy.parse_x_post_id("https://x.com/dannyscalant/status/1840000000")
        == "1840000000"
    )
    assert autonomy.parse_x_post_id("https://twitter.com/jack/status/2") == "2"


def test_parse_x_post_id_rejects_non_status_urls() -> None:
    assert autonomy.parse_x_post_id("https://x.com/dannyscalant") is None
    assert autonomy.parse_x_post_id("not a url") is None


def test_fetch_x_post_refuses_manual_mode(db_conn) -> None:
    _set_mode(db_conn, "manual")
    result = get_tool("fetch_x_post").handler(
        db_conn,
        url="https://x.com/example/status/1234567890",
    )
    assert result["status"] == "refused"
    assert result["reason"] == "data_collection_mode=manual"
    assert "paste" in result["fallback"].lower()


def test_fetch_x_post_rejects_invalid_url(db_conn) -> None:
    result = get_tool("fetch_x_post").handler(db_conn, url="https://example.com/post/1")
    assert result["status"] == "error"
    assert "canonical X status link" in result["error"]


def test_fetch_x_post_calls_x_api_get(monkeypatch, db_conn) -> None:
    seen: dict[str, object] = {}

    def fake_request(endpoint: str, **kwargs):
        seen["endpoint"] = endpoint
        seen["method"] = kwargs.get("method")
        seen["log_source"] = kwargs.get("log_source")
        return x_client.XApiResponse(
            status_code=200,
            body={
                "data": {
                    "id": "2059701677981413812",
                    "text": "Hello from ClaudeDevs",
                    "author_id": "999",
                    "created_at": "2026-05-27T12:00:00.000Z",
                    "conversation_id": "2059701677981413812",
                    "public_metrics": {
                        "like_count": 10,
                        "reply_count": 2,
                        "retweet_count": 1,
                        "quote_count": 0,
                    },
                },
                "includes": {
                    "users": [
                        {
                            "id": "999",
                            "username": "ClaudeDevs",
                            "name": "Claude Devs",
                            "public_metrics": {"followers_count": 1200},
                        }
                    ]
                },
            },
            raw_response_id=77,
            endpoint=endpoint,
            method="GET",
            elapsed_seconds=0.02,
        )

    monkeypatch.setattr(x_client, "request", fake_request)

    url = "https://x.com/ClaudeDevs/status/2059701677981413812"
    result = get_tool("fetch_x_post").handler(db_conn, url=url)

    assert result["status"] == "success"
    assert result["target_post_url"] == url
    assert result["x_post_id"] == "2059701677981413812"
    assert result["target_post_text"] == "Hello from ClaudeDevs"
    assert result["target_author_handle"] == "ClaudeDevs"
    assert result["like_count"] == 10
    assert seen["method"] == "GET"
    assert seen["log_source"] == "agent_fetch_x_post"
    assert "/2/tweets/2059701677981413812" in str(seen["endpoint"])


def test_fetch_x_post_surfaces_404(monkeypatch, db_conn) -> None:
    def fake_request(endpoint: str, **kwargs):  # noqa: ARG001
        raise x_client.XApiNotFound("post deleted")

    monkeypatch.setattr(x_client, "request", fake_request)

    result = get_tool("fetch_x_post").handler(
        db_conn,
        url="https://x.com/example/status/1234567890",
    )
    assert result["status"] == "error"
    assert result["status_code"] == 404
    assert result["reason"] == "target_deleted"


def test_query_x_api_refuses_manual_mode(db_conn) -> None:
    _set_mode(db_conn, "manual")
    result = get_tool("query_x_api").handler(db_conn, endpoint="/2/users/me")
    assert result["status"] == "refused"
    assert result["reason"] == "data_collection_mode=manual"


def test_query_x_api_rejects_non_v2_endpoint(db_conn) -> None:
    result = get_tool("query_x_api").handler(db_conn, endpoint="/1/statuses/show.json")
    assert result["status"] == "error"
    assert "starting with /2/" in result["error"]


def test_run_local_bash_refuses_env_dump(db_conn) -> None:
    result = get_tool("run_local_bash").handler(db_conn, command="env")
    assert result["status"] == "refused"
    assert "environment" in result["error"]


def test_run_local_bash_refuses_sudo(db_conn) -> None:
    result = get_tool("run_local_bash").handler(db_conn, command="sudo ls")
    assert result["status"] == "refused"
    assert "sudo" in result["error"]


def test_run_local_bash_refuses_env_file_access(db_conn) -> None:
    result = get_tool("run_local_bash").handler(db_conn, command="cat .env")
    assert result["status"] == "refused"


def test_run_local_bash_refuses_cwd_outside_project(db_conn) -> None:
    result = get_tool("run_local_bash").handler(
        db_conn,
        command="pwd",
        cwd="/tmp",
    )
    assert result["status"] == "error"
    assert "outside project root" in result["error"]


def test_fetch_x_post_is_registered_in_agent_tools() -> None:
    names = {tool.name for tool in AGENT_TOOLS}
    assert "fetch_x_post" in names
    assert "query_x_api" in names
    assert "run_local_bash" in names
