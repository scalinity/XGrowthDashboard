"""Read model for the reply queue view (§31.10)."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.agent.tools import _load_engagement_surface_settings
from app.agent.reply_targets import engagement_footnote as _engagement_footnote

def build_reply_queue_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Reply Target Queue view (§29.7) needs."""
    eng_settings = _load_engagement_surface_settings(conn)

    # Counters.
    ctr_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END) AS candidates,
            SUM(CASE WHEN status = 'drafted' THEN 1 ELSE 0 END) AS drafted,
            SUM(CASE WHEN status = 'posted'
                      AND DATE(last_checked_at_utc) = DATE('now') THEN 1 ELSE 0 END) AS posted_today,
            SUM(CASE WHEN status = 'skipped'
                      AND DATE(last_checked_at_utc) = DATE('now') THEN 1 ELSE 0 END) AS skipped_today
        FROM reply_targets
        """
    ).fetchone()
    counters = {
        "candidates": int(ctr_row["candidates"] or 0),
        "drafted": int(ctr_row["drafted"] or 0),
        "posted_today": int(ctr_row["posted_today"] or 0),
        "skipped_today": int(ctr_row["skipped_today"] or 0),
    }

    # Candidate rows (default: status='candidate', sorted by action score).
    rows = conn.execute(
        """SELECT id, target_post_url, target_text, target_author_handle,
                  target_author_follower_count, like_count, reply_count,
                  repost_count, relevance_score, engagement_surface_score,
                  saturation_score, reply_opportunity_score,
                  recommended_action_label, recommended_action_score,
                  score_rationale, pillar, reply_intent, status,
                  discovered_at_utc, discovered_via
           FROM reply_targets
           WHERE status = 'candidate'
           ORDER BY COALESCE(recommended_action_score, -1) DESC,
                    last_checked_at_utc DESC
           LIMIT 50"""
    ).fetchall()

    items = []
    for r in rows:
        handle = (r["target_author_handle"] or "unknown").lstrip("@")
        text = (r["target_text"] or "").strip().replace("\n", " ")
        if len(text) > 220:
            text = text[:219] + "…"
        items.append({
            "id": int(r["id"]),
            "handle": handle,
            "text_excerpt": text or None,
            "target_post_url": r["target_post_url"],
            "like_count": int(r["like_count"] or 0),
            "reply_count": int(r["reply_count"] or 0),
            "repost_count": int(r["repost_count"] or 0),
            "relevance_score": r["relevance_score"],
            "engagement_surface_score": r["engagement_surface_score"],
            "saturation_score": r["saturation_score"],
            "reply_opportunity_score": r["reply_opportunity_score"],
            "recommended_action_label": r["recommended_action_label"],
            "score_rationale": r["score_rationale"],
            "pillar": r["pillar"],
            "reply_intent": r["reply_intent"],
            "discovered_via": r["discovered_via"],
            "engagement_footnote": _engagement_footnote(
                r["target_author_follower_count"], eng_settings
            ),
        })

    return {
        "slice": "reply_queue",
        "counters": counters,
        "items": items,
    }



