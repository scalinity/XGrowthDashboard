"""Confirmation token mint + six-check validation chain (§28.2 rule #10, §28.10).

The publish flow is gated by a single-use, 60-second-TTL UUID minted by
the Streamlit click-handler. The raw UUID lives ONLY in the click-handler's
local stack frame and the synchronous Python call into the publish tool.
The DB stores only ``sha256(token)``. The agent's tool registry contains
no tool that reads this table — the token registry is unreachable from
the agent loop by construction.

Validation is the six-check chain from §28.2 rule #10. Each check has a
typed exception so tests can pin individual failure paths:

(a) ``token_hash`` exists                  → MissingTokenError
(b) ``expires_at_utc > now()``             → ExpiredTokenError
(c) ``consumed_at_utc IS NULL``            → ConsumedTokenError
(d) ``draft_text_hash_at_issue`` matches   → DraftTextChangedError
(e) row's ``post_id`` == argument          → PostIdMismatchError
(f) draft is still in ``'draft'`` state    → DraftNotInDraftStateError

A successful ``validate_and_consume_token`` runs inside ``BEGIN IMMEDIATE``
and atomically marks ``consumed_at_utc`` so retries and concurrent
attempts cannot double-spend the token.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Spec §10.2 settings block — token TTL default. The setting key exists for
# Settings UI editability later; mint_confirmation_token honors any override
# passed by the caller, otherwise falls back to this constant.
DEFAULT_TOKEN_TTL_SECONDS: int = 60


# ---------------------------------------------------------------------------
# Typed errors for the six checks. Tests pin individual paths via these.
# ---------------------------------------------------------------------------
class ConfirmationTokenError(Exception):
    """Base class for any confirmation-token validation failure."""


class MissingTokenError(ConfirmationTokenError):
    """Check (a): no row matches ``sha256(raw_token)``."""


class ExpiredTokenError(ConfirmationTokenError):
    """Check (b): row's ``expires_at_utc`` is in the past."""


class ConsumedTokenError(ConfirmationTokenError):
    """Check (c): row's ``consumed_at_utc`` is already set."""


class DraftTextChangedError(ConfirmationTokenError):
    """Check (d): draft text hash at issue differs from current ``posts.text`` hash."""


class PostIdMismatchError(ConfirmationTokenError):
    """Check (e): row's ``post_id`` does not match the caller's argument."""


class DraftNotInDraftStateError(ConfirmationTokenError):
    """Check (f): ``posts.manual_confirmation_status`` is not ``'draft'``."""


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_token(raw_token: str) -> str:
    """Return ``sha256(raw_token)`` hex digest. Public for tests / click-handler."""
    return _sha256_hex(raw_token)


def hash_draft_text(text: str) -> str:
    """Return ``sha256(text)`` hex digest. Public for tests / click-handler."""
    return _sha256_hex(text)


def _parse_db_timestamp(s: str) -> datetime:
    """Tolerate either ``YYYY-MM-DD HH:MM:SS`` or ISO-8601 with ``T`` separator.

    SQLite's ``datetime('now')`` writes the space-separator form, and
    ``mint_confirmation_token`` matches it with strftime. But a debug
    shell, migration replay, or future operator using
    ``datetime.now(...).isoformat()`` would emit the ``T`` form and
    crash the prior strict ``strptime`` with ``ValueError`` — escaping
    the typed-exception catch in ``publish_post_atomic`` and leaving
    the token consumption in an indeterminate state. Defensive parser
    normalizes the separator and strips fractional seconds before the
    strict parse.
    """
    normalized = s.replace("T", " ")
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc,
    )


# ---------------------------------------------------------------------------
# Mint — used by the click-handler ONLY. Returns the raw UUID; caller MUST
# pass it synchronously to the publish tool and never persist or log it.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MintedToken:
    raw_token: str
    token_id: int
    expires_at_utc: str


