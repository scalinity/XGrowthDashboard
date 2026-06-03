"""Regression: weekly review summary must expose follower bookends for native save."""

from __future__ import annotations

from datetime import date, timedelta

from app.db import apply_migrations, connect
from app.read_models.weekly_review import build_weekly_review_read_model
from scripts.seed_settings import seed_settings


def test_summary_includes_follower_bookends_for_native_save() -> None:
    conn = connect(":memory:")
    apply_migrations(conn)
    seed_settings(conn)
    try:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        for snap_date, followers in (
            (week_start.isoformat(), 100),
            (week_end.isoformat(), 150),
        ):
            conn.execute(
                """
                INSERT INTO account_snapshots
                  (snapshot_date, collected_at_utc, username, profile_url,
                   followers_count, following_count, post_count, listed_count,
                   baseline_followers, source, data_quality)
                VALUES (?, ?, 'u', 'https://x.com/u', ?, 0, 0, 0, ?, 'manual', 'exact')
                """,
                (snap_date, f"{snap_date}T09:00:00Z", followers, followers),
            )
        conn.commit()

        model = build_weekly_review_read_model(conn)
        summary = model["summary"]
        assert summary["followers_start"] == 100
        assert summary["followers_end"] == 150
        assert summary["follower_delta"] == 50
    finally:
        conn.close()
