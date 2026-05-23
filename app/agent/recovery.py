"""Crash-recovery — orphan-post detection + reconciliation (§28.10 step 8).

Boot-time scan for posts where the publish flow started but never
completed cleanly. MVP detection rule: ``publish_attempt_count > 0 AND
published_to_x_at IS NOT NULL AND publish_method != 'failed' AND
x_post_id IS NULL``. These are the rows where Daniel clicked Publish,
the click-handler began the transaction, but the chain didn't complete
(either Streamlit died mid-write or the user closed the tab before the
manual-clipboard URL was pasted back).

Phase 8 (migration 019 / §28.10 step 8): when ``publish_via_api_enabled
= TRUE`` and xurl is available, ``reconcile_orphans_via_x_api()`` calls
``GET /2/users/:id/tweets?since_id=...`` and auto-matches by text hash
against the published_confirmation_tokens.draft_text_hash_at_issue row
written at mint time. Orphans whose text matches a recent X post are
reconciled automatically; the rest stay in the manual-reconcile UI.

MVP reconciliation: orphans surface as a blocking banner in the §14.8
Agent Chat sidebar with two actions:
  * Mark posted — paste the live URL → populates x_post_id.
  * Mark not posted — flags as ``publish_method='failed'`` and clears
    the intent timestamp.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from app.db import transaction
from datetime import datetime, timezone


@dataclass(frozen=True)
class OrphanPost:
    post_id: int
    text: str
    published_to_x_at: str
    publish_attempt_count: int
    publish_method: str | None


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of one boot-time reconciliation sweep."""

    orphans_scanned: int
    auto_matched: list[tuple[int, str]]  # [(post_id, matched_x_post_id), …]
    remaining_for_manual: list[int]
    api_attempted: bool
    error: str | None = None


# Manual-clipboard publishes legitimately sit with `published_to_x_at NOT
# NULL AND x_post_id NULL` until Daniel finishes the manual paste via the
# existing "Mark posted" form. Treating those as orphans the moment they
# stage would surface a chat-banner + Settings entry on every successful
# publish — false positives that desensitize the user. Give the manual
# flow a 30-minute grace window before classifying as orphan.
MANUAL_CLIPBOARD_GRACE_MINUTES: int = 30


def detect_orphans(
    conn: sqlite3.Connection, *, limit: int = 50
) -> list[OrphanPost]:
    """Return posts that started publishing but never landed.

    Excludes manual_clipboard posts younger than the grace window —
    those are legitimately "pending paste" and Daniel hasn't had time
    to reconcile them via the existing Phase-2 "Mark posted" form yet.
    """
    rows = conn.execute(
        """
        SELECT id, text, published_to_x_at, publish_attempt_count,
               publish_method
        FROM posts
        WHERE publish_attempt_count > 0
          AND published_to_x_at IS NOT NULL
          AND x_post_id IS NULL
          AND (publish_method IS NULL OR publish_method != 'failed')
          AND NOT (
              publish_method = 'manual_clipboard'
              AND published_to_x_at > datetime('now', ?)
          )
        ORDER BY published_to_x_at DESC
        LIMIT ?
        """,
        (f"-{MANUAL_CLIPBOARD_GRACE_MINUTES} minutes", int(limit)),
    ).fetchall()
    return [
        OrphanPost(
            post_id=int(r["id"]),
            text=r["text"],
            published_to_x_at=r["published_to_x_at"],
            publish_attempt_count=int(r["publish_attempt_count"]),
            publish_method=r["publish_method"],
        )
        for r in rows
    ]


def mark_orphan_posted(
    conn: sqlite3.Connection, *, post_id: int, x_post_id: str, x_post_url: str | None = None
) -> None:
    """Reconcile an orphan as live: populate x_post_id, set status confirmed.

    Mirrors the post-publish state writes the original atomic transaction
    would have applied. publish_method stays at 'manual_clipboard' (the
    MVP method that began the flow) so the audit trail remains coherent.
    """
    conn.execute(
        """
        UPDATE posts
        SET x_post_id = ?,
            url = COALESCE(url, ?),
            manual_confirmation_status = 'confirmed',
            publish_last_error = NULL
        WHERE id = ?
        """,
        (x_post_id, x_post_url, post_id),
    )


def mark_orphan_failed(conn: sqlite3.Connection, *, post_id: int, reason: str) -> None:
    """Reconcile an orphan as never-posted.

    Sets ``publish_method = 'failed'`` AND clears ``published_to_x_at``
    (per the module docstring's promise — the prior implementation set
    only the method, leaving the intent timestamp populated, which
    would resurface the row if a future query dropped the
    ``publish_method != 'failed'`` predicate).
    """
    conn.execute(
        """
        UPDATE posts
        SET publish_method = 'failed',
            published_to_x_at = NULL,
            publish_last_error = ?
        WHERE id = ?
        """,
        (f"manual reconciliation @ {datetime.now(timezone.utc).isoformat()}: {reason}", post_id),
    )


