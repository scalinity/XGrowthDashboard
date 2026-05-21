"""View-level tests for Phase 1 (spec.md §11, §13).

The marquee test set is the v_lane_performance confidence-label boundary
sweep at post counts 4, 5, 14, 15, 29, 30 — these prove the graduated
sample-size rules from §11 fire exactly where they should.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _insert_post_with_lane(
    conn: sqlite3.Connection,
    *,
    pillar: str,
    audience: str,
    cta: str,
    created_date: str,
    impressions: int,
    idx: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO posts
          (x_post_id, created_at_utc, created_date, text, type,
           posted_via, manual_confirmation_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"xpost_{idx}",
            f"{created_date}T12:00:00Z",
            created_date,
            f"sample post {idx}",
            "standalone",
            "manual",
            "confirmed",
        ),
    )
    post_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO post_classifications
          (post_id, pillar, audience, cta)
        VALUES (?, ?, ?, ?)
        """,
        (post_id, pillar, audience, cta),
    )
    conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, impressions, likes,
           replies, reposts, quotes, bookmarks, source, data_quality)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            f"xpost_{idx}",
            f"{created_date}T13:00:00Z",
            impressions,
            5, 1, 0, 0, 2,
            "manual",
            "manual",
        ),
    )
    return post_id


def _seed_lane(
    conn: sqlite3.Connection,
    *,
    pillar: str,
    audience: str,
    cta: str,
    post_count: int,
    days_covered: int,
    impressions_seq: list[int] | None = None,
) -> None:
    """Insert `post_count` posts across `days_covered` distinct dates.

    The first `days_covered` posts each land on a unique date; remaining
    posts pile onto the last date. Impressions default to 100 * idx so
    the median/IQR functions have something to chew on.
    """
    assert post_count >= days_covered >= 1
    base = date(2026, 5, 1)
    impressions_seq = impressions_seq or [100 * (i + 1) for i in range(post_count)]
    assert len(impressions_seq) == post_count
    for i in range(post_count):
        # First `days_covered` posts span unique days; the rest pile on day-(days_covered-1).
        day_offset = i if i < days_covered else days_covered - 1
        d = (base + timedelta(days=day_offset)).isoformat()
        _insert_post_with_lane(
            conn,
            pillar=pillar,
            audience=audience,
            cta=cta,
            created_date=d,
            impressions=impressions_seq[i],
            idx=i + 1,
        )


def _fetch_lane(conn: sqlite3.Connection, pillar: str, audience: str, cta: str):
    return conn.execute(
        """
        SELECT post_count, days_covered, median_impressions,
               iqr_impressions_low, iqr_impressions_high, confidence_label
        FROM v_lane_performance
        WHERE pillar = ? AND audience = ? AND cta = ?
        """,
        (pillar, audience, cta),
    ).fetchone()


# ---------------------------------------------------------------------------
# v_lane_performance — boundary sweep on confidence_label (§11 / §13).
# The phase prompt names these six sample sizes explicitly as the
# load-bearing test in this phase.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "post_count, days_covered, expected_label",
    [
        # Below the 5-post floor → insufficient regardless of days.
        (4,  4,  "insufficient sample"),
        # 5..14 posts → "low" (directional only).
        (5,  5,  "low — show scatter, do not rank"),
        (14, 7,  "low — show scatter, do not rank"),
        # 15..29 posts with 7+ days → "moderate".
        (15, 7,  "moderate"),
        (29, 7,  "moderate"),
        # 30+ posts AND 14+ days → "stronger".
        (30, 14, "stronger"),
    ],
)
def test_lane_performance_confidence_label_boundaries(
    empty_db_conn: sqlite3.Connection,
    post_count: int,
    days_covered: int,
    expected_label: str,
) -> None:
    _seed_lane(
        empty_db_conn,
        pillar="stir",
        audience="icp",
        cta="ask",
        post_count=post_count,
        days_covered=days_covered,
    )
    row = _fetch_lane(empty_db_conn, "stir", "icp", "ask")
    assert row is not None, "lane should appear in v_lane_performance"
    assert row["post_count"] == post_count
    assert row["days_covered"] == days_covered
    assert row["confidence_label"] == expected_label


