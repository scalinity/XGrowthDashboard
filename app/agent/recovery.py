"""Crash-recovery — orphan-post detection (§28.10 step 8).

Boot-time scan for posts where the publish flow started but never
completed cleanly. MVP detection rule: ``publish_attempt_count > 0 AND
published_to_x_at IS NOT NULL AND publish_method != 'failed' AND
x_post_id IS NULL``. These are the rows where Daniel clicked Publish,
the click-handler began the transaction, but the chain didn't complete
(either Streamlit died mid-write or the user closed the tab before the
manual-clipboard URL was pasted back).

MVP reconciliation: surfaced as a blocking banner in the §14.8 Agent
Chat sidebar with two actions:
  * Mark posted — paste the live URL → populates x_post_id.
  * Mark not posted — flags as ``publish_method='failed'`` and clears
    the intent timestamp.

V1.1 will replace the manual reconciliation with a ``GET /2/users/:id/
tweets?since_id=<last_known>`` call that auto-matches by text hash.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class OrphanPost:
    post_id: int
    text: str
    published_to_x_at: str
    publish_attempt_count: int
    publish_method: str | None


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
    """Reconcile an orphan as never-posted. publish_method → 'failed'."""
    conn.execute(
        """
        UPDATE posts
        SET publish_method = 'failed',
            publish_last_error = ?
        WHERE id = ?
        """,
        (f"manual reconciliation @ {datetime.now(timezone.utc).isoformat()}: {reason}", post_id),
    )
