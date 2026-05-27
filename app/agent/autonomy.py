from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from app import x_client
from app.db import PROJECT_ROOT

_MAX_OUTPUT_CHARS = 12_000
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 120.0

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
