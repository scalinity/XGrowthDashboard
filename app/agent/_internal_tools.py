"""INTERNAL_TOOLS — publish entry points the agent CANNOT see (§28.4 #10, #11).

This module is the **only** legitimate import site for the publish
callables. ``app/agent/client.py`` and ``app/agent/tools.py`` MUST NOT
import from here — the startup assertion in ``app/main.py`` proves
``AGENT_TOOLS`` and ``INTERNAL_TOOLS`` are disjoint by tool name.

The tools are exposed as direct Python callables, not as JSON-schema
tool specs. The Anthropic SDK ``messages.create(tools=...)`` payload
that goes to the model is built from ``AGENT_TOOLS`` only; these
functions are never serialized into the model's tool catalog. The model
literally cannot attempt to call them — there's no schema slot to
populate (§28.4 internal-only tool surface note).

The Streamlit click-handler in §14.8 Agent Chat is the sole external
caller. It mints a token via ``confirmation.mint_confirmation_token``
and passes the raw UUID synchronously into one of these functions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from app.agent import publish


@dataclass(frozen=True)
class InternalToolDef:
    """Pairs the publish-callable's name with its Python function.

    Distinct from ``app.agent.tools.ToolDef`` so the agent SDK adapter has
    no path to accidentally serialize one of these into the model's tool
    catalog: the adapter type-checks against ``ToolDef``, which carries
    ``input_schema``; ``InternalToolDef`` does not.
    """

    name: str
    handler: Callable[..., Any]


def publish_post_to_x(
    conn: sqlite3.Connection,
    post_id: int,
    confirmation_token: str,
    *,
    message_id: int | None = None,
) -> publish.PublishResult:
    """Invoke the atomic publish transaction for a standalone post (§28.4 #10).

    Only called by the Streamlit click-handler. MVP variant returns a
    ``PublishResult`` with ``method='manual_clipboard'`` and the intent
    URL the UI opens for Daniel to complete the manual post.
    """
    return publish.publish_post_atomic(
        conn,
        post_id=post_id,
        raw_token=confirmation_token,
        message_id=message_id,
        tool_name="publish_post_to_x",
    )


def publish_reply_to_x(
    conn: sqlite3.Connection,
    post_id: int,
    confirmation_token: str,
    *,
    message_id: int | None = None,
) -> publish.PublishResult:
    """Invoke the atomic publish transaction for a reply (§28.4 #11).

    Same contract as ``publish_post_to_x``. The intent URL preserves
    ``in_reply_to`` so the resulting X post is a real reply, not a
    standalone post.
    """
    return publish.publish_post_atomic(
        conn,
        post_id=post_id,
        raw_token=confirmation_token,
        message_id=message_id,
        tool_name="publish_reply_to_x",
    )


# The registry the startup assertion compares against AGENT_TOOLS.
INTERNAL_TOOLS: list[InternalToolDef] = [
    InternalToolDef(name="publish_post_to_x", handler=publish_post_to_x),
    InternalToolDef(name="publish_reply_to_x", handler=publish_reply_to_x),
]
