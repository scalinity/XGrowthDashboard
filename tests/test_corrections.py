"""Snapshot correction tests — spec.md §13 hard rule 2 + §22.

A correction must:
1. Append a row to ``account_snapshot_corrections``.
2. **Not** mutate the original ``account_snapshots`` row.
3. Be visible to subsequent reads (i.e. it's actually committed).
"""

from __future__ import annotations

import sqlite3

from app.forms.correction import submit_correction
from app.forms.snapshot import submit_snapshot


def _seed_snapshot(db_conn: sqlite3.Connection, followers: int = 100) -> int:
    return submit_snapshot(
        db_conn,
        {
            "snapshot_date": "2026-05-20",
            "username": "dannyscalant",
            "profile_url": "https://x.com/dannyscalant",
            "followers_count": followers,
            "following_count": 80,
            "post_count": 200,
            "listed_count": 3,
            "baseline_followers": 61,
        },
    )


def test_correction_appends_row(db_conn: sqlite3.Connection) -> None:
    snap_id = _seed_snapshot(db_conn, followers=100)
    new_id = submit_correction(
        db_conn,
        {
            "snapshot_id": snap_id,
            "field_name": "followers_count",
            "new_value": 105,
            "reason": "miscounted on initial entry; checked profile twice",
        },
    )
    row = db_conn.execute(
        "SELECT * FROM account_snapshot_corrections WHERE id = ?", (new_id,)
    ).fetchone()
    assert row["snapshot_id"] == snap_id
    assert row["field_name"] == "followers_count"
    assert row["old_value"] == "100"
    assert row["new_value"] == "105"
    assert "miscounted" in row["reason"]


def test_correction_does_not_mutate_original_snapshot(
    db_conn: sqlite3.Connection,
) -> None:
    snap_id = _seed_snapshot(db_conn, followers=100)
    submit_correction(
        db_conn,
        {
            "snapshot_id": snap_id,
            "field_name": "followers_count",
            "new_value": 105,
            "reason": "miscounted",
        },
    )
    row = db_conn.execute(
        "SELECT followers_count FROM account_snapshots WHERE id = ?",
        (snap_id,),
    ).fetchone()
    # The raw row is untouched — §13 hard rule 2.
    assert row["followers_count"] == 100


def test_correction_is_visible_to_subsequent_reads(
    db_conn: sqlite3.Connection,
) -> None:
    snap_id = _seed_snapshot(db_conn, followers=100)
    submit_correction(
        db_conn,
        {
            "snapshot_id": snap_id,
            "field_name": "followers_count",
            "new_value": 105,
            "reason": "miscounted",
        },
    )
    submit_correction(
        db_conn,
        {
            "snapshot_id": snap_id,
            "field_name": "bio_text",
            "new_value": "scalinity / building Stir",
            "reason": "the bio I had at this moment",
        },
    )
    count = db_conn.execute(
        "SELECT COUNT(*) FROM account_snapshot_corrections WHERE snapshot_id = ?",
        (snap_id,),
    ).fetchone()[0]
    assert count == 2


def test_correction_records_null_old_value_as_empty_string(
    db_conn: sqlite3.Connection,
) -> None:
    snap_id = _seed_snapshot(db_conn, followers=100)
    # bio_text is null in the seed snapshot.
    submit_correction(
        db_conn,
        {
            "snapshot_id": snap_id,
            "field_name": "bio_text",
            "new_value": "scalinity / building Stir",
            "reason": "the bio I had at this moment",
        },
    )
    row = db_conn.execute(
        """
        SELECT old_value FROM account_snapshot_corrections
         WHERE snapshot_id = ? AND field_name = 'bio_text'
        """,
        (snap_id,),
    ).fetchone()
    assert row["old_value"] == ""
