"""Read model for the progress view (§31.10)."""

from __future__ import annotations

import sqlite3
from datetime import date as _date_t
from datetime import timedelta as _timedelta
from typing import Any

from app.agent.velocity import get_noise_floor, get_velocity_projection
from app.forms import get_setting

def build_progress_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Progress view (§14.3) needs."""
    # 1. Milestones.
    dist = conn.execute(
        "SELECT * FROM milestones WHERE category = 'distribution' ORDER BY ladder_position ASC"
    ).fetchall()
    val = conn.execute(
        "SELECT * FROM milestones WHERE category = 'validation' ORDER BY ladder_position ASC"
    ).fetchall()

    # 2. Current followers.
    latest = conn.execute(
        "SELECT followers_count FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    current_followers = int(latest["followers_count"]) if latest else None

    # Compute milestone progress for distribution milestones (server-side §31.10).
    dist_out = []
    for m in dist:
        md = dict(m)
        target = m["target_value"]
        start = m["start_value"] or 0
        if m["status"] == "achieved":
            md["progress"] = 1.0
            md["progress_label"] = "achieved"
        elif target and current_followers is not None and target > start:
            p = max(0.0, min(1.0, (current_followers - start) / max(1, target - start)))
            md["progress"] = p
            md["progress_label"] = f"{p * 100:.1f}%"
        else:
            md["progress"] = 0.0
            md["progress_label"] = "not yet"
        dist_out.append(md)

    val_out = []
    for m in val:
        md = dict(m)
        md["progress"] = 1.0 if m["status"] == "achieved" else 0.0
        md["progress_label"] = "achieved" if m["status"] == "achieved" else "not yet"
        val_out.append(md)

    # 3. Velocity projection.
    proj = get_velocity_projection(conn)
    noise_floor = get_noise_floor(conn)

    # 4. Weekly post/reply counts (last 8 ISO weeks).
    today = _date_t.today()
    monday = today - _timedelta(days=today.weekday())
    earliest = (monday - _timedelta(weeks=7)).isoformat()
    latest_date = (monday + _timedelta(days=6)).isoformat()
    week_rows = conn.execute(
        """
        SELECT
            DATE(created_date,
                 '-' || ((CAST(strftime('%w', created_date) AS INTEGER) + 6) % 7)
                 || ' days') AS week_start,
            SUM(CASE WHEN type IN ('standalone','thread_root','thread_child','quote')
                      THEN 1 ELSE 0 END) AS posts,
            SUM(CASE WHEN type = 'reply' THEN 1 ELSE 0 END) AS replies
        FROM posts
        WHERE created_date BETWEEN ? AND ?
        GROUP BY week_start ORDER BY week_start ASC
        """,
        (earliest, latest_date),
    ).fetchall()
    by_week = {r["week_start"]: (int(r["posts"] or 0), int(r["replies"] or 0)) for r in week_rows}
    weekly: list[dict[str, Any]] = []
    for w in range(7, -1, -1):
        ws = (monday - _timedelta(weeks=w)).isoformat()
        posts, replies = by_week.get(ws, (0, 0))
        weekly.append({"week_start": ws, "posts": posts, "replies": replies})

    # 5. Settings.
    post_target = int(get_setting(conn, "daily_post_target", 1) or 1)
    reply_target = int(get_setting(conn, "daily_reply_target", 12) or 12)
    session_target = int(get_setting(conn, "daily_reply_session_target", 1) or 1)
    operational_ceiling = int(get_setting(conn, "operational_ceiling", 5000) or 5000)
    long_arc = int(get_setting(conn, "long_arc_reminder", 500000) or 500000)

    return {
        "slice": "progress",
        "current_followers": current_followers,
        "distribution_milestones": dist_out,
        "validation_milestones": val_out,
        "velocity_projection": proj.to_dict() if proj else None,
        "noise_floor": noise_floor,
        "weekly_counts": weekly,
        "targets": {
            "post_target": post_target,
            "reply_target": reply_target,
            "session_target": session_target,
        },
        "operational_ceiling": operational_ceiling,
        "long_arc_reminder": long_arc,
    }



