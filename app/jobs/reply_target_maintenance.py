"""Reply-target maintenance jobs — spec §29.11.

Three idempotent jobs live here:

1. ``expire_stale_candidates`` — transitions ``status='candidate'`` rows
   whose ``last_checked_at_utc`` is older than ``reply_target_expiry_hours``
   into ``status='expired'``. Runs at app boot AND can be wired into a
   daily scheduler. Returns the affected ids.

2. ``stale_drafted_candidates`` — *read-only*. Returns the ids of
   ``status='drafted'`` rows older than 24h. The Queue page surfaces a
   per-row banner ("Did you post this? …") for each. The transition to
   ``posted`` / ``skipped`` is always an explicit user action, never an
   automated one.

3. ``vacuum_cleanup_dead_candidates`` — daily VACUUM cleanup. Deletes
   rows where ``status IN ('skipped','expired','target_deleted')`` AND
   ``discovered_at_utc < now() - 90 days``. Posted candidates stay forever
   (joined via ``posted_reply_post_id`` for postmortem audit).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import transaction

# Used by the VACUUM cleanup; mirrors §29.11 row 5.
DEAD_STATUSES = ("skipped", "expired", "target_deleted")
VACUUM_CLEANUP_AGE_DAYS = 90
DRAFTED_BANNER_AGE_HOURS = 24


def _get_expiry_hours(conn: sqlite3.Connection) -> int:
    """Read ``reply_target_expiry_hours`` from settings; default 24h."""
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'reply_target_expiry_hours'"
    ).fetchone()
    if row is None:
        return 24
    try:
        return int(json.loads(row["value_json"]))
    except Exception:
        return 24


def expire_stale_candidates(conn: sqlite3.Connection) -> list[int]:
    """Transition stale candidates to ``status='expired'``. Returns affected ids."""
    hours = _get_expiry_hours(conn)
    cutoff_expression = f"datetime('now', '-{int(hours)} hours')"
    # Two-step (SELECT then UPDATE) so the return value is exact.
    stale_rows = conn.execute(
        f"""
        SELECT id FROM reply_targets
        WHERE status = 'candidate'
          AND last_checked_at_utc < {cutoff_expression}
        """
    ).fetchall()
    stale_ids = [int(r["id"]) for r in stale_rows]
    if not stale_ids:
        return []
    placeholders = ",".join("?" for _ in stale_ids)
    with transaction(conn):
        conn.execute(
            f"""
            UPDATE reply_targets
            SET status = 'expired',
                expired_at_utc = datetime('now')
            WHERE id IN ({placeholders})
            """,
            stale_ids,
        )
    return stale_ids


def stale_drafted_candidates(
    conn: sqlite3.Connection,
    *,
    age_hours: int = DRAFTED_BANNER_AGE_HOURS,
) -> list[dict[str, Any]]:
    """Read-only — return drafted rows older than `age_hours` for the banner.

    Each row carries the fields the Queue page needs to render the
    "Did you post this? Record URL or close as skipped" banner.
    """
    rows = conn.execute(
        f"""
        SELECT id, target_post_url, target_author_handle, agent_draft_id,
               discovered_at_utc, last_checked_at_utc
        FROM reply_targets
        WHERE status = 'drafted'
          AND agent_draft_id IS NOT NULL
          AND last_checked_at_utc < datetime('now', '-{int(age_hours)} hours')
        ORDER BY last_checked_at_utc ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def vacuum_cleanup_dead_candidates(
    conn: sqlite3.Connection,
    *,
    age_days: int = VACUUM_CLEANUP_AGE_DAYS,
) -> int:
    """Delete dead rows older than `age_days`. Returns the number deleted.

    Wired into ``app.backup.backup_database`` so cleanup happens on the same
    daily cadence as the VACUUM INTO backup. Posted rows are preserved
    regardless of age — they remain the audit trail for the corresponding
    ``posts`` rows via ``posted_reply_post_id``.
    """
    placeholders = ",".join("?" for _ in DEAD_STATUSES)
    cur = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM reply_targets
        WHERE status IN ({placeholders})
          AND discovered_at_utc < datetime('now', '-{int(age_days)} days')
        """,
        DEAD_STATUSES,
    )
    n = int(cur.fetchone()["n"] or 0)
    if n == 0:
        return 0
    with transaction(conn):
        conn.execute(
            f"""
            DELETE FROM reply_targets
            WHERE status IN ({placeholders})
              AND discovered_at_utc < datetime('now', '-{int(age_days)} days')
            """,
            DEAD_STATUSES,
        )
    return n