def test_lane_performance_insufficient_when_days_below_three(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """Even with high post counts, days_covered < 3 → insufficient sample."""
    _seed_lane(
        empty_db_conn,
        pillar="build",
        audience="other",
        cta="ask",
        post_count=20,
        days_covered=2,
    )
    row = _fetch_lane(empty_db_conn, "build", "other", "ask")
    assert row is not None
    assert row["confidence_label"] == "insufficient sample"


def test_lane_performance_falls_back_to_moderate_when_days_below_seven(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """15+ posts but only 3..6 days → the trailing ELSE branch fires ("moderate")."""
    _seed_lane(
        empty_db_conn,
        pillar="self",
        audience="icp",
        cta="none",
        post_count=15,
        days_covered=4,
    )
    row = _fetch_lane(empty_db_conn, "self", "icp", "none")
    assert row is not None
    assert row["confidence_label"] == "moderate"


def test_lane_performance_median_and_iqr_are_sensible(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """Sanity-check the percentile aggregate at a known distribution."""
    impressions = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    _seed_lane(
        empty_db_conn,
        pillar="stir",
        audience="other",
        cta="none",
        post_count=10,
        days_covered=10,
        impressions_seq=impressions,
    )
    row = _fetch_lane(empty_db_conn, "stir", "other", "none")
    assert row is not None
    # Linear-interpolation PERCENTILE_CONT on [100..1000] step 100:
    #   p=0.5  → 550
    #   p=0.25 → 325
    #   p=0.75 → 775
    assert row["median_impressions"] == pytest.approx(550)
    assert row["iqr_impressions_low"] == pytest.approx(325)
    assert row["iqr_impressions_high"] == pytest.approx(775)


def test_lane_performance_uses_latest_classification_per_post(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """A post reclassified mid-stream counts once, against the latest lane."""
    _seed_lane(
        empty_db_conn,
        pillar="stir",
        audience="icp",
        cta="ask",
        post_count=5,
        days_covered=5,
    )
    # Reclassify post 1 — newer classified_at wins.
    empty_db_conn.execute(
        """
        INSERT INTO post_classifications
          (post_id, pillar, audience, cta, classified_at)
        VALUES (1, 'build', 'other', 'none', '2099-01-01T00:00:00Z')
        """
    )
    rows = empty_db_conn.execute(
        "SELECT pillar, audience, cta, post_count FROM v_lane_performance ORDER BY pillar"
    ).fetchall()
    by_lane = {(r["pillar"], r["audience"], r["cta"]): r["post_count"] for r in rows}
    assert by_lane.get(("build", "other", "none")) == 1
    assert by_lane.get(("stir", "icp", "ask")) == 4


def test_lane_performance_stir_signal_follows_latest_classification(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """A reclassified post's stir_signal counts only against the NEW lane.

    Regression for the bug where the stir_signal_count subquery joined
    post_classifications directly (all historical rows), so an event attached
    to a reclassified post would be counted in every historical lane.
    """
    # Post 1 starts in (stir, icp, ask)…
    _insert_post_with_lane(
        empty_db_conn,
        pillar="stir", audience="icp", cta="ask",
        created_date="2026-05-01", impressions=100, idx=1,
    )
    # …then gets reclassified to (build, other, none) at a later timestamp.
    empty_db_conn.execute(
        """
        INSERT INTO post_classifications
          (post_id, pillar, audience, cta, classified_at)
        VALUES (1, 'build', 'other', 'none', '2099-01-01T00:00:00Z')
        """
    )
    # Seed 5 unrelated posts in (stir, icp, ask) so that lane appears in
    # v_lane_performance (otherwise post 1's old lane wouldn't show up at all).
    base = date(2026, 5, 2)
    for i in range(5):
        d = (base + timedelta(days=i)).isoformat()
        _insert_post_with_lane(
            empty_db_conn,
            pillar="stir", audience="icp", cta="ask",
            created_date=d, impressions=100, idx=100 + i,
        )
    # Insert a stir event referencing post 1.
    empty_db_conn.execute(
        """
        INSERT INTO stir_conversion_events
          (occurred_at_utc, event_date, event_category, event_type,
           attribution_method, source_data_quality, referring_post_id)
        VALUES ('2026-06-01T00:00:00Z', '2026-06-01',
                'acquisition', 'download',
                'self_reported', 'manual', 1)
        """
    )
    rows = empty_db_conn.execute(
        "SELECT pillar, audience, cta, stir_signal_count FROM v_lane_performance"
    ).fetchall()
    by_lane = {(r["pillar"], r["audience"], r["cta"]): r["stir_signal_count"] for r in rows}
    # Post 1 now belongs to (build, other, none); its signal counts only there.
    assert by_lane.get(("build", "other", "none")) == 1
    assert by_lane.get(("stir", "icp", "ask")) == 0


def test_lane_performance_excludes_unclassified_posts(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """Posts without a classification row must not appear in any lane."""
    empty_db_conn.execute(
        """
        INSERT INTO posts
          (x_post_id, created_at_utc, created_date, text, type,
           posted_via, manual_confirmation_status)
        VALUES ('xorphan', '2026-05-01T12:00:00Z', '2026-05-01',
                'unclassified', 'standalone', 'manual', 'confirmed')
        """
    )
    empty_db_conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, impressions, source, data_quality)
        VALUES (1, 'xorphan', '2026-05-01T13:00:00Z', 1234, 'manual', 'manual')
        """
    )
    rows = empty_db_conn.execute(
        "SELECT COUNT(*) FROM v_lane_performance"
    ).fetchone()[0]
    assert rows == 0


# ---------------------------------------------------------------------------
# v_daily_reps — counts replies vs original posts per day.
# ---------------------------------------------------------------------------
def test_daily_reps_exposes_counts_and_target_flags(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        """
        INSERT INTO daily_activity
          (activity_date, planned_posts, planned_replies,
           posts_shipped, replies_shipped, quotes_shipped,
           reply_sessions_completed, minimum_reps_completed)
        VALUES ('2026-05-21', 1, 12, 2, 12, 0, 1, 1)
        """
    )
    row = db_conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = '2026-05-21'"
    ).fetchone()
    assert row is not None
    assert row["posts_shipped"] == 2
    assert row["replies_shipped"] == 12
    assert row["reply_sessions_completed"] == 1
    assert row["post_target_met"] == 1
    assert row["reply_target_met"] == 1
    assert row["session_target_met"] == 1


def test_daily_reps_target_unmet_flags(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        """
        INSERT INTO daily_activity
          (activity_date, planned_posts, planned_replies,
           posts_shipped, replies_shipped, quotes_shipped,
           reply_sessions_completed, minimum_reps_completed)
        VALUES ('2026-05-22', 1, 12, 0, 4, 0, 0, 0)
        """
    )
    row = db_conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = '2026-05-22'"
    ).fetchone()
    assert row["post_target_met"] == 0
    assert row["reply_target_met"] == 0
    assert row["session_target_met"] == 0


def test_daily_reps_counts_match_post_table(db_conn: sqlite3.Connection) -> None:
    """Posts vs replies in the underlying posts table are independent of the
    daily_activity counter; v_daily_reps surfaces the daily_activity counts.
    This test asserts the view reflects entered values verbatim — not derived."""
    db_conn.execute(
        """
        INSERT INTO daily_activity (activity_date, posts_shipped, replies_shipped)
        VALUES ('2026-05-23', 3, 7)
        """
    )
    # Independently insert 3 standalone + 7 reply rows for the same date in posts.
    for i in range(3):
        db_conn.execute(
            """
            INSERT INTO posts
              (x_post_id, created_at_utc, created_date, text, type,
               posted_via, manual_confirmation_status)
            VALUES (?, ?, '2026-05-23', 'post', 'standalone', 'manual', 'confirmed')
            """,
            (f"post_p{i}", f"2026-05-23T1{i}:00:00Z"),
        )
    for i in range(7):
        db_conn.execute(
            """
            INSERT INTO posts
              (x_post_id, created_at_utc, created_date, text, type,
               posted_via, manual_confirmation_status)
            VALUES (?, ?, '2026-05-23', 'reply', 'reply', 'manual', 'confirmed')
            """,
            (f"post_r{i}", f"2026-05-23T1{i}:00:00Z"),
        )
    row = db_conn.execute(
        "SELECT posts_shipped, replies_shipped FROM v_daily_reps WHERE activity_date='2026-05-23'"
    ).fetchone()
    assert row["posts_shipped"] == 3
    assert row["replies_shipped"] == 7
    # Cross-check with the raw posts table:
    raw_posts = db_conn.execute(
        "SELECT COUNT(*) FROM posts WHERE created_date='2026-05-23' AND type='standalone'"
    ).fetchone()[0]
    raw_replies = db_conn.execute(
        "SELECT COUNT(*) FROM posts WHERE created_date='2026-05-23' AND type='reply'"
    ).fetchone()[0]
    assert raw_posts == 3
    assert raw_replies == 7


# ---------------------------------------------------------------------------
# v_funnel_daily — aggregation + App-Store attribution gap.
# ---------------------------------------------------------------------------
def test_funnel_daily_aggregates_by_date_and_event_type(
    db_conn: sqlite3.Connection,
) -> None:
    events = [
        ("acquisition", "site_visit",    "self_reported", None),
        ("acquisition", "site_visit",    "utm",            None),
        ("acquisition", "link_click",    "utm",            None),
        ("acquisition", "download",      "self_reported",  1),
        ("acquisition", "download",      "utm",            None),
        ("activation",  "kitchen_scan",  "self_reported",  1),
        ("usage",       "cook_mode_started", "self_reported", 1),
    ]
    for cat, evtype, method, icp in events:
        db_conn.execute(
            """
            INSERT INTO stir_conversion_events
              (occurred_at_utc, event_date, event_category, event_type,
               attribution_method, is_likely_icp, source_data_quality)
            VALUES (?, '2026-05-21', ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-21T12:00:00Z",
                cat, evtype, method, icp, "manual",
            ),
        )
    row = db_conn.execute(
        "SELECT * FROM v_funnel_daily WHERE event_date='2026-05-21'"
    ).fetchone()
    assert row is not None
    assert row["getstir_visits"] == 2
    assert row["link_clicks"] == 1
    # Downloads include BOTH self_reported and utm — no inference filter.
    assert row["downloads"] == 2
    assert row["kitchen_scans"] == 1
    assert row["cook_mode_started"] == 1
    # qualified_icp_testers only counts the self_reported + is_likely_icp combo.
    assert row["qualified_icp_testers"] == 3  # download + scan + cook_mode


def test_funnel_daily_app_store_attribution_gap_is_visible(
    db_conn: sqlite3.Connection,
) -> None:
    """§14.5 / §18: no inferred download attribution. Downloads with
    attribution_method='inferred' still count in `downloads` (the gap is
    documented in the UI, not silently filtered)."""
    db_conn.execute(
        """
        INSERT INTO stir_conversion_events
          (occurred_at_utc, event_date, event_category, event_type,
           attribution_method, source_data_quality)
        VALUES ('2026-05-21T12:00:00Z', '2026-05-21',
                'acquisition', 'download',
                'inferred', 'inferred')
        """
    )
    row = db_conn.execute(
        "SELECT downloads, qualified_icp_testers FROM v_funnel_daily WHERE event_date='2026-05-21'"
    ).fetchone()
    assert row["downloads"] == 1
    # But the inferred event cannot count toward qualified_icp_testers
    # (the CHECK constraint and the view's filter both refuse it).
    assert row["qualified_icp_testers"] == 0


def test_post_latest_metrics_picks_latest_snapshot_per_post(
    empty_db_conn: sqlite3.Connection,
) -> None:
    empty_db_conn.execute(
        """
        INSERT INTO posts
          (x_post_id, created_at_utc, created_date, text, type,
           posted_via, manual_confirmation_status)
        VALUES ('xlatest', '2026-05-20T12:00:00Z', '2026-05-20',
                'hello', 'standalone', 'manual', 'confirmed')
        """
    )
    empty_db_conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, impressions, source, data_quality)
        VALUES (1, 'xlatest', '2026-05-20T13:00:00Z', 100, 'manual', 'manual')
        """
    )
    empty_db_conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, impressions, source, data_quality)
        VALUES (1, 'xlatest', '2026-05-21T13:00:00Z', 500, 'manual', 'manual')
        """
    )
    row = empty_db_conn.execute(
        "SELECT impressions, latest_metrics_collected_at FROM v_post_latest_metrics WHERE post_id = 1"
    ).fetchone()
    assert row["impressions"] == 500


def test_post_latest_metrics_rates_are_null_when_impressions_missing(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """§13 rule: rates display N/A when impressions IS NULL or 0, never 0."""
    empty_db_conn.execute(
        """
        INSERT INTO posts
          (x_post_id, created_at_utc, created_date, text, type,
           posted_via, manual_confirmation_status)
        VALUES ('xnoimp', '2026-05-20T12:00:00Z', '2026-05-20',
                'no impressions known', 'standalone', 'manual', 'confirmed')
        """
    )
    empty_db_conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, impressions, likes,
           source, data_quality)
        VALUES (1, 'xnoimp', '2026-05-20T13:00:00Z', NULL, 5, 'manual', 'manual')
        """
    )
    row = empty_db_conn.execute(
        "SELECT engagement_rate, bookmark_rate, reply_rate, link_click_rate FROM v_post_latest_metrics WHERE post_id = 1"
    ).fetchone()
    assert row["engagement_rate"] is None
    assert row["bookmark_rate"] is None
    assert row["reply_rate"] is None
    assert row["link_click_rate"] is None


def test_account_daily_computes_deltas_and_distances(
    db_conn: sqlite3.Connection,
) -> None:
    base_args = {
        "username": "dannyscalant",
        "profile_url": "https://x.com/dannyscalant",
        "following_count": 100,
        "post_count": 0,
        "listed_count": 0,
        "baseline_followers": 61,
        "source": "manual",
        "data_quality": "manual",
    }
    for offset, followers in [(0, 61), (1, 64), (2, 70)]:
        d = (date(2026, 5, 1) + timedelta(days=offset)).isoformat()
        db_conn.execute(
            """
            INSERT INTO account_snapshots
              (snapshot_date, collected_at_utc, username, profile_url,
               followers_count, following_count, post_count, listed_count,
               baseline_followers, source, data_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d, f"{d}T09:00:00Z",
                base_args["username"], base_args["profile_url"],
                followers,
                base_args["following_count"], base_args["post_count"], base_args["listed_count"],
                base_args["baseline_followers"],
                base_args["source"], base_args["data_quality"],
            ),
        )
    rows = db_conn.execute(
        "SELECT snapshot_date, followers_count, delta_vs_yesterday, delta_vs_baseline, distance_to_current_milestone, distance_to_operational_ceiling, distance_to_long_arc FROM v_account_daily ORDER BY snapshot_date"
    ).fetchall()
    assert [r["followers_count"] for r in rows] == [61, 64, 70]
    assert [r["delta_vs_baseline"] for r in rows] == [0, 3, 9]
    # delta_vs_yesterday: first day NULL, then +3, then +6
    assert rows[0]["delta_vs_yesterday"] is None
    assert rows[1]["delta_vs_yesterday"] == 3
    assert rows[2]["delta_vs_yesterday"] == 6
    # current_milestone=100, operational_ceiling=5000, long_arc=500000 from seed
    assert rows[2]["distance_to_current_milestone"] == 100 - 70
    assert rows[2]["distance_to_operational_ceiling"] == 5000 - 70
    assert rows[2]["distance_to_long_arc"] == 500_000 - 70


def test_account_daily_deltas_are_calendar_day_aware(
    db_conn: sqlite3.Connection,
) -> None:
    """A missed snapshot day must not silently shift delta_vs_yesterday or delta_7d.

    Regression for the bug where LAG(N) returned the value N *rows* back in
    the partition order, so skipping a calendar day made delta_vs_yesterday
    compare against the day-before-the-gap snapshot.
    """
    def _insert(d: str, followers: int) -> None:
        db_conn.execute(
            """
            INSERT INTO account_snapshots
              (snapshot_date, collected_at_utc, username, profile_url,
               followers_count, following_count, post_count, listed_count,
               baseline_followers, source, data_quality)
            VALUES (?, ?, 'dannyscalant', 'https://x.com/dannyscalant',
                    ?, 100, 0, 0, 61, 'manual', 'manual')
            """,
            (d, f"{d}T09:00:00Z", followers),
        )

    # Day 0, then SKIP day 1, then day 2.
    _insert("2026-05-01", 61)
    _insert("2026-05-03", 65)

    row = db_conn.execute(
        "SELECT delta_vs_yesterday FROM v_account_daily WHERE snapshot_date='2026-05-03'"
    ).fetchone()
    # 2026-05-02 has no snapshot → calendar-day-aware delta must be NULL.
    # (LAG(1)-by-rows would incorrectly return 4 = 65 - 61.)
    assert row["delta_vs_yesterday"] is None


def test_account_daily_delta_7d_skips_missing_calendar_days(
    db_conn: sqlite3.Connection,
) -> None:
    """delta_7d must be NULL when the calendar day 7 days prior is missing,
    even if 7+ snapshots exist on other dates."""
    def _insert(d: str, followers: int) -> None:
        db_conn.execute(
            """
            INSERT INTO account_snapshots
              (snapshot_date, collected_at_utc, username, profile_url,
               followers_count, following_count, post_count, listed_count,
               baseline_followers, source, data_quality)
            VALUES (?, ?, 'dannyscalant', 'https://x.com/dannyscalant',
                    ?, 100, 0, 0, 61, 'manual', 'manual')
            """,
            (d, f"{d}T09:00:00Z", followers),
        )

    # Insert 7 consecutive snapshots, then jump ahead — day-N has no snapshot
    # for "current - 7 days".
    base = date(2026, 5, 1)
    for offset in range(7):
        _insert((base + timedelta(days=offset)).isoformat(), 61 + offset)
    # Today is base + 20 days; base + 13 days has no snapshot.
    _insert((base + timedelta(days=20)).isoformat(), 100)

    row = db_conn.execute(
        "SELECT delta_7d FROM v_account_daily WHERE snapshot_date = ?",
        ((base + timedelta(days=20)).isoformat(),),
    ).fetchone()
    assert row["delta_7d"] is None


def test_account_daily_applies_corrections(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        """
        INSERT INTO account_snapshots
          (snapshot_date, collected_at_utc, username, profile_url,
           followers_count, following_count, post_count, listed_count,
           baseline_followers, source, data_quality)
        VALUES ('2026-05-10', '2026-05-10T09:00:00Z',
                'dannyscalant', 'https://x.com/dannyscalant',
                63, 100, 0, 0, 61, 'manual', 'manual')
        """
    )
    db_conn.execute(
        """
        INSERT INTO account_snapshot_corrections
          (snapshot_id, field_name, old_value, new_value, reason)
        VALUES (1, 'followers_count', '63', '64', 'screenshot reconciliation')
        """
    )
    row = db_conn.execute(
        "SELECT followers_count FROM v_account_daily WHERE snapshot_date='2026-05-10'"
    ).fetchone()
    assert row["followers_count"] == 64
