"""Seed the 12 dual-ladder milestones.

Per §4 and §10.2:
- 6 distribution rungs (61 → 100 → 250 → 500 → 1,000 → 2,500 → 5,000).
- 6 validation rungs (first attributed download, first 5, first ICP tester,
  first kitchen scan with 3 plausible dinners, first Cook Mode completion,
  5 Cook Mode completions in a week).

Content (3 rungs) and reps (2 rungs) ladders from §10.2 are also seeded so
the Progress view in Phase 3 has the full ladder set. The phase prompt's
acceptance gate counts the dual-ladder structure (6+6); content/reps are
additive.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

# (category, ladder_position, name, start_value, target_value, notes)
_DISTRIBUTION_MILESTONES: list[tuple[str, int, str, int | None, int | None, str | None]] = [
    ("distribution", 1, "61 → 100 followers",     61,   100,   None),
    ("distribution", 2, "100 → 250 followers",    100,  250,   None),
    ("distribution", 3, "250 → 500 followers",    250,  500,   None),
    ("distribution", 4, "500 → 1,000 followers",  500,  1000,  None),
    ("distribution", 5, "1,000 → 2,500 followers",1000, 2500,  None),
    (
        "distribution",
        6,
        "2,500 → 5,000 followers (operational ceiling)",
        2500,
        5000,
        "Operational ceiling per §27; beyond here the dashboard stops anchoring on follower count",
    ),
]

_VALIDATION_MILESTONES: list[tuple[str, int, str, int | None, int | None, str | None]] = [
    ("validation", 1, "First Stir download attributed to X",                 None, 1, None),
    ("validation", 2, "First 5 Stir downloads",                              None, 5, None),
    (
        "validation",
        3,
        "First working-parent/home-cook tester (self-reported)",
        None,
        1,
        "Self-reported only per §10.2 / §18 rule 11",
    ),
    ("validation", 4, "First kitchen scan with 3 plausible dinners",         None, 1, None),
    ("validation", 5, "First Cook Mode completion",                          None, 1, None),
    ("validation", 6, "5 Cook Mode completions in a week",                   None, 5, None),
]

_CONTENT_MILESTONES: list[tuple[str, int, str, int | None, int | None, str | None]] = [
    ("content", 1, "First post with 1,000 impressions", None, 1000, None),
    ("content", 2, "First reply with 100 impressions",  None, 100,  None),
    ("content", 3, "First post with 10+ bookmarks",     None, 10,   None),
]

_REPS_MILESTONES: list[tuple[str, int, str, int | None, int | None, str | None]] = [
    ("reps", 1, "First week with daily reply reps completed",         None, 7,  None),
    ("reps", 2, "First 4 consecutive weeks of rep adherence",         None, 28, None),
]

_ALL_MILESTONES: list[tuple[str, int, str, int | None, int | None, str | None]] = (
    _DISTRIBUTION_MILESTONES
    + _VALIDATION_MILESTONES
    + _CONTENT_MILESTONES
    + _REPS_MILESTONES
)


def seed_milestones(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, int, str, int | None, int | None, str | None]] | None = None,
) -> int:
    payload = list(rows) if rows is not None else _ALL_MILESTONES
    inserted = 0
    for category, position, name, start, target, notes in payload:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO milestones
              (category, ladder_position, name, start_value, target_value, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (category, position, name, start, target, notes),
        )
        inserted += cursor.rowcount or 0
    return inserted


def expected_counts() -> dict[str, int]:
    return {
        "distribution": len(_DISTRIBUTION_MILESTONES),
        "validation":   len(_VALIDATION_MILESTONES),
        "content":      len(_CONTENT_MILESTONES),
        "reps":         len(_REPS_MILESTONES),
    }
