"""Atomic publish transaction — §28.10 with Phase 8 API-vs-manual branch.

The publish flow is invoked ONLY by the Streamlit click-handler after
Daniel confirms in the modal. The click-handler does:

1. ``mint_confirmation_token(conn, post_id, draft_text)`` → MintedToken.
2. Synchronous call into ``_internal_tools.publish_post_to_x(post_id, raw_token)``,
   which delegates to ``publish_post_atomic`` here.

The raw UUID lives only on the click-handler's local stack frame; once
``publish_post_atomic`` returns, the raw value is dropped. Audit logging
redacts the token via ``app.agent.audit.log_tool_call`` (substitutes
``publish_confirmation_tokens.id`` for the raw value before insert).

Phase 5.5 shipped the manual-clipboard branch only. Phase 8 (migration
019) adds the API branch alongside it, gated by ``publish_via_api_enabled``
(default TRUE).

**Phase 8 R-1 architecture (split-txn pattern):** the X API subprocess
call (``publish_post_to_x_via_api``) now happens OUTSIDE any open
SQLite transaction so the writer-lock is never held across network
I/O. The flow is:

1. Read-only preflight (no txn): length cap, rate capacity, reply
   target resolution.
2. X API call OUTSIDE any txn. ``_log_raw`` audit rows from
   ``app.x_client.request()`` commit in autocommit and SURVIVE if the
   downstream commit fails.
3. Commit txn (BEGIN IMMEDIATE held for milliseconds only): validate
   + consume token + UPDATE posts + UPDATE consumed_by_x_post_id +
   audit-log writes.

The four typed-exception failure modes from the X API call are
handled BEFORE entering the commit txn:

* ``XApiRateLimited`` (429): token UN-consumed (no txn ever opened).
* ``XApiColdReplyError`` (403): token CONSUMED via an explicit narrow
  txn (X considered it a real attempt — §22 + §29.11). No posts row
  created.
* ``XApiServerError`` (5xx after retry): token CONSUMED per rule
  #10(f). No posts row.
* ``XApiTimeoutError``: token CONSUMED to prevent retry-double-post.
  ``publish_method='unknown'`` + ``published_to_x_at`` set so the
  §28.10 step 8 crash-recovery scan picks it up via the existing
  ``detect_orphans`` predicate.

The manual-clipboard branch (``publish_via_api_enabled = FALSE``)
takes the same shape minus the API call: read-only preflight →
commit txn (validate-and-consume + UPDATE posts to
``manual_clipboard``). §29.1 invariant preserved.

The validate-and-consume + posts UPDATE remain atomic with each other
(single BEGIN IMMEDIATE txn). The X API call is no longer atomic with
the DB writes — a successful POST followed by a commit-txn failure
(e.g. ConfirmationTokenError because Daniel edited the draft text
mid-flight, or a runtime error during the commit) leaves an orphan
that the crash-recovery scan reconciles via
``recovery.reconcile_orphans_via_x_api``.

Length cap (§28.10): drafts > 280 chars (``x_post_max_chars``) are
refused before the API call. The modal also disables the confirm
button when char_count > 280 — server-side belt to that suspenders.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app import x_client
from app.agent import audit, audit_log, confirmation
from app.db import transaction

X_POST_MAX_CHARS: int = 280

ErrorKind = Literal[
    "rate_limited",
    "cold_reply",
    "server_error",
    "timeout",
    "confirmation",
    "length",
    "runtime",
]


class DraftTooLongError(RuntimeError):
    """Draft text exceeds ``X_POST_MAX_CHARS``. Refused before any state writes."""


class RateLimitRefusalError(RuntimeError):
    """Phase 8: write-rate sliding window refused this publish attempt.

    Surfaced when ``check_write_rate_capacity()`` returns ``ok=False``
    BEFORE the X API call. The confirmation token stays UN-consumed
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
    error_kind: ErrorKind | None = None


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
    019's seeded default and §28.10 Phase 5.5 → Phase 8 transition.
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
    """For reply-type posts, return the target's X post id (snowflake string).

    ``posts.in_reply_to_post_id`` is a TEXT column storing the target's
    X post ID directly (per migration 001 line 111 + the existing
    ``app/forms/post_log.py`` populator). Earlier Phase 8 code wrongly
    treated it as an internal posts.id integer and did a PK lookup —
    that lookup never matched, so the API branch silently posted
    replies as standalone tweets.

    For agent-drafted replies whose ``in_reply_to_post_id`` hasn't been
    populated yet, fall back to parsing the X status id out of
    ``agent_drafts.target_post_url`` via the standard
    ``/status/<id>`` URL shape.
    """
    if post_row["type"] != "reply":
        return None
    target_x_id = post_row["in_reply_to_post_id"]
    if target_x_id:
        return str(target_x_id)
    # Fallback: agent-drafted reply with empty in_reply_to_post_id.
    row = conn.execute(
        """
        SELECT ad.target_post_url
          FROM posts p
          JOIN agent_drafts ad ON ad.id = p.agent_draft_id
         WHERE p.id = ?
        """,
        (post_row["id"],),
    ).fetchone()
    if row is None or not row["target_post_url"]:
        return None
    m = re.search(r"/status(?:es)?/(\d+)", row["target_post_url"])
    return m.group(1) if m else None