def _get_setting_ttl_seconds(conn: sqlite3.Connection) -> int:
    """Read x_posting_confirmation_token_ttl_seconds from settings (S9)."""
    import json as _json
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'x_posting_confirmation_token_ttl_seconds'"
    ).fetchone()
    if row is None:
        return DEFAULT_TOKEN_TTL_SECONDS
    try:
        return int(_json.loads(row["value_json"]))
    except (_json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_TOKEN_TTL_SECONDS


def mint_confirmation_token(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    draft_text: str,
    ttl_seconds: int | None = None,
) -> MintedToken:
    """Mint a single-use publish token for ``post_id`` with the given TTL.

    Inserts a row into ``publish_confirmation_tokens`` with the hash of a
    freshly-minted UUID and the hash of the current draft text. Returns
    the raw UUID to the caller — it MUST be passed synchronously into
    the publish tool and NEVER stored.

    S9: ``ttl_seconds`` defaults to the value from settings (
    ``x_posting_confirmation_token_ttl_seconds``, default 60) rather than
    the hardcoded constant. Explicit kwarg still wins for tests.
    """
    effective_ttl = (
        int(ttl_seconds)
        if ttl_seconds is not None
        else _get_setting_ttl_seconds(conn)
    )
    raw_token = uuid.uuid4().hex
    token_hash = hash_token(raw_token)
    draft_hash = hash_draft_text(draft_text)
    now = _utcnow()
    expires_at = now + timedelta(seconds=effective_ttl)
    cur = conn.execute(
        """
        INSERT INTO publish_confirmation_tokens
            (token_hash, post_id, draft_text_hash_at_issue,
             created_at_utc, expires_at_utc)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            token_hash,
            post_id,
            draft_hash,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return MintedToken(
        raw_token=raw_token,
        token_id=int(cur.lastrowid),
        expires_at_utc=expires_at.strftime("%Y-%m-%d %H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# Six-check validation chain.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConsumedToken:
    """Result of a successful validate_and_consume_token call."""

    token_id: int
    post_id: int


def validate_and_consume_token(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    raw_token: str,
    now: datetime | None = None,
) -> ConsumedToken:
    """Run the six checks; on success, mark ``consumed_at_utc`` atomically.

    The caller is responsible for running this inside a transaction that
    also performs the publish state writes (see ``app/agent/publish.py``).
    The function does not start its own transaction so callers can compose
    it with the X API call + post updates.
    """
    now = now or _utcnow()
    token_hash = hash_token(raw_token)

    row = conn.execute(
        """
        SELECT id, post_id, draft_text_hash_at_issue,
               expires_at_utc, consumed_at_utc
        FROM publish_confirmation_tokens
        WHERE token_hash = ?
        """,
        (token_hash,),
    ).fetchone()

    # (a) token exists
    if row is None:
        raise MissingTokenError("no token row matches the provided raw token")

    # app.db.connect sets row_factory=sqlite3.Row globally, so the prior
    # `hasattr(row, 'keys') else row[N]` defensive fallback was dead code.
    token_id = int(row["id"])
    row_post_id = int(row["post_id"])
    issued_text_hash = row["draft_text_hash_at_issue"]
    expires_at_str = row["expires_at_utc"]
    consumed_at_str = row["consumed_at_utc"]

    # (c) not consumed (check before expiry to give the more actionable error
    # when a token has been spent; expiry vs consumed are mutually exclusive
    # in practice but order is defensive).
    if consumed_at_str is not None:
        raise ConsumedTokenError(f"token {token_id} already consumed at {consumed_at_str}")

    # (b) not expired
    expires_at = _parse_db_timestamp(expires_at_str)
    if expires_at <= now:
        raise ExpiredTokenError(
            f"token {token_id} expired at {expires_at_str} (now={now.isoformat()})"
        )

    # (e) post_id matches the caller's argument
    if row_post_id != post_id:
        raise PostIdMismatchError(
            f"token {token_id} authorizes post_id={row_post_id}, "
            f"caller passed post_id={post_id}"
        )

    # (d) draft text hasn't changed since issue (Daniel didn't edit after mint)
    post_row = conn.execute(
        "SELECT text, manual_confirmation_status FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if post_row is None:
        raise PostIdMismatchError(
            f"token {token_id} references post_id={post_id} which no longer exists"
        )
    current_text = post_row["text"]
    current_status = post_row["manual_confirmation_status"]
    if hash_draft_text(current_text) != issued_text_hash:
        raise DraftTextChangedError(
            f"post_id={post_id} text has changed since token {token_id} was issued"
        )

    # (f) draft is still in draft state
    if current_status != "draft":
        raise DraftNotInDraftStateError(
            f"post_id={post_id} has manual_confirmation_status={current_status!r}, "
            f"not 'draft' — refusing to consume token {token_id}"
        )

    # All checks passed → consume atomically.
    conn.execute(
        "UPDATE publish_confirmation_tokens SET consumed_at_utc = ? WHERE id = ?",
        (now.strftime("%Y-%m-%d %H:%M:%S"), token_id),
    )
    return ConsumedToken(token_id=token_id, post_id=post_id)
