"""Public Agent Ops orchestration helpers.

These functions keep UI and service routes from depending on private
``app.agent.tools`` call shapes directly. They are still local, synchronous jobs
for Daniel's single-user app; the contract here is UI-ready summaries.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.agent.tools import _find_reply_targets, _score_reply_candidates


def find_reply_targets(conn: sqlite3.Connection) -> dict[str, Any]:
    """Discover reply targets through the agent tools behind a public job API."""
    result = _find_reply_targets(conn)
    accounts = result.get("accounts") or []
    return {"ok": not result.get("errors"), **result, "account_count": len(accounts)}


def score_pending_reply_targets(
    conn: sqlite3.Connection, *, limit: int = 50
) -> dict[str, Any]:
    """Score pending reply-target candidates and surface partial failures."""
    rows = conn.execute(
        """
        SELECT id
          FROM reply_targets
         WHERE status = 'candidate'
         ORDER BY COALESCE(recommended_action_score, -1) DESC,
                  last_checked_at_utc DESC,
                  id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()

    scored: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        result = _score_reply_candidates(conn, reply_target_id=int(row["id"]))
        scored.extend(result.get("scored", []))
        errors.extend(str(error) for error in result.get("errors", []))

    return {
        "ok": not errors,
        "considered": len(rows),
        "scored_count": len(scored),
        "scored": scored,
        "errors": errors,
    }
