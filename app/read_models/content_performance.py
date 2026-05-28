"""Read model for the content performance view (§31.10)."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.components.badges.confidence_label import ui_label_for_db_label
from app.components.charts.lane_grid import confidence_color_for_ui_label, count_rankable_lanes, lane_rows_from_sql

def build_content_performance_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather every data slice the Content Performance view (§14.4) needs."""

    # 1. Lane performance rows.
    raw_rows = conn.execute(
        "SELECT * FROM v_lane_performance ORDER BY post_count DESC"
    ).fetchall()
    lane_data = lane_rows_from_sql(raw_rows)
    rankable_count = count_rankable_lanes(lane_data)

    lanes = []
    for lr in lane_data:
        ui_label = ui_label_for_db_label(lr.db_confidence_label)
        chip_bg = confidence_color_for_ui_label(ui_label)
        # Format median+IQR server-side (§31.10).
        if ui_label == "insufficient" or lr.median_impressions is None:
            median_display = "—"
        elif lr.iqr_low is None or lr.iqr_high is None:
            median_display = f"{int(round(lr.median_impressions)):,}"
        else:
            median_display = (
                f"{int(round(lr.median_impressions)):,} "
                f"[{int(round(lr.iqr_low)):,}–{int(round(lr.iqr_high)):,}]"
            )
        lanes.append({
            "pillar": lr.pillar, "audience": lr.audience, "cta": lr.cta,
            "post_count": lr.post_count, "days_covered": lr.days_covered,
            "median_display": median_display,
            "median_impressions": lr.median_impressions,
            "iqr_low": lr.iqr_low, "iqr_high": lr.iqr_high,
            "total_bookmarks": lr.total_bookmarks,
            "total_replies": lr.total_replies,
            "stir_signal_count": lr.stir_signal_count,
            "ui_label": ui_label, "chip_bg": chip_bg,
        })

    # 2. Best lane (§14.4 anti-overfitting gate).
    best = None
    if rankable_count >= 3:
        rankable = [
            lr for lr in lane_data
            if ui_label_for_db_label(lr.db_confidence_label) in {"tentative", "confident"}
            and lr.median_impressions is not None
        ]
        if rankable:
            b = max(rankable, key=lambda r: r.median_impressions or 0)
            ui_label = ui_label_for_db_label(b.db_confidence_label)
            best = {
                "lane": f"{b.pillar} · {b.audience} · {b.cta}",
                "median_impressions": int(b.median_impressions or 0),
                "iqr_low": int(b.iqr_low or 0), "iqr_high": int(b.iqr_high or 0),
                "ui_label": ui_label,
                "chip_bg": confidence_color_for_ui_label(ui_label),
            }

    # 3. V/G/P/P content type table.
    ct_rows = conn.execute(
        """SELECT content_type, post_count, days_covered,
                  median_impressions, iqr_impressions_low, iqr_impressions_high,
                  median_engagement_rate, confidence_label
           FROM v_content_type_performance
           ORDER BY CASE content_type
              WHEN 'value' THEN 0 WHEN 'growth' THEN 1
              WHEN 'personality' THEN 2 WHEN 'proof' THEN 3 ELSE 9 END"""
    ).fetchall()
    content_types = []
    for r in ct_rows:
        ul = ui_label_for_db_label(r["confidence_label"] or "insufficient sample")
        content_types.append({
            "content_type": r["content_type"],
            "post_count": int(r["post_count"] or 0),
            "days_covered": int(r["days_covered"] or 0),
            "median_impressions": r["median_impressions"],
            "median_engagement_rate": r["median_engagement_rate"],
            "ui_label": ul,
            "chip_bg": confidence_color_for_ui_label(ul),
        })

    # 4. Pre-publish scorer calibration.
    cal_rows = conn.execute(
        """SELECT ps.composite_label, COUNT(*) AS n,
                  AVG(plm.impressions) AS avg_impressions,
                  AVG(plm.engagement_rate) AS avg_engagement_rate,
                  AVG(ps.screenshot_test_score) AS avg_screenshot_test_score,
                  COUNT(ps.screenshot_test_score) AS n_with_screenshot_score
           FROM agent_drafts ad
           JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
           JOIN posts p ON p.id = ad.final_post_id
           JOIN v_post_latest_metrics plm ON plm.post_id = p.id
           WHERE p.manual_confirmation_status = 'confirmed' AND plm.impressions IS NOT NULL
           GROUP BY ps.composite_label
           ORDER BY CASE ps.composite_label
             WHEN 'strong' THEN 0 WHEN 'viable' THEN 1 WHEN 'weak' THEN 2 ELSE 3 END
           LIMIT 10"""
    ).fetchall()
    calibration = [dict(r) for r in cal_rows]

    return {
        "slice": "content_performance",
        "lanes": lanes,
        "rankable_count": rankable_count,
        "best_lane": best,
        "content_types": content_types,
        "calibration": calibration,
    }



