"""Atomic publish transaction — §28.10 with Phase 8 API-vs-manual branch.

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

Phase 5.5 shipped the manual-clipboard branch only. Phase 8 (migration
019) adds the API branch alongside it, gated by the
``publish_via_api_enabled`` settings row (default TRUE):

* ``publish_via_api_enabled = TRUE`` (default) — call
  ``app.x_client.publish_post_to_x_via_api()`` inside the same atomic
  transaction. On success, set ``posts.publish_method='agent_confirmed'``
  and ``posts.x_post_id`` from the X API response. On the four typed
  failure modes (429 / 403 / 5xx / timeout) each maps to a specific
  token-consumed-or-not outcome per §22 + §29.11.
* ``publish_via_api_enabled = FALSE`` — take the manual-clipboard
  branch end-to-end. No X API call fires. ``posts.publish_method``
  stays ``'manual_clipboard'`` and the click-handler opens the intent
  URL for Daniel to complete the post manually.

Both branches share the same six-check + atomic-transaction wrapper.
Only the X API call inside it differs. §29.1 "Manual workflows remain
inviolable as Settings-selectable fallbacks forever" — the manual
branch is not deprecated and is exercised by a dedicated test.

Token-consumed matrix (§22 + §29.11):

* 429 (X API rate limit, no X-side state change): token UN-consumed.
* 403 (X cold-reply, X considers it a real attempt): token CONSUMED,
  no posts row created.
* 5xx after retry exhaustion: token CONSUMED, ROLLBACK, crash-recovery
  reconciles via api_get_recent_tweets on next boot.
* Timeout mid-call (X may have processed it): token CONSUMED, ROLLBACK,
  crash-recovery reconciles. Never retried — risk of duplicate post.

Length cap (§28.10): drafts > 280 chars (``x_post_max_chars``) are
refused inside the publish transaction with ``DraftTooLongError``. The
modal also disables the confirm button when char_count > 280; this is
the server-side belt to the modal's suspenders.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from app import x_client
from app.agent import audit, audit_log, confirmation
from app.db import transaction

X_POST_MAX_CHARS: int = 280


class DraftTooLongError(RuntimeError):
    """Draft text exceeds ``X_POST_MAX_CHARS``. Refused before any state writes."""


class RateLimitRefusalError(RuntimeError):
    """Phase 8: write-rate sliding window refused this publish attempt.

    Raised INSIDE the atomic transaction by the API branch before any
    state writes. ROLLBACK leaves the confirmation token UN-consumed
    (no X-side state change occurred); Daniel retries after the window
    rolls over.
    """

    def __init__(self, reason: str, *, reset_at_iso: str | None = None) -> None:
        super().__init__(reason)
        self.reset_at_iso = reset_at_iso


@dataclass(frozen=True)
class PublishResult:
    """Outcome surfaced back to the click-handler for UI rendering."""

    success: bool
    post_id: int
    method: str  # 'manual_clipboard' | 'agent_confirmed' | 'failed'
    intent_url: str | None = None  # X compose-tweet URL for manual variant
    x_post_id: str | None = None
    error: str | None = None
    error_kind: str | None = None  # 'rate_limited' | 'cold_reply' | 'server_error' | 'timeout' | 'confirmation' | 'length' | 'runtime'


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


def _read_publish_via_api_enabled(conn: sqlite3.Connection) -> bool:
    """Phase 8 gate: read the ``publish_via_api_enabled`` settings row.

    Defaults to TRUE on parse failure / missing row — matches migration
    019's seeded default and §28.10 Phase 5.5 → Phase 8 transition. The
    manual-clipboard branch is the FALSE path; tests that exercise it
    set the row to FALSE explicitly.
    """
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = ?",
            ("publish_via_api_enabled",),
        ).fetchone()
    except sqlite3.OperationalError:
        return True
    if row is None or row["value_json"] is None:
        return True
    try:
        parsed = json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        return True
    return bool(parsed)


def _resolve_reply_target_x_post_id(
    conn: sqlite3.Connection, post_row: sqlite3.Row
) -> str | None:
    """For reply-type posts, look up the target's X post id.

    The X API v2 reply shape needs the *target's x_post_id*, not the
    in-DB `posts.id` of the local row representing the target. We look
    it up via the in_reply_to_post_id FK. NULL → standalone post.
    """
    if post_row["type"] != "reply":
        return None
    in_reply_to_post_id = post_row["in_reply_to_post_id"]
    if in_reply_to_post_id is None:
        return None
    target = conn.execute(
        "SELECT x_post_id FROM posts WHERE id = ?", (in_reply_to_post_id,)
    ).fetchone()
    if target is None:
        return None
    target_x_id = target["x_post_id"]
    return str(target_x_id) if target_x_id is not None else None


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

    Phase 8 branches inside the transaction:

    * ``publish_via_api_enabled = TRUE``: call ``check_write_rate_capacity``
      → call ``publish_post_to_x_via_api`` → on 200 set
      ``publish_method='agent_confirmed'`` and ``posts.x_post_id`` from
      the response. Specific exceptions handled with the §22 + §29.11
      token-consumed matrix.
    * ``publish_via_api_enabled = FALSE``: take the Phase 5.5 manual-
      clipboard branch unchanged.
    """
    if message_id is None:
        message_id = _ensure_audit_anchor_message(conn)

    arguments = {"post_id": post_id, "confirmation_token": raw_token}
    consumed: confirmation.ConsumedToken | None = None
    use_api_branch = _read_publish_via_api_enabled(conn)

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

            # Phase 8 API branch: rate-limit gate runs BEFORE the six-check
            # so a rate-limited window doesn't burn the token.
            if use_api_branch:
                capacity = x_client.check_write_rate_capacity(conn)
                if not capacity.ok:
                    reset_iso = (
                        capacity.reset_at_utc.isoformat()
                        if capacity.reset_at_utc
                        else None
                    )
                    raise RateLimitRefusalError(
                        capacity.reason or "rate-limited",
                        reset_at_iso=reset_iso,
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

            if use_api_branch:
                in_reply_to_x_id = _resolve_reply_target_x_post_id(conn, row)
                api_data = x_client.publish_post_to_x_via_api(
                    post_text,
                    in_reply_to_x_post_id=in_reply_to_x_id,
                    conn=conn,
                )
                api_x_post_id = str(api_data["id"])

                conn.execute(
                    """
                    UPDATE posts
                    SET publish_method = 'agent_confirmed',
                        published_to_x_at = ?,
                        x_post_id = ?,
                        manual_confirmation_status = 'confirmed',
                        publish_attempt_count = publish_attempt_count + 1,
                        publish_last_error = NULL,
                        published_via_agent_message_id = ?
                    WHERE id = ?
                    """,
                    (_utcnow_iso(), api_x_post_id, message_id, post_id),
                )
                conn.execute(
                    """
                    UPDATE publish_confirmation_tokens
                    SET consumed_by_x_post_id = ?
                    WHERE id = ?
                    """,
                    (api_x_post_id, consumed.token_id),
                )

                audit.log_tool_call(
                    conn,
                    message_id=message_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    status="success",
                    result={
                        "method": "agent_confirmed",
                        "x_post_id": api_x_post_id,
                    },
                    confirmation_token_id=consumed.token_id,
                )
                audit_log.log(
                    conn,
                    event_category="publish",
                    event_type="publish_succeeded",
                    target_type="post",
                    target_id=post_id,
                    details={
                        "method": "agent_confirmed",
                        "x_post_id": api_x_post_id,
                        "tool_name": tool_name,
                        "confirmation_token_id": consumed.token_id,
                    },
                )

                return PublishResult(
                    success=True,
                    post_id=post_id,
                    method="agent_confirmed",
                    x_post_id=api_x_post_id,
                )

            # Manual-clipboard branch (publish_via_api_enabled = FALSE).
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

            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_succeeded",
                target_type="post",
                target_id=post_id,
                details={
                    "method": "manual_clipboard",
                    "intent_url": intent_url,
                    "tool_name": tool_name,
                    "confirmation_token_id": consumed.token_id,
                },
            )

        return PublishResult(
            success=True,
            post_id=post_id,
            method="manual_clipboard",
            intent_url=intent_url,
        )

    except RateLimitRefusalError as exc:
        # Phase 8 §22: 429 / capacity refusal BEFORE the X API call.
        # Token stays UN-consumed (the consume UPDATE was inside the
        # rolled-back transaction).
        with transaction(conn):
            conn.execute(
                """
                UPDATE posts
                SET publish_attempt_count = publish_attempt_count + 1,
                    publish_last_error = ?
                WHERE id = ?
                """,
                (f"RateLimitRefusal: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"RateLimitRefusal: {exc}",
                confirmation_token_id=None,
            )
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_rate_limited",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=str(exc),
                details={
                    "tool_name": tool_name,
                    "reset_at_iso": exc.reset_at_iso,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=str(exc),
            error_kind="rate_limited",
        )

    except x_client.XApiRateLimited as exc:
        # X API itself returned 429 mid-publish. Token UN-consumed via
        # ROLLBACK; if validate_and_consume_token ran, its UPDATE was
        # rolled back so consumed_at_utc stays NULL.
        with transaction(conn):
            conn.execute(
                """
                UPDATE posts
                SET publish_attempt_count = publish_attempt_count + 1,
                    publish_last_error = ?
                WHERE id = ?
                """,
                (f"XApiRateLimited: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"XApiRateLimited: {exc}",
                confirmation_token_id=None,
            )
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_rate_limited",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=str(exc),
                details={
                    "tool_name": tool_name,
                    "retry_after_seconds": exc.retry_after_seconds,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=str(exc),
            error_kind="rate_limited",
        )

    except x_client.XApiColdReplyError as exc:
        # 403: X accepted the request and refused it. Token CONSUMED
        # per §22 + §29.11. Re-mark consumed_at_utc after the rollback.
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
                (f"XApiColdReply: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"XApiColdReply: {exc}",
                confirmation_token_id=consumed.token_id if consumed else None,
            )
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_cold_reply",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=str(exc),
                details={
                    "tool_name": tool_name,
                    "confirmation_token_id": consumed.token_id if consumed else None,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=str(exc),
            error_kind="cold_reply",
        )

    except x_client.XApiTimeoutError as exc:
        # Timeout mid-call: X may have processed the request. Token
        # CONSUMED (re-mark). publish_method='unknown' so the crash-
        # recovery scan (recovery.py) picks it up via the existing
        # x_post_id IS NULL + publish_method != 'failed' predicate.
        with transaction(conn):
            if consumed is not None:
                conn.execute(
                    "UPDATE publish_confirmation_tokens SET consumed_at_utc = ? WHERE id = ?",
                    (_utcnow_iso(), consumed.token_id),
                )
            conn.execute(
                """
                UPDATE posts
                SET publish_method = 'unknown',
                    published_to_x_at = ?,
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (_utcnow_iso(), f"XApiTimeout: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"XApiTimeout: {exc}",
                confirmation_token_id=consumed.token_id if consumed else None,
            )
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_timeout",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=str(exc),
                details={
                    "tool_name": tool_name,
                    "confirmation_token_id": consumed.token_id if consumed else None,
                    "crash_recovery_required": True,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=str(exc),
            error_kind="timeout",
        )

    except x_client.XApiServerError as exc:
        # 5xx after retry exhaustion: token CONSUMED per rule #10(f).
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
                (f"XApiServerError: {exc}", post_id),
            )
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=arguments,
                status="error",
                error_message=f"XApiServerError: {exc}",
                confirmation_token_id=consumed.token_id if consumed else None,
            )
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_server_error",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=str(exc),
                details={
                    "tool_name": tool_name,
                    "confirmation_token_id": consumed.token_id if consumed else None,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=str(exc),
            error_kind="server_error",
        )

    except confirmation.ConfirmationTokenError as exc:
        # Validation failure path (§28.10 step 6): the main transaction
        # rolled back, so the token UPDATE never landed → token stays
        # unconsumed and Daniel can retry within the TTL.
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
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_confirmation",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                details={"tool_name": tool_name},
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"{type(exc).__name__}: {exc}",
            error_kind="confirmation",
        )

    except DraftTooLongError as exc:
        # Length-cap failure: token was never validated.
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
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_length_cap",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=f"DraftTooLongError: {exc}",
                details={"tool_name": tool_name},
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"DraftTooLongError: {exc}",
            error_kind="length",
        )

    except Exception as exc:
        # Post-validation runtime failure: token MUST stay consumed.
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
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_failed_runtime",
                target_type="post",
                target_id=post_id,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                details={
                    "tool_name": tool_name,
                    "confirmation_token_id": consumed.token_id if consumed else None,
                },
            )
        return PublishResult(
            success=False,
            post_id=post_id,
            method="failed",
            error=f"{type(exc).__name__}: {exc}",
            error_kind="runtime",
        )