# ---------------------------------------------------------------------------
# Phase 8 — API-driven reconciliation (§28.10 step 8).
# ---------------------------------------------------------------------------
def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _highest_committed_x_post_id(conn: sqlite3.Connection) -> str | None:
    """Return the highest (lexicographically-greatest) committed x_post_id.

    X tweet IDs are monotonically-increasing snowflake IDs, so the
    greatest value is the most-recent committed post; passing it as
    `since_id` to GET /2/users/:id/tweets bounds the response to posts
    that landed AFTER our last known state.
    """
    row = conn.execute(
        "SELECT MAX(x_post_id) AS m FROM posts WHERE x_post_id IS NOT NULL"
    ).fetchone()
    if row is None or row["m"] is None:
        return None
    return str(row["m"])


def reconcile_orphans_via_x_api(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> ReconciliationResult:
    """§28.10 step 8 Phase 8 reconciliation.

    Walks the orphan list from `detect_orphans()` and, when xurl is
    available + `publish_via_api_enabled = TRUE`, calls
    `api_get_recent_tweets(since_id=highest_committed_x_post_id)` to
    match each orphan's text against the X timeline by sha256 hash.

    On match: writes the reconciliation row via `mark_orphan_posted`,
    moving the orphan out of the manual-reconcile queue.

    On no match / xurl unavailable / publish_via_api_enabled=FALSE:
    leaves the orphan in place for manual reconciliation via the
    existing Phase 5.5 UI.
    """
    # Local import to avoid a hard module-level dependency on x_client
    # for users who never enable the API branch.
    from app import x_client

    orphans = detect_orphans(conn, limit=limit)
    if not orphans:
        return ReconciliationResult(
            orphans_scanned=0,
            auto_matched=[],
            remaining_for_manual=[],
            api_attempted=False,
        )

    # Gate check: only run the API path when the publish gate is ON,
    # mirroring publish.py's branch logic.
    publish_via_api = _read_publish_via_api_enabled(conn)
    if not publish_via_api:
        return ReconciliationResult(
            orphans_scanned=len(orphans),
            auto_matched=[],
            remaining_for_manual=[o.post_id for o in orphans],
            api_attempted=False,
        )

    since_id = _highest_committed_x_post_id(conn)
    try:
        recent = x_client.api_get_recent_tweets(
            since_id=since_id,
            max_results=min(100, max(25, len(orphans) * 2)),
            conn=conn,
        )
    except Exception as exc:  # pragma: no cover — api_get_recent_tweets already swallows XApi*
        return ReconciliationResult(
            orphans_scanned=len(orphans),
            auto_matched=[],
            remaining_for_manual=[o.post_id for o in orphans],
            api_attempted=True,
            error=str(exc),
        )

    if not recent:
        return ReconciliationResult(
            orphans_scanned=len(orphans),
            auto_matched=[],
            remaining_for_manual=[o.post_id for o in orphans],
            api_attempted=True,
        )

    # Build a text-hash → x_post_id index across the recent posts.
    by_hash: dict[str, str] = {}
    for row in recent:
        rid = row.get("id")
        rtext = row.get("text")
        if isinstance(rid, str) and isinstance(rtext, str):
            by_hash[_text_sha256(rtext)] = rid

    matched: list[tuple[int, str]] = []
    remaining: list[int] = []
    for orphan in orphans:
        candidate_hash = _text_sha256(orphan.text or "")
        x_post_id = by_hash.get(candidate_hash)
        if x_post_id is None:
            remaining.append(orphan.post_id)
            continue
        # Auto-reconcile inside a short transaction per orphan so a
        # later mid-walk failure doesn't roll back already-matched rows.
        # P8R-3: was `with conn:` which is a no-op on autocommit
        # connections (app/db.py opens with isolation_level=None). Use
        # the project's BEGIN-IMMEDIATE wrapper so mark_orphan_posted +
        # the publish_method UPDATE commit atomically — otherwise a
        # crash between them leaves x_post_id populated but
        # publish_method='unknown', a half-reconciled state.
        try:
            with transaction(conn):
                mark_orphan_posted(
                    conn,
                    post_id=orphan.post_id,
                    x_post_id=x_post_id,
                    x_post_url=f"https://x.com/i/web/status/{x_post_id}",
                )
                conn.execute(
                    "UPDATE posts SET publish_method = 'agent_confirmed' WHERE id = ?",
                    (orphan.post_id,),
                )
            matched.append((orphan.post_id, x_post_id))
        except sqlite3.DatabaseError:
            # Don't fail the whole sweep on one bad row; surface it via
            # the remaining-for-manual list.
            remaining.append(orphan.post_id)

    return ReconciliationResult(
        orphans_scanned=len(orphans),
        auto_matched=matched,
        remaining_for_manual=remaining,
        api_attempted=True,
    )


# P8R-5: was a duplicate of publish._read_publish_via_api_enabled.
# Import from publish.py instead so the settings reader has one source
# of truth — if the default flips (TRUE→FALSE) or the key is renamed,
# only the canonical implementation needs to change. publish.py doesn't
# import recovery, so there's no circular-import constraint.
from app.agent.publish import (  # noqa: E402 — kept at module bottom for clarity
    _read_publish_via_api_enabled,
)