def _ensure_audit_anchor_message(conn: sqlite3.Connection) -> int:
    """Return a message_id to anchor a publish audit row to.

    When the click-handler invokes publish without a live conversation,
    anchor to the single dedicated ``[publish-flow audit anchor]``
    archived conversation (created on demand, then reused).
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


# ---------------------------------------------------------------------------
# Failure-path emitters (each runs a narrow recovery txn — no network I/O).
# ---------------------------------------------------------------------------
def _emit_length_failure(
    conn: sqlite3.Connection,
    post_id: int,
    exc: BaseException,
    *,
    message_id: int,
    arguments: dict,
    tool_name: str,
) -> PublishResult:
    """Length-cap or missing-row failure. Token never validated."""
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
            target_id=str(post_id),
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


def _emit_rate_limit_refusal(
    conn: sqlite3.Connection,
    post_id: int,
    capacity: x_client.WriteRateCapacity,
    *,
    message_id: int,
    arguments: dict,
    tool_name: str,
) -> PublishResult:
    """Local rate-capacity refusal BEFORE the X API call. Token UN-consumed."""
    reason = capacity.reason or "rate-limited"
    reset_iso = capacity.reset_at_utc.isoformat() if capacity.reset_at_utc else None
    with transaction(conn):
        conn.execute(
            """
            UPDATE posts
            SET publish_attempt_count = publish_attempt_count + 1,
                publish_last_error = ?
            WHERE id = ?
            """,
            (f"RateLimitRefusal: {reason}", post_id),
        )
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=f"RateLimitRefusal: {reason}",
            confirmation_token_id=None,
        )
        audit_log.log(
            conn,
            event_category="publish",
            event_type="publish_failed_rate_limited",
            target_type="post",
            target_id=str(post_id),
            success=False,
            error_message=reason,
            details={"tool_name": tool_name, "reset_at_iso": reset_iso},
        )
    return PublishResult(
        success=False,
        post_id=post_id,
        method="failed",
        error=reason,
        error_kind="rate_limited",
    )


def _emit_api_failure(
    conn: sqlite3.Connection,
    post_id: int,
    exc: BaseException,
    *,
    kind: ErrorKind,
    consume_token: bool,
    raw_token: str,
    message_id: int,
    arguments: dict,
    tool_name: str,
    mark_unknown_for_orphan: bool = False,
    extra_audit_details: dict | None = None,
) -> PublishResult:
    """Map an X API failure to its §22 + §29.11 token-consumed outcome.

    ``consume_token=True`` runs validate-and-consume in a narrow txn so
    the token is marked consumed even though no posts row gets created.
    Failures of the consume step are tolerated (token might already be
    expired) — the audit row records the API failure regardless.

    ``mark_unknown_for_orphan=True`` (timeout path) sets
    ``publish_method='unknown'`` + ``published_to_x_at`` so the
    §28.10 step 8 crash-recovery scan picks the row up.
    """
    error_message = f"{type(exc).__name__}: {exc}"
    publish_method_value = "unknown" if mark_unknown_for_orphan else "failed"
    consumed_id: int | None = None

    with transaction(conn):
        if consume_token:
            try:
                consumed = confirmation.validate_and_consume_token(
                    conn, post_id=post_id, raw_token=raw_token
                )
                consumed_id = consumed.token_id
            except confirmation.ConfirmationTokenError:
                # Token already expired / consumed / drift — proceed with
                # failure logging anyway; audit trail still captures the API
                # failure even when the token side-effect can't land.
                consumed_id = None

        if mark_unknown_for_orphan:
            conn.execute(
                """
                UPDATE posts
                SET publish_method = ?,
                    published_to_x_at = ?,
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (publish_method_value, _utcnow_iso(), error_message, post_id),
            )
        else:
            conn.execute(
                """
                UPDATE posts
                SET publish_method = ?,
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (publish_method_value, error_message, post_id),
            )

        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=error_message,
            confirmation_token_id=consumed_id,
        )
        details: dict = {
            "tool_name": tool_name,
            "confirmation_token_id": consumed_id,
        }
        if extra_audit_details:
            details.update(extra_audit_details)
        if mark_unknown_for_orphan:
            details["crash_recovery_required"] = True
        audit_log.log(
            conn,
            event_category="publish",
            event_type=f"publish_failed_{kind}",
            target_type="post",
            target_id=str(post_id),
            success=False,
            error_message=str(exc),
            details=details,
        )
    return PublishResult(
        success=False,
        post_id=post_id,
        method="failed",
        error=error_message,
        error_kind=kind,
    )


def _emit_confirmation_failure(
    conn: sqlite3.Connection,
    post_id: int,
    exc: BaseException,
    *,
    message_id: int,
    arguments: dict,
    tool_name: str,
    api_orphan: bool = False,
) -> PublishResult:
    """Token validation failed during the commit txn.

    Token stays UN-consumed (the consume UPDATE was inside the
    rolled-back txn). If ``api_orphan=True`` the X post already exists
    — the orphan-detection scan reconciles next boot via text-hash
    match.
    """
    error_message = f"{type(exc).__name__}: {exc}"
    with transaction(conn):
        if api_orphan:
            conn.execute(
                """
                UPDATE posts
                SET publish_method = 'unknown',
                    published_to_x_at = ?,
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (_utcnow_iso(), error_message, post_id),
            )
        else:
            conn.execute(
                """
                UPDATE posts
                SET publish_attempt_count = publish_attempt_count + 1,
                    publish_last_error = ?
                WHERE id = ?
                """,
                (error_message, post_id),
            )
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=error_message,
            confirmation_token_id=None,
        )
        audit_log.log(
            conn,
            event_category="publish",
            event_type="publish_failed_confirmation",
            target_type="post",
            target_id=str(post_id),
            success=False,
            error_message=error_message,
            details={"tool_name": tool_name, "api_orphan": api_orphan},
        )
    return PublishResult(
        success=False,
        post_id=post_id,
        method="failed",
        error=error_message,
        error_kind="confirmation",
    )


