"""Single-source audit logging with raw-token redaction (§28.2 rule #11).

Every write to ``agent_tool_calls`` flows through ``log_tool_call``. For
tool names in ``PUBLISH_TOOL_NAMES`` the raw ``confirmation_token`` is
stripped from the arguments dict and replaced with a
``confirmation_token_id`` FK reference BEFORE the row is inserted.
``redacted_arguments = 1`` marks the row so audit reviewers can filter on
it.

The redaction is centralized here (not in tool handlers, not in the SDK
adapter, not in the publish module) so any new publish-style tool only
needs to add its name to ``PUBLISH_TOOL_NAMES`` to inherit the same
redaction policy.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

# Names of tools whose `arguments_json` MUST have the raw confirmation_token
# stripped before audit insert. Keep in sync with INTERNAL_TOOLS — the
# test `test_publish_tool_names_match_internal_tools` enforces equality.
PUBLISH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "publish_post_to_x",
        "publish_reply_to_x",
    }
)


def _redact_publish_args(
    args: Mapping[str, Any], confirmation_token_id: int | None
) -> tuple[dict[str, Any], bool]:
    """Strip raw token from publish-tool args; return (redacted_args, was_redacted)."""
    out = dict(args)
    redacted = False
    if "confirmation_token" in out:
        del out["confirmation_token"]
        redacted = True
    if confirmation_token_id is not None:
        out["confirmation_token_id"] = confirmation_token_id
    return out, redacted


def log_tool_call(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    tool_name: str,
    arguments: Mapping[str, Any],
    status: str,
    result: Any | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    confirmation_token_id: int | None = None,
    cost_input_tokens: int | None = None,
    cost_output_tokens: int | None = None,
    cost_usd: float | None = None,
    notes: str | None = None,
) -> int:
    """Insert an ``agent_tool_calls`` row. Returns the new row id.

    For publish tools, ``arguments['confirmation_token']`` is dropped and
    replaced with ``confirmation_token_id`` (provided by the caller after
    a successful token mint or validation). ``redacted_arguments`` is set
    to 1 when redaction occurred.

    The caller MUST pass ``confirmation_token_id`` for publish tools even
    on the error path — otherwise the audit row will have neither the raw
    token nor the FK back to it, defeating the audit purpose.
    """
    if tool_name in PUBLISH_TOOL_NAMES:
        redacted_args, was_redacted = _redact_publish_args(arguments, confirmation_token_id)
        redacted_flag = 1 if was_redacted else 0
    else:
        redacted_args = dict(arguments)
        redacted_flag = 0

    cur = conn.execute(
        """
        INSERT INTO agent_tool_calls (
            message_id, tool_name, arguments_json, redacted_arguments,
            result_json, status, error_message, duration_ms,
            cost_input_tokens, cost_output_tokens, cost_usd, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            tool_name,
            json.dumps(redacted_args, default=str),
            redacted_flag,
            None if result is None else json.dumps(result, default=str),
            status,
            error_message,
            duration_ms,
            cost_input_tokens,
            cost_output_tokens,
            cost_usd,
            notes,
        ),
    )
    return int(cur.lastrowid)
