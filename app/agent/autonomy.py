from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from app import x_client
from app.db import PROJECT_ROOT
from app.forms import get_setting

_MAX_OUTPUT_CHARS = 12_000
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 120.0

_X_STATUS_URL_RE = re.compile(r"(?:x|twitter)\.com/[^/]+/status/(\d+)")

_BLOCKED_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[;&|]\s*)sudo\b"), "sudo is outside the local agent tool boundary"),
    (re.compile(r"\brm\s+-[^\n;|&]*r[^\n;|&]*f\s+(/|~|\$HOME|\*)"), "recursive force deletion is blocked"),
    (re.compile(r"\b(chmod|chown)\s+-R\b"), "recursive ownership/permission changes are blocked"),
    (re.compile(r"\b(diskutil\s+erase|mkfs|dd\s+if=|shutdown|reboot)\b"), "machine-level destructive commands are blocked"),
    (re.compile(r"\b(printenv|env)\b"), "dumping the process environment may expose secrets"),
    (re.compile(r"\.env(?:\.|\b)"), "commands that read or write env files are blocked"),
)

_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{16,})"
)


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text, False
    return text[:_MAX_OUTPUT_CHARS] + "\n[truncated]", True


def _redact(text: str) -> str:
    return _SECRET_VALUE_RE.sub("[REDACTED_SECRET]", text)


def _resolve_workdir(cwd: str | None) -> Path:
    root = PROJECT_ROOT.resolve()
    if not cwd or cwd == ".":
        return root
    candidate = Path(cwd).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"cwd {str(resolved)!r} is outside project root {str(root)!r}"
        )
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"cwd {str(resolved)!r} is not an existing directory")
    return resolved


def _blocked_reason(command: str) -> str | None:
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def parse_x_post_id(url: str) -> str | None:
    """Pull the numeric post id from an X status URL.

    Accepts canonical ``https://x.com/{handle}/status/{id}`` (and
    ``twitter.com`` aliases). Returns ``None`` on no match.
    """
    if not url:
        return None
    match = _X_STATUS_URL_RE.search(url.strip())
    return match.group(1) if match else None


def _read_data_collection_mode(conn: sqlite3.Connection) -> str:
    mode = get_setting(conn, "data_collection_mode", "api")
    if isinstance(mode, str):
        return mode.strip().lower() or "api"
    return "api"


def _manual_mode_refusal(*, tool_name: str, detail: str | None = None) -> dict[str, Any]:
    message = (
        "data_collection_mode=manual — X API reads are disabled. "
        "Ask Daniel to paste the post text, or switch Settings → "
        "data_collection_mode to 'api' when xurl is configured."
    )
    if detail:
        message = f"{message} ({detail})"
    return {
        "status": "refused",
        "error": message,
        "reason": "data_collection_mode=manual",
        "tool": tool_name,
        "fallback": "paste target_post_text manually",
    }


def _tweet_fetch_endpoint(x_post_id: str) -> str:
    return (
        f"/2/tweets/{x_post_id}"
        f"?tweet.fields=public_metrics,non_public_metrics,created_at,author_id,conversation_id"
        f"&expansions=author_id"
        f"&user.fields=public_metrics,username,name"
    )


