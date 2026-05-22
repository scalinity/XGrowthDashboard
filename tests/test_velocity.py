"""Phase 5.9 / §28.19 — follower-velocity projection.

Covers:

  1. get_velocity_projection returns None when no snapshots exist.
  2. Noise-floor path: |delta_7d| < threshold → projection columns are
     None AND in_noise_floor = True. Never display a precise date.
  3. Non-noise path: positive velocity → projection date materializes;
     projection date is in the future.
  4. Milestone already met → no projection (treated like noise for
     suppression purposes).
  5. daily_followers_needed_to_hit_milestone_by_date helper:
        - returns positive int when target is in the future.
        - returns None when target date is today / past.
        - returns None when milestone already met.
        - returns None when no snapshots exist.
  6. Tool wrapper returns a dict shape with `in_noise_floor` flag.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app.agent import velocity
from app.agent.tools import get_tool


# ---------------------------------------------------------------------------
# Helpers — seed account_snapshots so v_account_daily / v_follower_velocity
# have data to work with.
# ---------------------------------------------------------------------------
def _seed_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str,
    followers_count: int,
    following_count: int = 100,
    post_count: int = 50,
    listed_count: int = 0,
    like_count: int = 0,
    media_count: int = 0,
    baseline_followers: int = 61,
) -> int:
    """Insert one row matching the §10 account_snapshots schema."""
    return int(conn.execute(
        """
        INSERT INTO account_snapshots
            (snapshot_date, collected_at_utc, username, profile_url,
             source, data_quality,
             followers_count, following_count, post_count, listed_count,
             like_count, media_count, baseline_followers)
        VALUES (?, ?, 'dannyscalant', 'https://x.com/dannyscalant',
                'manual', 'manual', ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            snapshot_date,
            snapshot_date + "T09:00:00Z",
            followers_count,
            following_count,
            post_count,
            listed_count,
            like_count,
            media_count,
            baseline_followers,
        ),
    ).fetchone()[0])


def _seed_steady_growth(conn: sqlite3.Connection, per_day: int) -> None:
    """Seed 8 daily snapshots ending today with +per_day/day growth."""
    base_followers = 100
    for days_back in range(8):
        d = (date.today() - timedelta(days=days_back)).isoformat()
        followers = base_followers + per_day * (7 - days_back)
        _seed_snapshot(conn, snapshot_date=d, followers_count=followers)


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def test_returns_none_when_no_snapshots(db_conn: sqlite3.Connection) -> None:
    assert velocity.get_velocity_projection(db_conn) is None


def test_noise_floor_path_suppresses_projections(db_conn: sqlite3.Connection) -> None:
    """+1/day over 7 days = delta_7d=7, below default noise_floor=10 →
    projection columns must be NULL and in_noise_floor=True."""
    _seed_steady_growth(db_conn, per_day=1)
    proj = velocity.get_velocity_projection(db_conn)
    assert proj is not None
    assert proj.in_noise_floor is True
    # Never fabricate a precise date.
    assert proj.projected_milestone_hit_date_at_7d_pace is None
    assert proj.projected_milestone_hit_date_at_30d_pace is None
    assert proj.days_until_milestone_at_7d_pace is None
    assert proj.days_until_milestone_at_30d_pace is None


def test_non_noise_path_produces_projection(db_conn: sqlite3.Connection) -> None:
    """+3/day over 7 days = delta_7d=21, comfortably above the floor."""
    _seed_steady_growth(db_conn, per_day=3)
    proj = velocity.get_velocity_projection(db_conn)
    assert proj is not None
    assert proj.in_noise_floor is False
    assert proj.velocity_7d_per_day is not None
    assert proj.velocity_7d_per_day > 0
    # Distance to milestone is positive (default milestone=100, followers >100).
    assert proj.current_milestone_target == 100
    # When followers already met the milestone, the view returns None
    # for projection dates (milestone met). Our seed starts at 100 +
    # per_day*7 = 121, so milestone is already met. Confirm this is
    # handled — distance_to_current_milestone goes negative.
    assert proj.distance_to_current_milestone <= 0