def _emit_runtime_failure(
    conn: sqlite3.Connection,
    post_id: int,
    exc: BaseException,
    *,
    raw_token: str,
    message_id: int,
    arguments: dict,
    tool_name: str,
    api_orphan: bool = False,
) -> PublishResult:
    """Unexpected runtime failure during the commit txn.

    Re-mark token consumed (rule #10(f) — prevent retry abuse on a
    partially-applied publish). If ``api_orphan=True``, set
    ``publish_method='unknown'`` for crash-recovery.
    """
    error_message = f"{type(exc).__name__}: {exc}"
    consumed_id: int | None = None
    with transaction(conn):
        try:
            consumed = confirmation.validate_and_consume_token(
                conn, post_id=post_id, raw_token=raw_token
            )
            consumed_id = consumed.token_id
        except confirmation.ConfirmationTokenError:
            consumed_id = None

        if api_orphan:
            conn.execute(
                """
                UPDATE posts
                SET publish_method = 'unknown',
                    published_to_x_at = ?,
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (_utcnow_iso(), error_message, post_id),
            )
        else:
            conn.execute(
                """
                UPDATE posts
                SET publish_method = 'failed',
                    publish_last_error = ?,
                    publish_attempt_count = publish_attempt_count + 1
                WHERE id = ?
                """,
                (error_message, post_id),
            )

        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_message=error_message,
            confirmation_token_id=consumed_id,
        )
        audit_log.log(
            conn,
            event_category="publish",
            event_type="publish_failed_runtime",
            target_type="post",
            target_id=str(post_id),
            success=False,
            error_message=error_message,
            details={
                "tool_name": tool_name,
                "confirmation_token_id": consumed_id,
                "api_orphan": api_orphan,
            },
        )
    return PublishResult(
        success=False,
        post_id=post_id,
        method="failed",
        error=error_message,
        error_kind="runtime",
    )


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------
def publish_post_atomic(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    raw_token: str,
    message_id: int | None = None,
    tool_name: str = "publish_post_to_x",
) -> PublishResult:
    """Phase 8 R-1 split-txn publish flow.

    1. Read-only preflight (no txn): length cap, rate-capacity, reply
       target resolution.
    2. X API call OUTSIDE any txn (writer-lock NOT held; ``_log_raw``
       audit rows commit in autocommit and survive a downstream
       commit failure).
    3. Commit txn (BEGIN IMMEDIATE held for milliseconds only):
       validate-and-consume token + UPDATE posts + UPDATE
       consumed_by_x_post_id + audit writes.

    Token-consumed matrix preserved via ``_emit_api_failure(consume_token=...)``:
    429 stays UN-consumed; 403 / 5xx-after-retry / timeout all CONSUME
    via a narrow txn before the failure return.
    """
    if message_id is None:
        message_id = _ensure_audit_anchor_message(conn)

    arguments = {"post_id": post_id, "confirmation_token": raw_token}
    use_api_branch = _read_publish_via_api_enabled(conn)

    # ----- 1. Read-only preflight -----
    row = conn.execute(
        "SELECT id, text, type, in_reply_to_post_id FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if row is None:
        return _emit_length_failure(
            conn,
            post_id,
            DraftTooLongError(f"post_id={post_id} not found"),
            message_id=message_id,
            arguments=arguments,
            tool_name=tool_name,
        )
    if len(row["text"]) > X_POST_MAX_CHARS:
        return _emit_length_failure(
            conn,
            post_id,
            DraftTooLongError(
                f"draft is {len(row['text'])} chars; X cap is {X_POST_MAX_CHARS}"
            ),
            message_id=message_id,
            arguments=arguments,
            tool_name=tool_name,
        )

    if use_api_branch:
        capacity = x_client.check_write_rate_capacity(conn)
        if not capacity.ok:
            return _emit_rate_limit_refusal(
                conn,
                post_id,
                capacity,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
            )

    # ----- 1b. Six-check token validation BEFORE the X API call -----
    # RV2-34: per §28.10 rule #10, the six-check chain MUST run BEFORE
    # the X API call so a stale/edited/already-consumed/wrong-state draft
    # never burns an API request. validate_token_only is the read-only
    # twin of validate_and_consume_token — it raises the same typed
    # ConfirmationTokenError subclasses (DraftTextChangedError check (d),
    # DraftNotInDraftStateError check (f), etc.) without mutating the
    # token row. Consumption happens later in the post-API commit txn
    # via validate_and_consume_token which re-runs the same six checks
    # atomically (covers the race where the draft state mutates between
    # preflight and commit).
    try:
        confirmation.validate_token_only(
            conn, post_id=post_id, raw_token=raw_token
        )
    except confirmation.ConfirmationTokenError as exc:
        return _emit_confirmation_failure(
            conn,
            post_id,
            exc,
            message_id=message_id,
            arguments=arguments,
            tool_name=tool_name,
            api_orphan=False,
        )

    # ----- 2. X API call OUTSIDE any txn (writer-lock NOT held) -----
    api_data: dict | None = None
    if use_api_branch:
        in_reply_to_x_id = _resolve_reply_target_x_post_id(conn, row)
        try:
            api_data = x_client.publish_post_to_x_via_api(
                row["text"],
                in_reply_to_x_post_id=in_reply_to_x_id,
                conn=conn,
            )
        except x_client.XApiRateLimited as exc:
            # 429 → token UN-consumed (no txn opened).
            return _emit_api_failure(
                conn,
                post_id,
                exc,
                kind="rate_limited",
                consume_token=False,
                raw_token=raw_token,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
                extra_audit_details={"retry_after_seconds": exc.retry_after_seconds},
            )
        except x_client.XApiColdReplyError as exc:
            # 403 → token CONSUMED (X considered it a real attempt).
            return _emit_api_failure(
                conn,
                post_id,
                exc,
                kind="cold_reply",
                consume_token=True,
                raw_token=raw_token,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
            )
        except x_client.XApiTimeoutError as exc:
            # Timeout → token CONSUMED + publish_method='unknown' for
            # the §28.10 step 8 crash-recovery scan.
            return _emit_api_failure(
                conn,
                post_id,
                exc,
                kind="timeout",
                consume_token=True,
                raw_token=raw_token,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
                mark_unknown_for_orphan=True,
            )
        except x_client.XApiServerError as exc:
            # 5xx after retry exhaustion → token CONSUMED per rule #10(f).
            return _emit_api_failure(
                conn,
                post_id,
                exc,
                kind="server_error",
                consume_token=True,
                raw_token=raw_token,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
            )
        except x_client.XApiUnavailable as exc:
            # xurl missing / 401 / non-JSON output. Treat as runtime
            # failure — token stays UN-consumed (no X-side state change).
            return _emit_api_failure(
                conn,
                post_id,
                exc,
                kind="runtime",
                consume_token=False,
                raw_token=raw_token,
                message_id=message_id,
                arguments=arguments,
                tool_name=tool_name,
            )

    # ----- 3. Commit txn — token-consume + posts UPDATE -----
    try:
        with transaction(conn):
            consumed = confirmation.validate_and_consume_token(
                conn, post_id=post_id, raw_token=raw_token
            )

            if use_api_branch:
                assert api_data is not None
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
                    result={"method": "agent_confirmed", "x_post_id": api_x_post_id},
                    confirmation_token_id=consumed.token_id,
                )
                audit_log.log(
                    conn,
                    event_category="publish",
                    event_type="publish_succeeded",
                    target_type="post",
                    target_id=str(post_id),
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

            # Manual-clipboard branch.
            intent_url = _build_intent_url(
                row["text"],
                in_reply_to_post_id=row["in_reply_to_post_id"]
                if row["type"] == "reply"
                else None,
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
            audit_log.log(
                conn,
                event_category="publish",
                event_type="publish_succeeded",
                target_type="post",
                target_id=str(post_id),
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

    except confirmation.ConfirmationTokenError as exc:
        return _emit_confirmation_failure(
            conn,
            post_id,
            exc,
            message_id=message_id,
            arguments=arguments,
            tool_name=tool_name,
            api_orphan=(api_data is not None),
        )
    except Exception as exc:
        return _emit_runtime_failure(
            conn,
            post_id,
            exc,
            raw_token=raw_token,
            message_id=message_id,
            arguments=arguments,
            tool_name=tool_name,
            api_orphan=(api_data is not None),
        )