def _normalize_tweet_payload(
    *,
    target_post_url: str,
    x_post_id: str,
    body: dict[str, Any],
    raw_response_id: int | None,
    endpoint: str,
    status_code: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    data = body.get("data")
    if not isinstance(data, dict):
        return {
            "status": "error",
            "target_post_url": target_post_url,
            "x_post_id": x_post_id,
            "error": "X API response missing 'data' object",
            "status_code": status_code,
            "endpoint": endpoint,
        }

    author: dict[str, Any] | None = None
    includes = body.get("includes") or {}
    if isinstance(includes, dict):
        users = includes.get("users")
        if isinstance(users, list):
            tweet_author_id = data.get("author_id")
            for user in users:
                if isinstance(user, dict) and (
                    tweet_author_id is None or user.get("id") == tweet_author_id
                ):
                    author = user
                    break

    public_metrics = data.get("public_metrics") if isinstance(data.get("public_metrics"), dict) else {}
    non_public_metrics = (
        data.get("non_public_metrics")
        if isinstance(data.get("non_public_metrics"), dict)
        else {}
    )
    author_public = author.get("public_metrics") if isinstance(author, dict) and isinstance(author.get("public_metrics"), dict) else {}

    handle = None
    display_name = None
    if isinstance(author, dict):
        username = author.get("username")
        if isinstance(username, str) and username.strip():
            handle = username.strip().lstrip("@")
        name = author.get("name")
        if isinstance(name, str) and name.strip():
            display_name = name.strip()

    return {
        "status": "success",
        "target_post_url": target_post_url,
        "x_post_id": x_post_id,
        "target_post_text": data.get("text") or "",
        "target_author_handle": handle,
        "target_author_display_name": display_name,
        "target_author_follower_count": author_public.get("followers_count"),
        "like_count": public_metrics.get("like_count"),
        "reply_count": public_metrics.get("reply_count"),
        "repost_count": public_metrics.get("retweet_count"),
        "quote_count": public_metrics.get("quote_count"),
        "impression_count": non_public_metrics.get("impression_count"),
        "created_at": data.get("created_at"),
        "conversation_id": data.get("conversation_id"),
        "author_id": data.get("author_id"),
        "endpoint": endpoint,
        "status_code": status_code,
        "raw_response_id": raw_response_id,
        "elapsed_seconds": elapsed_seconds,
    }


def fetch_x_post_by_url(
    conn: sqlite3.Connection,
    *,
    url: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Resolve a pasted X status URL via read-only X API v2 (xurl).

    Never scrapes the web page — only parses the status id and calls
    ``GET /2/tweets/{id}`` through the existing audited xurl wrapper.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        return {"status": "error", "error": "url is required"}

    if _read_data_collection_mode(conn) == "manual":
        return _manual_mode_refusal(tool_name="fetch_x_post")

    x_post_id = parse_x_post_id(clean_url)
    if x_post_id is None:
        return {
            "status": "error",
            "target_post_url": clean_url,
            "error": (
                "url must be a canonical X status link "
                "(https://x.com/{handle}/status/{id} or twitter.com alias)"
            ),
        }

    request_timeout = (
        _DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    )
    request_timeout = max(1.0, min(request_timeout, _MAX_TIMEOUT_SECONDS))
    endpoint = _tweet_fetch_endpoint(x_post_id)

    try:
        response = x_client.request(
            endpoint,
            method="GET",
            conn=conn,
            timeout_seconds=request_timeout,
            log_source="agent_fetch_x_post",
            log_notes=f"Growth Agent fetch_x_post url={clean_url!r}",
        )
    except x_client.XApiNotFound as exc:
        return {
            "status": "error",
            "target_post_url": clean_url,
            "x_post_id": x_post_id,
            "error": str(exc),
            "status_code": 404,
            "reason": "target_deleted",
            "fallback": "paste target_post_text manually if Daniel still has it",
        }
    except x_client.XApiRateLimited as exc:
        return {
            "status": "error",
            "target_post_url": clean_url,
            "x_post_id": x_post_id,
            "error": str(exc),
            "status_code": 429,
            "retry_after_seconds": exc.retry_after_seconds,
            "fallback": "retry after rate limit resets or paste target_post_text manually",
        }
    except x_client.XApiUnavailable as exc:
        return {
            "status": "error",
            "target_post_url": clean_url,
            "x_post_id": x_post_id,
            "error": str(exc),
            "status_code": exc.status_code,
            "fallback": "configure xurl auth or paste target_post_text manually",
        }
    except x_client.XApiError as exc:
        return {
            "status": "error",
            "target_post_url": clean_url,
            "x_post_id": x_post_id,
            "error": str(exc),
            "status_code": exc.status_code,
            "fallback": "paste target_post_text manually",
        }

    body = response.body
    if not isinstance(body, dict):
        return {
            "status": "error",
            "target_post_url": clean_url,
            "x_post_id": x_post_id,
            "error": f"unexpected X API body shape: {type(body).__name__}",
            "status_code": response.status_code,
            "endpoint": endpoint,
        }

    return _normalize_tweet_payload(
        target_post_url=clean_url,
        x_post_id=x_post_id,
        body=body,
        raw_response_id=response.raw_response_id,
        endpoint=response.endpoint,
        status_code=response.status_code,
        elapsed_seconds=response.elapsed_seconds,
    )


def run_bash_command(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Run a local project-scoped bash command for the agent.

    This is intentionally autonomous: it does not prompt Daniel for each
    command. The boundary is instead structural: cwd must stay inside the
    project, runtime is capped, output is captured/redacted, and obviously
    destructive or secret-dumping commands are refused before execution.
    """
    clean_command = (command or "").strip()
    if not clean_command:
        return {"status": "error", "error": "command is required"}

    reason = _blocked_reason(clean_command)
    if reason is not None:
        return {
            "status": "refused",
            "error": reason,
            "command": clean_command,
            "purpose": purpose,
        }

    try:
        workdir = _resolve_workdir(cwd)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "command": clean_command}

    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT_SECONDS
    timeout = max(1.0, min(timeout, _MAX_TIMEOUT_SECONDS))

    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", clean_command],
            cwd=str(workdir),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _redact(exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = _redact(exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stdout, stdout_truncated = _truncate(stdout)
        stderr, stderr_truncated = _truncate(stderr)
        return {
            "status": "timeout",
            "exit_code": None,
            "command": clean_command,
            "cwd": str(workdir),
            "timeout_seconds": timeout,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
        }

    stdout, stdout_truncated = _truncate(_redact(proc.stdout or ""))
    stderr, stderr_truncated = _truncate(_redact(proc.stderr or ""))
    return {
        "status": "success" if proc.returncode == 0 else "error",
        "exit_code": int(proc.returncode),
        "command": clean_command,
        "cwd": str(workdir),
        "timeout_seconds": timeout,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
        "purpose": purpose,
    }


def query_x_api_get(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run a model-visible, read-only X API request through xurl.

    Live posting/replying remains internal-only and confirmation-gated. This
    helper gives the agent real X read access without exposing POST /2/tweets.
    """
    if _read_data_collection_mode(conn) == "manual":
        return _manual_mode_refusal(tool_name="query_x_api")

    clean_endpoint = (endpoint or "").strip()
    if not clean_endpoint.startswith("/2/"):
        return {
            "status": "error",
            "error": "endpoint must be an X API v2 path starting with /2/",
            "endpoint": clean_endpoint,
        }

    request_timeout = (
        _DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    )
    request_timeout = max(1.0, min(request_timeout, _MAX_TIMEOUT_SECONDS))

    try:
        response = x_client.request(
            clean_endpoint,
            method="GET",
            conn=conn,
            timeout_seconds=request_timeout,
            log_source="agent_x_api_read",
            log_notes="Growth Agent read-only X API tool",
        )
    except x_client.XApiError as exc:
        return {
            "status": "error",
            "endpoint": clean_endpoint,
            "error": str(exc),
            "status_code": exc.status_code,
        }

    return {
        "status": "success",
        "endpoint": response.endpoint,
        "status_code": response.status_code,
        "body": response.body,
        "raw_response_id": response.raw_response_id,
        "elapsed_seconds": response.elapsed_seconds,
    }
