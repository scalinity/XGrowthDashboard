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

Atomicity (§28.10 step 6): the whole flow runs inside ``db.transaction``
(BEGIN IMMEDIATE / COMMIT / ROLLBACK). Failure paths:

* Validation failure (any of the six checks): ROLLBACK → token stays
  unconsumed. A second narrower transaction bumps publish_attempt_count
  and writes the audit row. Daniel can retry within the TTL.
* Post-validation runtime failure: ROLLBACK → token consumption is
  rolled back. A second transaction RE-MARKS ``consumed_at_utc`` so
  retry abuse is prevented (§28.10 atomicity rule), sets
  ``publish_method='failed'``, and writes the audit row.

Length cap (§28.10): drafts > 280 chars (``x_post_max_chars``) are
refused inside the publish transaction with ``DraftTooLongError``. The
modal also disables the confirm button when char_count > 280; this is
the server-side belt to the modal's suspenders.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agent import audit, confirmation
from app.db import transaction

X_POST_MAX_CHARS: int = 280


class DraftTooLongError(RuntimeError):
    """Draft text exceeds ``X_POST_MAX_CHARS``. Refused before any state writes."""


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


def _ensure_audit_anchor_message(conn: sqlite3.Connection) -> int:
    """Return a message_id to anchor a publish audit row to.

    When the click-handler invokes publish without a live conversation
    (e.g. orphan reconciliation or click from a non-chat surface), we
    anchor to the single dedicated ``[publish-flow audit anchor]``
    conversation. Only ONE such conversation is ever created; subsequent
    anchor-less publishes append messages to it. This prevents the
    accumulation of synthetic conversations across retries (W9).
    """
    anchor_row = conn.execute(
        """
        SELECT id FROM agent_conversations
        WHERE title = '[publish-flow audit anchor]' AND status = 'archived'
        ORDER BY id ASC LIMIT 1
        """,
    ).fetchone()
    if anchor_row is None:
        cur = conn.execute(
            """
            INSERT INTO agent_conversations (title, status, model_default)
            VALUES ('[publish-flow audit anchor]', 'archived', NULL)
            """,
        )
        conv_id = int(cur.lastrowid)
    else:
        conv_id = int(anchor_row["id"])
    cur = conn.execute(
        """
        INSERT INTO agent_messages (conversation_id, role, content)
        VALUES (?, 'system', '[publish click-handler — no live conversation]')
        """,
        (conv_id,),
    )
    return int(cur.lastrowid)


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
    # message_id anchoring (whether to a live conversation or a synthetic
    # audit anchor) happens OUTSIDE the publish transaction by design —
    # we want one anchor row regardless of how the publish resolves.
    if message_id is None:
        message_id = _ensure_audit_anchor_message(conn)

    arguments = {"post_id": post_id, "confirmation_token": raw_token}
    consumed: confirmation.ConsumedToken | None = None

    try:
        with transaction(conn):
            # Length cap — server-side belt to the modal's suspenders.
            row = conn.execute(
                "SELECT text, type, in_reply_to_post_id FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if row is None:
                raise DraftTooLongError(
                    f"post_id={post_id} not found (cannot validate length)"
                )
            post_text = row["text"]
            if len(post_text) > X_POST_MAX_CHARS:
                raise DraftTooLongError(
                    f"draft is {len(post_text)} chars; X cap is {X_POST_MAX_CHARS}"
                )

            consumed = confirmation.validate_and_consume_token(
                conn, post_id=post_id, raw_token=raw_token
            )

            post_type = row["type"]
            in_reply_to = row["in_reply_to_post_id"]
            intent_url = _build_intent_url(
                post_text,
                in_reply_to_post_id=in_reply_to if post_type == "reply" else None,
            )

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

    except confirmation.ConfirmationTokenError as exc:
        # Validation failure path (§28.10 step 6): the main transaction
        # rolled back, so the token UPDATE never landed → token stays
        # unconsumed and Daniel can retry within the TTL. We still need to
        # bump the attempt counter and write the audit row — in a separate
        # narrower transaction so those writes commit even though the
        # validation phase aborted.
        with transaction(conn):
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

    except DraftTooLongError as exc:
        # Length-cap failure: token was never validated (we raise before
        # validate_and_consume_token), so the token stays unconsumed.
        with transaction(conn):
            conn.execute(
                """
                UPDATE posts
                SET publish_attempt_count = publish_attempt_count + 1,
                    publish_last_error = ?
                WHERE id = ?
                """,
                (f"DraftTooLongError: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"DraftTooLongError: {exc}",
                confirmation_token_id=None,
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"DraftTooLongError: {exc}",
        )

    except Exception as exc:
        # Post-validation runtime failure: the main transaction rolled
        # back, so the token-consume UPDATE was undone. §28.10 atomicity
        # rule: the token MUST stay consumed to prevent retry abuse on
        # a partially-applied publish. Re-mark consumed_at_utc + write
        # the failure state in a fresh transaction.
        with transaction(conn):
            if consumed is not None:
                conn.execute(
                    "UPDATE publish_confirmation_tokens SET consumed_at_utc = ? WHERE id = ?",
                    (_utcnow_iso(), consumed.token_id),
                )
            conn.execute(
                """
                UPDATE posts
                SET publish_method = 'failed',
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
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
                confirmation_token_id=consumed.token_id if consumed else None,
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
