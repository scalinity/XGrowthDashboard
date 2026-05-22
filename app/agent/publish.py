"""Atomic publish transaction — manual-clipboard MVP (§28.10).

The publish flow is invoked ONLY by the Streamlit click-handler after
Daniel confirms in the modal. The click-handler does:

1. ``mint_confirmation_token(conn, post_id, draft_text)`` → MintedToken.
2. Synchronous call into ``_internal_tools.publish_post_to_x(post_id, raw_token)``,
   which delegates to ``publish_post_atomic`` here.

The raw UUID lives only on the click-handler's local stack frame; once
``publish_post_atomic`` returns, the raw value is dropped. Audit logging
in this module redacts the token via ``app.agent.audit.log_tool_call``,
which substitutes the ``publish_confirmation_tokens.id`` for the raw
value before insert.

MVP behavior: the manual-clipboard variant. The atomic transaction
validates the token, marks it consumed, sets ``posts.publish_method =
'manual_clipboard'``, ``published_to_x_at = now()``, and increments
``publish_attempt_count``. No X API call yet — the click-handler opens
``intent_url`` for Daniel and the existing Phase 2 "Mark posted" form
captures the resulting ``x_post_id``. V1.2 replaces the manual branch
with a direct ``POST /2/tweets`` call and lifts ``publish_method`` to
``'agent_confirmed'``.

Failure handling: any of the six-check exceptions → ROLLBACK any state
written so far, increment ``publish_attempt_count``, set
``publish_last_error``, log the audit row with ``status='error'`` and
the typed exception class name. Token consumption is NOT rolled back on
post-validation errors (the token stays unconsumed so Daniel can retry
within the TTL) — this matches §28.10 step 6.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agent import audit, confirmation


@dataclass(frozen=True)
class PublishResult:
    """Outcome surfaced back to the click-handler for UI rendering."""

    success: bool
    post_id: int
    method: str  # 'manual_clipboard' | 'agent_confirmed' | 'failed'
    intent_url: str | None = None  # X compose-tweet URL for manual variant
    x_post_id: str | None = None
    error: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _build_intent_url(text: str, in_reply_to_post_id: str | None = None) -> str:
    """Return the X compose-tweet URL for the manual-clipboard MVP flow.

    https://twitter.com/intent/tweet?text=...&in_reply_to=...
    """
    params = {"text": text}
    if in_reply_to_post_id:
        params["in_reply_to"] = in_reply_to_post_id
    return "https://twitter.com/intent/tweet?" + urllib.parse.urlencode(params)


def publish_post_atomic(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    raw_token: str,
    message_id: int | None = None,
    tool_name: str = "publish_post_to_x",
) -> PublishResult:
    """Validate token, consume it, write publish state — all in one transaction.

    The MVP manual-clipboard variant does NOT call the X API; it stages
    state for Daniel to paste the URL via the existing "Mark posted" form.
    The same transaction structure remains in place for V1.2 when the X
    API write replaces the manual branch.
    """
    # The audit log MUST always record this attempt, success OR failure.
    # If message_id is None (no live agent conversation), insert a synthetic
    # system message so the FK is satisfied. The click-handler in Session 2
    # will pass a real message_id from the agent_messages row that staged
    # the draft.
    if message_id is None:
        cur = conn.execute(
            """
            INSERT INTO agent_messages (conversation_id, role, content)
            SELECT id, 'system', '[publish click-handler — no live conversation]'
            FROM agent_conversations ORDER BY id DESC LIMIT 1
            """,
        )
        if cur.rowcount == 0:
            # No conversations exist yet — create a synthetic one so audit
            # has somewhere to anchor. Real flows always carry message_id.
            conv_id = int(
                conn.execute(
                    """
                    INSERT INTO agent_conversations (title, status, model_default)
                    VALUES ('[publish-flow audit anchor]', 'archived', NULL)
                    """,
                ).lastrowid
            )
            cur = conn.execute(
                """
                INSERT INTO agent_messages (conversation_id, role, content)
                VALUES (?, 'system', '[publish click-handler — no live conversation]')
                """,
                (conv_id,),
            )
        message_id = int(cur.lastrowid)

    arguments = {"post_id": post_id, "confirmation_token": raw_token}

    # Phase 1 of the transaction: validate + consume. Failures here mean
    # the token MAY stay unconsumed (per §28.10 step 6) — the validation
    # chain only marks consumed_at_utc when ALL six checks pass.
    try:
        consumed = confirmation.validate_and_consume_token(
            conn, post_id=post_id, raw_token=raw_token
        )
    except confirmation.ConfirmationTokenError as exc:
        # Increment attempt counter, write last_error, log audit row.
        # Note: we still pass confirmation_token_id=None here because the
        # token was never validated; the raw token is stripped via the
        # redaction path regardless.
        conn.execute(
            """
            UPDATE posts
            SET publish_attempt_count = publish_attempt_count + 1,
                publish_last_error = ?
            WHERE id = ?
            """,
            (f"{type(exc).__name__}: {exc}", post_id),
        )
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=f"{type(exc).__name__}: {exc}",
            confirmation_token_id=None,
        )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Phase 2: write publish state. From here, any DB error must NOT roll
    # back the token consumption (§28.10 step 6 — the token stays consumed
    # to prevent retry abuse; the row is marked failed for reconciliation).
    try:
        post_text_row = conn.execute(
            "SELECT text, type, in_reply_to_post_id FROM posts WHERE id = ?",
            (post_id,),
        ).fetchone()
        post_text = post_text_row["text"]
        post_type = post_text_row["type"]
        in_reply_to = post_text_row["in_reply_to_post_id"]

        intent_url = _build_intent_url(
            post_text,
            in_reply_to_post_id=in_reply_to if post_type == "reply" else None,
        )

        # Manual-clipboard MVP: publish_method='manual_clipboard'. The
        # post stays at manual_confirmation_status='draft' until Daniel
        # pastes the URL via the existing Mark posted form — at which
        # point status becomes 'confirmed' and x_post_id is populated.
        # The token has already been consumed; the post row is staged.
        conn.execute(
            """
            UPDATE posts
            SET publish_method = 'manual_clipboard',
                published_to_x_at = ?,
                publish_attempt_count = publish_attempt_count + 1,
                publish_last_error = NULL,
                published_via_agent_message_id = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), message_id, post_id),
        )

        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="success",
            result={"method": "manual_clipboard", "intent_url": intent_url},
            confirmation_token_id=consumed.token_id,
        )

        return PublishResult(
            success=True,
            post_id=post_id,
            method="manual_clipboard",
            intent_url=intent_url,
        )

    except Exception as exc:
        # Post-validation failure: token stays consumed, row marked failed.
        conn.execute(
            """
            UPDATE posts
            SET publish_method = 'failed',
                publish_last_error = ?
            WHERE id = ?
            """,
            (f"{type(exc).__name__}: {exc}", post_id),
        )
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=f"{type(exc).__name__}: {exc}",
            confirmation_token_id=consumed.token_id,
        )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
