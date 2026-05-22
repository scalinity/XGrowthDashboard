"""Follower-velocity projection (§28.19, Phase 5.9).

Wraps the ``v_follower_velocity`` view (migration 012) with the
noise-floor discipline §28.19 requires. The view itself NULLs every
projection column when ``|delta_7d| < velocity_projection_noise_floor_
followers`` OR ``velocity_7d_per_day <= 0`` OR the milestone is met,
so this module just surfaces the row + a tool wrapper.

Hard rule (mirrors §13): never display a precise date when the input is
noise. The UI also checks the suppression condition independently so
the contract is enforced in two places.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as _date_t

from app.agent import settings_io


# Default copied from §13's velocity_7d_display_threshold so the rules
# read together; the migration also seeds an explicit
# velocity_projection_noise_floor_followers row for auditability.
DEFAULT_NOISE_FLOOR: int = 10


def get_noise_floor(conn: sqlite3.Connection) -> int:
    return settings_io.get_int(
        conn, "velocity_projection_noise_floor_followers", DEFAULT_NOISE_FLOOR
    )


@dataclass(frozen=True)
class VelocityProjection:
    """Latest-snapshot velocity + projection columns from v_follower_velocity.

    Projection fields are None when the noise-floor / non-positive-
    velocity / milestone-met suppression conditions hold (per §28.19).
    """

    snapshot_date: str | None
    followers_count: int | None
    velocity_7d_per_day: float | None
    velocity_30d_per_day: float | None
    current_milestone_target: int | None
    distance_to_current_milestone: int | None
    projected_milestone_hit_date_at_7d_pace: str | None
    projected_milestone_hit_date_at_30d_pace: str | None
    days_until_milestone_at_7d_pace: int | None
    days_until_milestone_at_30d_pace: int | None
    in_noise_floor: bool  # explicit suppression flag

    def to_dict(self) -> dict:
        return {
            "snapshot_date": self.snapshot_date,
            "followers_count": self.followers_count,
            "velocity_7d_per_day": self.velocity_7d_per_day,
            "velocity_30d_per_day": self.velocity_30d_per_day,
            "current_milestone_target": self.current_milestone_target,
            "distance_to_current_milestone": self.distance_to_current_milestone,
            "projected_milestone_hit_date_at_7d_pace": (
                self.projected_milestone_hit_date_at_7d_pace
            ),
            "projected_milestone_hit_date_at_30d_pace": (
                self.projected_milestone_hit_date_at_30d_pace
            ),
            "days_until_milestone_at_7d_pace": self.days_until_milestone_at_7d_pace,
            "days_until_milestone_at_30d_pace": self.days_until_milestone_at_30d_pace,
            "in_noise_floor": self.in_noise_floor,
        }


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def get_velocity_projection(conn: sqlite3.Connection) -> VelocityProjection | None:
    """Latest v_follower_velocity row, or None if no snapshots exist.

    P59A-W6: the view now surfaces ``in_noise_floor`` and ``delta_7d``
    directly (migration 014). One SELECT — the prior second SELECT
    against v_account_daily is gone, and the suppression rule lives
    in exactly one place (the view).
    """
    row = conn.execute(
        """
        SELECT snapshot_date,
               followers_count,
               delta_7d,
               velocity_7d_per_day,
               velocity_30d_per_day,
               current_milestone_target,
               distance_to_current_milestone,
               in_noise_floor,
               projected_milestone_hit_date_at_7d_pace,
               projected_milestone_hit_date_at_30d_pace,
               days_until_milestone_at_7d_pace,
               days_until_milestone_at_30d_pace
        FROM v_follower_velocity
        """
    ).fetchone()
    if row is None:
        return None

    in_noise_floor = bool(row["in_noise_floor"])
    return VelocityProjection(
        snapshot_date=row["snapshot_date"],
        followers_count=(
            int(row["followers_count"]) if row["followers_count"] is not None else None
        ),
        velocity_7d_per_day=row["velocity_7d_per_day"],
        velocity_30d_per_day=row["velocity_30d_per_day"],
        current_milestone_target=(
            int(row["current_milestone_target"])
            if row["current_milestone_target"] is not None
            else None
        ),
        distance_to_current_milestone=(
            int(row["distance_to_current_milestone"])
            if row["distance_to_current_milestone"] is not None
            else None
        ),
        projected_milestone_hit_date_at_7d_pace=(
            row["projected_milestone_hit_date_at_7d_pace"]
        ),
        projected_milestone_hit_date_at_30d_pace=(
            row["projected_milestone_hit_date_at_30d_pace"]
        ),
        days_until_milestone_at_7d_pace=(
            int(row["days_until_milestone_at_7d_pace"])
            if row["days_until_milestone_at_7d_pace"] is not None
            else None
        ),
        days_until_milestone_at_30d_pace=(
            int(row["days_until_milestone_at_30d_pace"])
            if row["days_until_milestone_at_30d_pace"] is not None
            else None
        ),
        in_noise_floor=in_noise_floor,
    )


# ---------------------------------------------------------------------------
# Date-target widget helper (§28.19 §14.3 panel).
# ---------------------------------------------------------------------------
def daily_followers_needed_to_hit_milestone_by_date(
    conn: sqlite3.Connection,
    *,
    target_date: _date_t | str,
) -> int | None:
    """Per the §11 spec for ``daily_followers_needed_to_hit_milestone_by_date``.

    Returns ``ceil((current_milestone_target - followers_count) /
    max((target_date - today), 1))`` from the latest v_account_daily
    snapshot. Returns None when the milestone is already met or the
    target date is today / in the past.
    """
    if isinstance(target_date, str):
        try:
            target_d = _date_t.fromisoformat(target_date)
        except ValueError:
            return None
    else:
        target_d = target_date
    today = _date_t.today()
    days_remaining = (target_d - today).days
    if days_remaining <= 0:
        return None

    proj = get_velocity_projection(conn)
    if proj is None or proj.current_milestone_target is None or proj.followers_count is None:
        return None
    distance = proj.current_milestone_target - proj.followers_count
    if distance <= 0:
        return None  # milestone already met
    # math.ceil but stay in int land for sqlite-friendly result types
    return -(-distance // days_remaining)


__all__ = [
    "DEFAULT_NOISE_FLOOR",
    "VelocityProjection",
    "daily_followers_needed_to_hit_milestone_by_date",
    "get_noise_floor",
    "get_velocity_projection",
]