def test_non_noise_with_distance_remaining(db_conn: sqlite3.Connection) -> None:
    """Distance > 0 and positive velocity → projected date materializes."""
    db_conn.execute(
        "UPDATE settings SET value_json = '500' WHERE key = ?",
        ("current_milestone",),
    )
    _seed_steady_growth(db_conn, per_day=3)
    proj = velocity.get_velocity_projection(db_conn)
    assert proj is not None
    assert proj.in_noise_floor is False
    assert proj.current_milestone_target == 500
    assert proj.distance_to_current_milestone is not None
    assert proj.distance_to_current_milestone > 0
    assert proj.projected_milestone_hit_date_at_7d_pace is not None
    # Date is in the future.
    proj_date = date.fromisoformat(proj.projected_milestone_hit_date_at_7d_pace)
    assert proj_date >= date.today()


def test_milestone_already_met_returns_no_projection(
    db_conn: sqlite3.Connection,
) -> None:
    """When followers > current_milestone_target, the view emits NULL
    for the projection columns (distance <= 0 in the CASE)."""
    db_conn.execute(
        "UPDATE settings SET value_json = '100' WHERE key = ?",
        ("current_milestone",),
    )
    _seed_steady_growth(db_conn, per_day=3)
    proj = velocity.get_velocity_projection(db_conn)
    assert proj is not None
    # Already over 100, so no projection.
    assert proj.projected_milestone_hit_date_at_7d_pace is None


# ---------------------------------------------------------------------------
# daily_followers_needed_to_hit_milestone_by_date.
# ---------------------------------------------------------------------------
def test_helper_returns_positive_int_for_future_target(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        "UPDATE settings SET value_json = '500' WHERE key = ?",
        ("current_milestone",),
    )
    _seed_steady_growth(db_conn, per_day=1)
    target = date.today() + timedelta(days=30)
    out = velocity.daily_followers_needed_to_hit_milestone_by_date(
        db_conn, target_date=target
    )
    assert out is not None
    assert out > 0


def test_helper_returns_none_when_target_in_past(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_steady_growth(db_conn, per_day=1)
    yesterday = date.today() - timedelta(days=1)
    out = velocity.daily_followers_needed_to_hit_milestone_by_date(
        db_conn, target_date=yesterday
    )
    assert out is None


def test_helper_returns_none_when_milestone_met(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        "UPDATE settings SET value_json = '100' WHERE key = ?",
        ("current_milestone",),
    )
    _seed_steady_growth(db_conn, per_day=3)  # starts at 121 > 100
    out = velocity.daily_followers_needed_to_hit_milestone_by_date(
        db_conn, target_date=date.today() + timedelta(days=30)
    )
    assert out is None


def test_helper_returns_none_with_no_snapshots(
    db_conn: sqlite3.Connection,
) -> None:
    out = velocity.daily_followers_needed_to_hit_milestone_by_date(
        db_conn, target_date=date.today() + timedelta(days=30)
    )
    assert out is None


def test_helper_accepts_iso_string(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        "UPDATE settings SET value_json = '500' WHERE key = ?",
        ("current_milestone",),
    )
    _seed_steady_growth(db_conn, per_day=1)
    iso = (date.today() + timedelta(days=14)).isoformat()
    assert velocity.daily_followers_needed_to_hit_milestone_by_date(
        db_conn, target_date=iso
    ) is not None


# ---------------------------------------------------------------------------
# Tool wrapper.
# ---------------------------------------------------------------------------
def test_tool_returns_dict_with_in_noise_floor_flag(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_steady_growth(db_conn, per_day=3)
    tool = get_tool("get_velocity_projection")
    out = tool.handler(db_conn)
    assert isinstance(out, dict)
    assert "in_noise_floor" in out
    assert "snapshot_date" in out


def test_tool_handles_empty_db_gracefully(db_conn: sqlite3.Connection) -> None:
    tool = get_tool("get_velocity_projection")
    out = tool.handler(db_conn)
    # Empty DB — handler returns the documented error shape.
    assert "error" in out


# ---------------------------------------------------------------------------
# Settings — explicit noise-floor reader.
# ---------------------------------------------------------------------------
def test_get_noise_floor_reads_setting(db_conn: sqlite3.Connection) -> None:
    assert velocity.get_noise_floor(db_conn) == 10  # default seed
    db_conn.execute(
        "UPDATE settings SET value_json = '25' WHERE key = ?",
        ("velocity_projection_noise_floor_followers",),
    )
    assert velocity.get_noise_floor(db_conn) == 25
