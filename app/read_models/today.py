"""Read model for the today view (§31.10)."""

from __future__ import annotations

import sqlite3
import time
from datetime import date as _date_t
from typing import Any

from app.agent.content_types import get_content_type_gaps, get_recommendation_window_days
from app.agent.velocity import get_noise_floor
from app.forms import get_setting

def build_today_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Today view (§14.1) needs.

    All business logic stays here (§31.10: "no business logic in the frontend"):
    milestone progress pct, velocity gating, high-engagement mix target, text
    preview truncation. The frontend only renders.
    """
    today_iso = _date_t.today().isoformat()
    api_snapshot_date = time.strftime("%Y-%m-%d", time.gmtime())

    # 1. Today's snapshot row from v_account_daily.
    # API snapshots use UTC calendar date; manual entry uses local date — try both.
    snap = conn.execute(
        "SELECT * FROM v_account_daily WHERE snapshot_date = ?",
        (api_snapshot_date,),
    ).fetchone()
    if snap is None and api_snapshot_date != today_iso:
        snap = conn.execute(
            "SELECT * FROM v_account_daily WHERE snapshot_date = ?",
            (today_iso,),
        ).fetchone()
    snapshot = dict(snap) if snap else None

    # 2. Baseline + milestone.
    baseline = int(get_setting(conn, "baseline_followers", 61) or 61)
    target = int(get_setting(conn, "current_milestone", 100) or 100)
    ms_row = conn.execute(
        "SELECT * FROM milestones WHERE category = 'distribution' AND target_value = ? LIMIT 1",
        (target,),
    ).fetchone()
    milestone = dict(ms_row) if ms_row else None
    milestone_progress_pct: float | None = None
    if snapshot and milestone:
        start = int(milestone.get("start_value") or baseline)
        end = int(milestone.get("target_value") or target)
        foll = int(snapshot["followers_count"])
        milestone_progress_pct = max(0.0, min(1.0, (foll - start) / max(1, end - start)))

    # 3. Velocity gating (§13 rule 6). Uses get_noise_floor so the threshold
    # is consistent with Progress view (RV5-C3 fix — was hardcoded at 10).
    delta_7d = snapshot["delta_7d"] if snapshot else None
    noise_floor = get_noise_floor(conn)
    velocity_measurable = delta_7d is not None and abs(delta_7d) >= noise_floor
    velocity_7d = snapshot["velocity_7d_per_day"] if snapshot else None

    # 4. Content-type recommendation (§28.17).
    ct_window = get_recommendation_window_days(conn)
    ct_gap = get_content_type_gaps(conn, window_days=ct_window)

    # 5. Daily reps + §29.9 mix.
    reps_snap = conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = ?", (today_iso,)
    ).fetchone()
    reps_row = dict(reps_snap) if reps_snap else None
    post_target = int(get_setting(conn, "daily_post_target", 1) or 1)
    reply_target_val = int(get_setting(conn, "daily_reply_target", 12) or 12)
    session_target = int(get_setting(conn, "daily_reply_session_target", 1) or 1)
    high_eng_mix_pct = float(get_setting(conn, "reply_high_engagement_mix_pct", 0.5) or 0.5)
    cand_review_target = int(get_setting(conn, "reply_candidate_review_daily_target", 15) or 15)
    mix: dict[str, Any] = {}
    if reps_row:
        replies_shipped = int(reps_row.get("replies_shipped") or 0)
        high_eng = int(reps_row.get("high_engagement_replies_shipped") or 0)
        high_eng_target = max(1, int(round(high_eng_mix_pct * max(1, replies_shipped))))
        mix = {
            "high_eng": high_eng,
            "icp_intent": int(reps_row.get("icp_intent_replies_shipped") or 0),
            "candidates_rev": int(reps_row.get("candidates_reviewed_today") or 0),
            "high_eng_target": high_eng_target,
            "high_eng_met": replies_shipped > 0 and high_eng >= high_eng_target,
            "cand_target": cand_review_target,
            "cand_met": int(reps_row.get("candidates_reviewed_today") or 0) >= cand_review_target,
        }

    # 6. Pending agent drafts (today, proposed).
    pending_rows = conn.execute(
        """
        SELECT ad.id, ad.text, ad.draft_kind, ad.created_at,
               ad.similarity_warning_json,
               ps.composite_label
        FROM agent_drafts ad
        LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
        WHERE date(ad.created_at) = ?
          AND ad.status = 'proposed'
        ORDER BY ad.id DESC LIMIT 5
        """,
        (today_iso,),
    ).fetchall()
    pending_drafts = []
    for d in pending_rows:
        preview = (d["text"] or "").strip().replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:157] + "…"
        pending_drafts.append({
            "id": d["id"],
            "text_preview": preview,
            "draft_kind": d["draft_kind"],
            "composite_label": d["composite_label"],
            "similarity_warning_json": d["similarity_warning_json"],
        })

    # 7. Recent posts (today, last 5).
    recent_rows = conn.execute(
        """
        SELECT p.id, p.created_at_utc, p.text, p.type,
               p.manual_confirmation_status,
               pc.pillar, pc.audience, pc.cta
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        WHERE p.created_date = ?
        ORDER BY p.created_at_utc DESC LIMIT 5
        """,
        (today_iso,),
    ).fetchall()
    recent_posts = []
    for r in recent_rows:
        preview = (r["text"] or "").strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "…"
        recent_posts.append({
            "id": r["id"],
            "type": r["type"],
            "text_preview": preview,
            "pillar": r["pillar"],
            "audience": r["audience"],
            "cta": r["cta"],
            "confirm_status": r["manual_confirmation_status"],
        })

    # 8. Snapshot form defaults (so frontend can pre-fill without another call).
    snap_defaults = {
        "username": get_setting(conn, "x_handle", "") or "",
        "profile_url": get_setting(conn, "profile_url", "") or "",
        "baseline_followers": baseline,
        "x_user_id": get_setting(conn, "x_user_id"),
    }

    # S2: removed unused account_last_7 query (no frontend consumer).

    # 9. Follower sparkline (last 14 days for the Today dashboard mini-chart).
    sparkline_rows = conn.execute(
        """SELECT snapshot_date, followers_count
           FROM v_account_daily
           ORDER BY snapshot_date DESC LIMIT 14"""
    ).fetchall()
    follower_sparkline = [
        {"date": r["snapshot_date"], "count": int(r["followers_count"])}
        for r in reversed(sparkline_rows)
    ]

    return {
        "slice": "today",
        "today_iso": today_iso,
        "snapshot": snapshot,
        "baseline_followers": baseline,
        "current_milestone_target": target,
        "milestone": milestone,
        "milestone_progress_pct": milestone_progress_pct,
        "velocity_measurable": velocity_measurable,
        "velocity_7d_per_day": velocity_7d,
        "content_type_reco": {
            "under_represented": ct_gap.get("under_represented"),
            "rationale": ct_gap.get("rationale", ""),
        },
        "daily_reps": {
            "row": reps_row,
            "targets": {
                "post_target": post_target,
                "reply_target": reply_target_val,
                "session_target": session_target,
                "high_engagement_mix_pct": high_eng_mix_pct,
                "candidate_review_daily_target": cand_review_target,
            },
            "mix": mix,
        },
        "pending_drafts": pending_drafts,
        "recent_posts": recent_posts,
        "snapshot_defaults": snap_defaults,
        "follower_sparkline": follower_sparkline,
    }



