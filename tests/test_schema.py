"""Schema-level tests for Phase 1 (spec.md §10).

Covers: every Phase 1 table exists and is queryable, FK enforcement is real,
views compile, and the documented seed rows land as expected.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.db import apply_migrations, connect
from scripts.seed_milestones import expected_counts, seed_milestones
from scripts.seed_settings import documented_keys, seed_settings

PHASE_1_TABLES: tuple[str, ...] = (
    "settings",
    "account_snapshots",
    "account_snapshot_corrections",
    "raw_api_responses",
    "posts",
    "post_metric_snapshots",
    "post_classifications",
    "daily_activity",
    "reply_sessions",
    "stir_conversion_events",
    "stir_testers",
    "milestones",
    "weekly_reviews",
    "experiments",
)

PHASE_1_VIEWS: tuple[str, ...] = (
    "v_account_daily",
    "v_post_latest_metrics",
    "v_daily_reps",
    "v_funnel_daily",
    "v_lane_performance",
)


def test_every_phase1_table_exists(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_1_TABLES) - names
    assert not missing, f"Phase 1 tables missing from schema: {missing}"


def test_every_table_is_queryable(db_conn: sqlite3.Connection) -> None:
    for table in PHASE_1_TABLES:
        db_conn.execute(f"SELECT * FROM {table} LIMIT 0")


def test_every_view_compiles(db_conn: sqlite3.Connection) -> None:
    for view in PHASE_1_VIEWS:
        db_conn.execute(f"SELECT * FROM {view} LIMIT 0")


def test_schema_migrations_records_each_file(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    filenames = [row["filename"] for row in rows]
    assert filenames == [
        "001_initial.sql",
        "002_views.sql",
        "003_backup_settings.sql",
        "004_data_exports.sql",
        "005_agent_core.sql",
        "006_publish_columns.sql",
    ]


def test_apply_migrations_is_idempotent(db_path: Path) -> None:
    conn = connect(db_path)
    first_run = apply_migrations(conn)
    second_run = apply_migrations(conn)
    conn.close()
    assert first_run == [
        "001_initial.sql",
        "002_views.sql",
        "003_backup_settings.sql",
        "004_data_exports.sql",
        "005_agent_core.sql",
        "006_publish_columns.sql",
    ]
    assert second_run == []


# ---------------------------------------------------------------------------
# Foreign-key enforcement: prove PRAGMA foreign_keys = ON is doing real work.
# Strategy: insert a post_metric_snapshots row referencing a non-existent
# post_id with FK on (expect IntegrityError), then re-attempt the same insert
# on a separate connection with FK off (expect success). If both behaviors
# match, the PRAGMA setting is load-bearing.
# ---------------------------------------------------------------------------
def test_fk_enforcement_blocks_bad_post_id(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO post_metric_snapshots
              (post_id, x_post_id, collected_at_utc, source, data_quality)
            VALUES (?, ?, ?, ?, ?)
            """,
            (999_999, "x_does_not_exist", "2026-05-21T12:00:00Z", "manual", "manual"),
        )


def test_fk_enforcement_pragma_is_load_bearing(db_path: Path) -> None:
    """With PRAGMA OFF, the same bad insert would succeed."""
    conn = connect(db_path)
    apply_migrations(conn)
    # Sanity: PRAGMA is on by default through app.db.connect().
    fk_state = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_state == 1, "app.db.connect() must turn FKs on"

    # Now flip off and confirm the bad insert succeeds.
    conn.execute("PRAGMA foreign_keys = OFF;")
    fk_state = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_state == 0
    conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc, source, data_quality)
        VALUES (?, ?, ?, ?, ?)
        """,
        (999_999, "x_does_not_exist", "2026-05-21T12:00:00Z", "manual", "manual"),
    )
    # Row landed despite the dangling FK — proves the PRAGMA gate matters.
    count = conn.execute(
        "SELECT COUNT(*) FROM post_metric_snapshots WHERE post_id = 999999"
    ).fetchone()[0]
    assert count == 1
    conn.close()


# ---------------------------------------------------------------------------
# CHECK constraints — spot-check a few load-bearing ones.
# ---------------------------------------------------------------------------
def test_posts_type_check_constraint(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO posts
              (created_date, text, type, posted_via, manual_confirmation_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-05-21", "hello", "tweet", "manual", "confirmed"),  # 'tweet' invalid
        )


def test_stir_conversion_event_privacy_check_blocks_inferred_icp(
    db_conn: sqlite3.Connection,
) -> None:
    """§10.2 privacy rule: is_likely_icp requires attribution_method='self_reported'."""
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO stir_conversion_events
              (occurred_at_utc, event_date, event_category, event_type,
               attribution_method, is_likely_icp, source_data_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-21T12:00:00Z", "2026-05-21",
                "acquisition", "download",
                "inferred", 1,  # is_likely_icp set with non-self-reported method
                "inferred",
            ),
        )


def test_stir_conversion_event_self_reported_icp_is_allowed(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        """
        INSERT INTO stir_conversion_events
          (occurred_at_utc, event_date, event_category, event_type,
           attribution_method, is_likely_icp, source_data_quality)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-21T12:00:00Z", "2026-05-21",
            "acquisition", "download",
            "self_reported", 1,
            "manual",
        ),
    )


# ---------------------------------------------------------------------------
# Seed assertions.
# ---------------------------------------------------------------------------
def test_settings_has_every_documented_row(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    for expected in documented_keys():
        assert expected in keys, f"missing settings row: {expected}"


def test_settings_value_json_is_decodable(db_conn: sqlite3.Connection) -> None:
    for row in db_conn.execute("SELECT key, value_json FROM settings"):
        # JSON-decoding every row catches malformed inserts.
        json.loads(row["value_json"])


def test_baseline_settings_have_expected_values(db_conn: sqlite3.Connection) -> None:
    """Spot-check the load-bearing defaults from §27 / §10.2."""
    def get(key: str):
        row = db_conn.execute(
            "SELECT json_extract(value_json, '$') FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    assert get("x_handle") == "dannyscalant"
    assert get("baseline_followers") == 61
    assert get("operational_ceiling") == 5000
    assert get("long_arc_reminder") == 500000
    assert get("daily_reply_target") == 12
    assert get("daily_post_target") == 1
    assert get("data_collection_mode") == "manual"


def test_milestones_seed_distribution_and_validation_counts(
    db_conn: sqlite3.Connection,
) -> None:
    counts: dict[str, int] = {}
    for row in db_conn.execute(
        "SELECT category, COUNT(*) FROM milestones GROUP BY category"
    ):
        counts[row[0]] = row[1]
    expected = expected_counts()
    assert counts.get("distribution") == expected["distribution"] == 6
    assert counts.get("validation") == expected["validation"] == 6
    assert counts.get("content") == expected["content"]
    assert counts.get("reps") == expected["reps"]


def test_milestones_seed_is_idempotent(db_conn: sqlite3.Connection) -> None:
    before = db_conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
    seed_milestones(db_conn)
    after = db_conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
    assert before == after, "seed_milestones must be idempotent (INSERT OR IGNORE)"


def test_settings_seed_is_idempotent(db_conn: sqlite3.Connection) -> None:
    before = db_conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    seed_settings(db_conn)
    after = db_conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    assert before == after, "seed_settings must be idempotent (INSERT OR IGNORE)"
