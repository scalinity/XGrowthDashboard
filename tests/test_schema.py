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


EXPECTED_MIGRATION_FILES: tuple[str, ...] = (
    "001_initial.sql",
    "002_views.sql",
    "003_backup_settings.sql",
    "004_data_exports.sql",
    "005_agent_core.sql",
    "006_publish_columns.sql",
    "007_post_classifications_unique.sql",
    "008_agent_tool_usage_view.sql",
    "009_reply_targets.sql",
    "010_reply_targets_idx.sql",
    "011_drafting_intelligence.sql",
    "012_niche_content_type.sql",
    "013_strategic_analysis.sql",
    "014_velocity_view_expose_noise_floor.sql",
    "015_growth_layer_qol.sql",
    "016_blogs.sql",
    "017_blog_agent_action_x_to_blog.sql",
)


def test_schema_migrations_records_each_file(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()
    filenames = [row["filename"] for row in rows]
    assert filenames == list(EXPECTED_MIGRATION_FILES)


def test_apply_migrations_is_idempotent(db_path: Path) -> None:
    conn = connect(db_path)
    first_run = apply_migrations(conn)
    second_run = apply_migrations(conn)
    conn.close()
    assert first_run == list(EXPECTED_MIGRATION_FILES)
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


# ---------------------------------------------------------------------------
# Phase 5.8 — Drafting Intelligence Pack (migration 011).
# ---------------------------------------------------------------------------
PHASE_58_TABLES: tuple[str, ...] = (
    "voice_profiles",
    "post_embeddings",
    "prepublish_scores",
)

PHASE_58_SETTINGS: tuple[str, ...] = (
    "voice_profile_window_days",
    "voice_profile_min_source_posts",
    "repetition_guard_lookback_days",
    "repetition_guard_near_duplicate_threshold",
    "repetition_guard_close_echo_threshold",
    "prepublish_scorer_llm_augmentation_enabled",
    "modal_hash_recheck_debounce_ms",
    "modal_edit_settle_seconds",
)


def test_phase58_tables_exist(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_58_TABLES) - names
    assert not missing, f"Phase 5.8 tables missing: {missing}"


def test_phase58_new_agent_drafts_columns(db_conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in db_conn.execute("PRAGMA table_info(agent_drafts)")}
    assert {"prepublish_score_id", "confidence_label", "similarity_warning_json"}.issubset(cols)


def test_phase58_new_agent_messages_column(db_conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in db_conn.execute("PRAGMA table_info(agent_messages)")}
    assert "confidence_label" in cols


def test_phase58_voice_profiles_unique_active(db_conn: sqlite3.Connection) -> None:
    # First active row is allowed.
    db_conn.execute(
        """
        INSERT INTO voice_profiles
          (is_active, source_post_window_days, source_post_count, profile_json,
           model_used, tokens_used)
        VALUES (1, 90, 12, '{}', 'claude-haiku-4-5-20251001', 0)
        """
    )
    # Second active row violates the partial unique index.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO voice_profiles
              (is_active, source_post_window_days, source_post_count, profile_json,
               model_used, tokens_used)
            VALUES (1, 90, 12, '{}', 'claude-haiku-4-5-20251001', 0)
            """
        )


def test_phase58_post_embeddings_cascade_on_post_delete(db_conn: sqlite3.Connection) -> None:
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES ('2026-05-22', 'hello', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO post_embeddings
          (post_id, embedding_blob, embedding_dim, model_name, source_text_hash)
        VALUES (?, X'00', 4, 'voyage-3-lite', 'deadbeef')
        """,
        (post_id,),
    )
    db_conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    remaining = db_conn.execute(
        "SELECT COUNT(*) FROM post_embeddings WHERE post_id = ?", (post_id,)
    ).fetchone()[0]
    assert remaining == 0, "post_embeddings should cascade-delete with its parent post"


def test_phase58_prepublish_scores_composite_label_check(db_conn: sqlite3.Connection) -> None:
    # Insert a parent draft to satisfy the FK first.
    draft_id = db_conn.execute(
        """
        INSERT INTO agent_drafts (draft_kind, text)
        VALUES ('standalone', 'placeholder')
        RETURNING id
        """
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO prepublish_scores
              (agent_draft_id, clarity_score, hook_strength_score, specificity_score,
               length_fit_score, format_fit_score, topic_fit_score, composite_label,
               scorer_version)
            VALUES (?, 2, 2, 2, 2, 2, 2, 'mediocre', 'prepublish-scorer/0.1.0')
            """,
            (draft_id,),
        )


def test_phase58_agent_drafts_confidence_label_check(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO agent_drafts (draft_kind, text, confidence_label)
            VALUES ('standalone', 'x', 'gut_feel')
            """
        )


def test_phase58_settings_rows_present(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    missing = set(PHASE_58_SETTINGS) - keys
    assert not missing, f"missing Phase 5.8 settings rows: {missing}"


# ---------------------------------------------------------------------------
# Phase 5.9 — Niche & Content-Type Calibration Pack (migration 012).
# ---------------------------------------------------------------------------
PHASE_59_TABLES: tuple[str, ...] = ("personality_lore",)

PHASE_59_VIEWS: tuple[str, ...] = (
    "v_content_type_performance",
    "v_follower_velocity",
)

PHASE_59_SETTINGS: tuple[str, ...] = (
    "niche_problem",
    "niche_person",
    "reply_quality_lint_enabled",
    "personality_lore_overuse_threshold",
    "content_type_recommendation_window_days",
    "velocity_projection_noise_floor_followers",
    "personality_lore_splice_count",
)


def test_phase59_tables_exist(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_59_TABLES) - names
    assert not missing, f"Phase 5.9 tables missing: {missing}"


def test_phase59_views_compile(db_conn: sqlite3.Connection) -> None:
    for view in PHASE_59_VIEWS:
        db_conn.execute(f"SELECT * FROM {view} LIMIT 0")


def test_phase59_posts_content_type_column_and_default(db_conn: sqlite3.Connection) -> None:
    cols = {row[1]: row for row in db_conn.execute("PRAGMA table_info(posts)")}
    assert "content_type" in cols
    # New rows default to 'unspecified' per §28.17 — never retro-classify.
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES ('2026-05-22', 'hi', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    ct = db_conn.execute("SELECT content_type FROM posts WHERE id = ?", (post_id,)).fetchone()[0]
    assert ct == "unspecified"


def test_phase59_posts_content_type_check_constraint(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO posts (created_date, text, type, posted_via, manual_confirmation_status,
                               content_type)
            VALUES ('2026-05-22', 'hi', 'standalone', 'manual', 'confirmed', 'thought-leadership')
            """
        )


def test_phase59_agent_drafts_content_type_check(db_conn: sqlite3.Connection) -> None:
    # NULL is permitted (legacy rows pre-Phase 5.9 stay NULL).
    db_conn.execute(
        """
        INSERT INTO agent_drafts (draft_kind, text, content_type)
        VALUES ('standalone', 'x', NULL)
        """
    )
    # 'unspecified' is permitted at the CHECK level (orchestrator does the
    # runtime refusal). Invalid value still rejected.
    db_conn.execute(
        """
        INSERT INTO agent_drafts (draft_kind, text, content_type)
        VALUES ('standalone', 'y', 'unspecified')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO agent_drafts (draft_kind, text, content_type)
            VALUES ('standalone', 'z', 'thought-leadership')
            """
        )


def test_phase59_agent_drafts_reply_quality_lint_passed_check(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO agent_drafts (draft_kind, text, reply_quality_lint_passed)
            VALUES ('reply', 'x', 2)
            """
        )


def test_phase59_personality_lore_defaults(db_conn: sqlite3.Connection) -> None:
    row_id = db_conn.execute(
        """
        INSERT INTO personality_lore (theme, description)
        VALUES ('water bottle in frame', 'self-deprecating bit about my water bottle')
        RETURNING id
        """
    ).fetchone()[0]
    row = db_conn.execute(
        "SELECT invocation_count, is_active, priority FROM personality_lore WHERE id = ?",
        (row_id,),
    ).fetchone()
    assert row["invocation_count"] == 0
    assert row["is_active"] == 1
    assert row["priority"] == 100


def test_phase59_reply_targets_source_check(db_conn: sqlite3.Connection) -> None:
    # The §28.20 third path adds `replier_under_thread`. CHECK rejects unknown.
    db_conn.execute(
        """
        INSERT INTO reply_targets
          (discovered_via, target_post_url, target_author_handle, source)
        VALUES ('manual', 'https://x.com/foo/status/1', 'foo', 'replier_under_thread')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO reply_targets
              (discovered_via, target_post_url, target_author_handle, source)
            VALUES ('manual', 'https://x.com/foo/status/2', 'foo', 'firehose_scan')
            """
        )


def test_phase59_settings_rows_present(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    missing = set(PHASE_59_SETTINGS) - keys
    assert not missing, f"missing Phase 5.9 settings rows: {missing}"


# ---------------------------------------------------------------------------
# Phase 5.10 — Strategic Analysis Pack (migration 013).
# ---------------------------------------------------------------------------
PHASE_510_TABLES: tuple[str, ...] = (
    "brain_dumps",
    "account_research_reports",
    "profile_audits",
)

PHASE_510_SETTINGS: tuple[str, ...] = (
    "coach_refuse_without_evidence",
    "coach_citation_strip_log_threshold",
    "brain_dump_max_candidate_drafts",
    "profile_audit_recent_posts_window_days",
    "profile_audit_cadence_reminder_days",
)


def test_phase510_tables_exist(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_510_TABLES) - names
    assert not missing, f"Phase 5.10 tables missing: {missing}"


def test_phase510_brain_dumps_status_check(db_conn: sqlite3.Connection) -> None:
    # Default status is unprocessed per §28.22 capture-first flow.
    row_id = db_conn.execute(
        """
        INSERT INTO brain_dumps (raw_text) VALUES ('kitchen-scanner missed ginger again')
        RETURNING id
        """
    ).fetchone()[0]
    status = db_conn.execute(
        "SELECT status FROM brain_dumps WHERE id = ?", (row_id,)
    ).fetchone()[0]
    assert status == "unprocessed"
    # CHECK constraint rejects unknown status values.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO brain_dumps (raw_text, status) VALUES ('x', 'committed')"
        )


def test_phase510_account_research_handle_versioning(db_conn: sqlite3.Connection) -> None:
    # Multiple reports per handle are allowed — versioned history per §28.24.
    db_conn.execute(
        """
        INSERT INTO account_research_reports
          (target_handle, created_at_utc, analysis_json, model_used)
        VALUES ('@foo', '2026-05-01T00:00:00', '{}', 'claude-opus-4-7')
        """
    )
    db_conn.execute(
        """
        INSERT INTO account_research_reports
          (target_handle, created_at_utc, analysis_json, model_used)
        VALUES ('@foo', '2026-05-22T00:00:00', '{}', 'claude-opus-4-7')
        """
    )
    rows = db_conn.execute(
        "SELECT COUNT(*) FROM account_research_reports WHERE target_handle = '@foo'"
    ).fetchone()[0]
    assert rows == 2
    # But unique(target_handle, created_at_utc) prevents duplicate timestamps.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO account_research_reports
              (target_handle, created_at_utc, analysis_json, model_used)
            VALUES ('@foo', '2026-05-22T00:00:00', '{}', 'claude-opus-4-7')
            """
        )


def test_phase510_account_research_linked_reply_target_fk(
    db_conn: sqlite3.Connection,
) -> None:
    # Insert a reply_targets row to link to.
    rt_id = db_conn.execute(
        """
        INSERT INTO reply_targets
          (discovered_via, target_post_url, target_author_handle)
        VALUES ('manual', 'https://x.com/foo/status/1', '@foo')
        RETURNING id
        """
    ).fetchone()[0]
    arr_id = db_conn.execute(
        """
        INSERT INTO account_research_reports
          (target_handle, analysis_json, model_used, linked_reply_target_id)
        VALUES ('@foo', '{}', 'claude-opus-4-7', ?)
        RETURNING id
        """,
        (rt_id,),
    ).fetchone()[0]
    # ON DELETE SET NULL — deleting the reply target nulls the back-reference.
    db_conn.execute("DELETE FROM reply_targets WHERE id = ?", (rt_id,))
    linked = db_conn.execute(
        "SELECT linked_reply_target_id FROM account_research_reports WHERE id = ?",
        (arr_id,),
    ).fetchone()[0]
    assert linked is None


def test_phase510_profile_audits_window_check(db_conn: sqlite3.Connection) -> None:
    # Default window is 30 days per §28.25 / migration 013.
    row_id = db_conn.execute(
        """
        INSERT INTO profile_audits
          (bio_snapshot, audit_json, model_used)
        VALUES ('hi', '{}', 'claude-opus-4-7')
        RETURNING id
        """
    ).fetchone()[0]
    win = db_conn.execute(
        "SELECT recent_posts_window_days FROM profile_audits WHERE id = ?", (row_id,)
    ).fetchone()[0]
    assert win == 30
    # CHECK rejects non-positive windows.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO profile_audits
              (bio_snapshot, audit_json, model_used, recent_posts_window_days)
            VALUES ('hi', '{}', 'claude-opus-4-7', 0)
            """
        )


def test_phase510_agent_messages_evidence_citations_column(
    db_conn: sqlite3.Connection,
) -> None:
    cols = {row[1] for row in db_conn.execute("PRAGMA table_info(agent_messages)")}
    assert "evidence_citations_json" in cols
    # Backfills NULL for pre-existing rows AND new rows that don't set it.
    conv_id = db_conn.execute(
        """
        INSERT INTO agent_conversations (status) VALUES ('active') RETURNING id
        """
    ).fetchone()[0]
    msg_id = db_conn.execute(
        """
        INSERT INTO agent_messages (conversation_id, role, content)
        VALUES (?, 'user', 'hi') RETURNING id
        """,
        (conv_id,),
    ).fetchone()[0]
    cit = db_conn.execute(
        "SELECT evidence_citations_json FROM agent_messages WHERE id = ?", (msg_id,)
    ).fetchone()[0]
    assert cit is None


def test_phase510_settings_rows_present(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    missing = set(PHASE_510_SETTINGS) - keys
    assert not missing, f"missing Phase 5.10 settings rows: {missing}"


# ---------------------------------------------------------------------------
# Phase 5.11 — Growth Layer + Quality-of-Life Pack (migration 015).
# ---------------------------------------------------------------------------
PHASE_511_TABLES: tuple[str, ...] = (
    "campaigns",
    "campaign_items",
    "monthly_reviews",
    "saved_inspiration_posts",
    "inspiration_transforms",
    "audit_logs",
)

PHASE_511_VIEWS: tuple[str, ...] = ("v_campaign_progress",)

PHASE_511_SETTINGS: tuple[str, ...] = (
    "inspiration_plagiarism_jaccard_high_threshold",
    "inspiration_plagiarism_jaccard_medium_threshold",
    "inspiration_plagiarism_ngram_high_threshold",
    "inspiration_plagiarism_ngram_medium_threshold",
    "monthly_review_auto_draft_enabled",
    "audit_log_retention_days",
    "calendar_default_view",
    "calendar_am_cutoff_hour",
)


def test_phase511_tables_exist(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_511_TABLES) - names
    assert not missing, f"Phase 5.11 tables missing: {missing}"


def test_phase511_v_campaign_progress_compiles(db_conn: sqlite3.Connection) -> None:
    for view in PHASE_511_VIEWS:
        db_conn.execute(f"SELECT * FROM {view} LIMIT 0")


def test_phase511_campaigns_status_check(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        """
        INSERT INTO campaigns (name, start_date, end_date)
        VALUES ('p1', '2026-05-01', '2026-05-28')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO campaigns (name, status, start_date, end_date)
            VALUES ('bad', 'paused', '2026-05-01', '2026-05-28')
            """
        )


def test_phase511_campaigns_date_check(db_conn: sqlite3.Connection) -> None:
    # CHECK (start_date <= end_date) rejects inverted ranges.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO campaigns (name, start_date, end_date)
            VALUES ('inverted', '2026-06-01', '2026-05-01')
            """
        )


def test_phase511_campaign_items_cascades_on_campaign_delete(
    db_conn: sqlite3.Connection,
) -> None:
    camp_id = db_conn.execute(
        """
        INSERT INTO campaigns (name, start_date, end_date)
        VALUES ('p', '2026-05-01', '2026-05-28')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO campaign_items (campaign_id, item_type)
        VALUES (?, 'post')
        """,
        (camp_id,),
    )
    db_conn.execute("DELETE FROM campaigns WHERE id = ?", (camp_id,))
    remaining = db_conn.execute(
        "SELECT COUNT(*) FROM campaign_items WHERE campaign_id = ?", (camp_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_phase511_campaign_items_status_check(db_conn: sqlite3.Connection) -> None:
    camp_id = db_conn.execute(
        """
        INSERT INTO campaigns (name, start_date, end_date)
        VALUES ('p', '2026-05-01', '2026-05-28')
        RETURNING id
        """
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO campaign_items (campaign_id, item_type, status)
            VALUES (?, 'post', 'archived')
            """,
            (camp_id,),
        )


def test_phase511_monthly_reviews_unique_iso_month(db_conn: sqlite3.Connection) -> None:
    db_conn.execute("INSERT INTO monthly_reviews (iso_month) VALUES ('2026-05')")
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("INSERT INTO monthly_reviews (iso_month) VALUES ('2026-05')")


def test_phase511_monthly_reviews_confidence_label_check(
    db_conn: sqlite3.Connection,
) -> None:
    db_conn.execute(
        "INSERT INTO monthly_reviews (iso_month, confidence_label) VALUES ('2026-06', 'fact')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO monthly_reviews (iso_month, confidence_label)
            VALUES ('2026-07', 'maybe')
            """
        )


def test_phase511_saved_inspiration_hash_unique(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        """
        INSERT INTO saved_inspiration_posts (source_post_text, source_text_hash)
        VALUES ('hello', 'abc123')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO saved_inspiration_posts (source_post_text, source_text_hash)
            VALUES ('different text but same hash', 'abc123')
            """
        )


def test_phase511_inspiration_transforms_mode_check(db_conn: sqlite3.Connection) -> None:
    src_id = db_conn.execute(
        """
        INSERT INTO saved_inspiration_posts (source_post_text, source_text_hash)
        VALUES ('hi', 'h1')
        RETURNING id
        """
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO inspiration_transforms
              (saved_inspiration_id, transform_mode, output_text, output_text_hash, model_used)
            VALUES (?, 'rewrite_in_pirate_voice', 'arr', 'h2', 'claude-opus-4-7')
            """,
            (src_id,),
        )


def test_phase511_inspiration_transforms_cascades_on_source_delete(
    db_conn: sqlite3.Connection,
) -> None:
    src_id = db_conn.execute(
        """
        INSERT INTO saved_inspiration_posts (source_post_text, source_text_hash)
        VALUES ('hi', 'h3')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO inspiration_transforms
          (saved_inspiration_id, transform_mode, output_text, output_text_hash, model_used)
        VALUES (?, 'structure', 'pattern', 'h4', 'claude-opus-4-7')
        """,
        (src_id,),
    )
    db_conn.execute("DELETE FROM saved_inspiration_posts WHERE id = ?", (src_id,))
    remaining = db_conn.execute(
        "SELECT COUNT(*) FROM inspiration_transforms WHERE saved_inspiration_id = ?",
        (src_id,),
    ).fetchone()[0]
    assert remaining == 0


def test_phase511_audit_logs_category_check(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        "INSERT INTO audit_logs (event_category, event_type) VALUES ('settings', 'test')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO audit_logs (event_category, event_type) VALUES ('unknown', 'test')"
        )


def test_phase511_audit_logs_migration_row_present(db_conn: sqlite3.Connection) -> None:
    # Migration 015's final statement logs its own application.
    row = db_conn.execute(
        """
        SELECT event_category, event_type, success
        FROM audit_logs
        WHERE event_category = 'migration' AND event_type = 'migration_applied_015'
        """
    ).fetchone()
    assert row is not None
    assert row["success"] == 1


def test_phase511_v_campaign_progress_math(db_conn: sqlite3.Connection) -> None:
    camp_id = db_conn.execute(
        """
        INSERT INTO campaigns (name, start_date, end_date)
        VALUES ('p', '2026-05-01', '2026-05-28')
        RETURNING id
        """
    ).fetchone()[0]
    # 3 items: 2 shipped, 1 planned → percent_shipped = 2/3, percent_planned_shipped = 2/3.
    for status in ("shipped", "shipped", "planned"):
        db_conn.execute(
            """
            INSERT INTO campaign_items (campaign_id, item_type, status)
            VALUES (?, 'post', ?)
            """,
            (camp_id, status),
        )
    row = db_conn.execute(
        """
        SELECT items_total, items_shipped, items_planned,
               percent_shipped, percent_planned_shipped
        FROM v_campaign_progress WHERE campaign_id = ?
        """,
        (camp_id,),
    ).fetchone()
    assert row["items_total"] == 3
    assert row["items_shipped"] == 2
    assert row["items_planned"] == 1
    assert abs(row["percent_shipped"] - (2 / 3)) < 1e-9
    assert abs(row["percent_planned_shipped"] - (2 / 3)) < 1e-9


def test_phase511_v_campaign_progress_null_when_empty(
    db_conn: sqlite3.Connection,
) -> None:
    camp_id = db_conn.execute(
        """
        INSERT INTO campaigns (name, start_date, end_date)
        VALUES ('empty', '2026-05-01', '2026-05-28')
        RETURNING id
        """
    ).fetchone()[0]
    row = db_conn.execute(
        """
        SELECT items_total, percent_shipped, percent_planned_shipped
        FROM v_campaign_progress WHERE campaign_id = ?
        """,
        (camp_id,),
    ).fetchone()
    assert row["items_total"] == 0
    assert row["percent_shipped"] is None
    assert row["percent_planned_shipped"] is None


def test_phase511_settings_rows_present(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    missing = set(PHASE_511_SETTINGS) - keys
    assert not missing, f"missing Phase 5.11 settings rows: {missing}"


# ---------------------------------------------------------------------------
# Phase 6 — Long-form Blogs (migration 016).
# ---------------------------------------------------------------------------
PHASE_6_TABLES: tuple[str, ...] = (
    "blogs",
    "blog_versions",
    "blog_exports",
    "blog_to_post_links",
)

PHASE_6_VIEWS: tuple[str, ...] = ("v_blog_pipeline",)

PHASE_6_SETTINGS: tuple[str, ...] = (
    "blog_stale_status_warning_days",
    "blog_default_target_length_words",
    "blog_export_default_directory",
    "blog_repurposing_plagiarism_check_enabled",
    "blog_agent_max_draft_iterations",
)


def test_phase6_tables_exist(db_conn: sqlite3.Connection) -> None:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    missing = set(PHASE_6_TABLES) - names
    assert not missing, f"Phase 6 tables missing: {missing}"


def test_phase6_v_blog_pipeline_compiles(db_conn: sqlite3.Connection) -> None:
    for view in PHASE_6_VIEWS:
        db_conn.execute(f"SELECT * FROM {view} LIMIT 0")


def test_phase6_blogs_status_check(db_conn: sqlite3.Connection) -> None:
    db_conn.execute(
        "INSERT INTO blogs (slug, title, status) VALUES ('a', 'A', 'idea')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO blogs (slug, title, status) VALUES ('b', 'B', 'in_progress')"
        )


def test_phase6_blogs_slug_unique(db_conn: sqlite3.Connection) -> None:
    db_conn.execute("INSERT INTO blogs (slug, title) VALUES ('dup', 'first')")
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("INSERT INTO blogs (slug, title) VALUES ('dup', 'second')")


def test_phase6_blog_versions_unique_version_per_blog(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('uvc', 't') RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_text_hash, title_at_version,
           status_at_version, created_by)
        VALUES (?, 1, 'h1', 't', 'idea', 'daniel')
        """,
        (blog_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO blog_versions
              (blog_id, version_number, body_text_hash, title_at_version,
               status_at_version, created_by)
            VALUES (?, 1, 'h2', 't', 'idea', 'daniel')
            """,
            (blog_id,),
        )


def test_phase6_blog_versions_partial_unique_current(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('puc', 't') RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_text_hash, title_at_version,
           status_at_version, created_by, is_current_for_blog)
        VALUES (?, 1, 'h', 't', 'idea', 'daniel', 1)
        """,
        (blog_id,),
    )
    # Second is_current_for_blog=1 row for the same blog violates the
    # partial unique index.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO blog_versions
              (blog_id, version_number, body_text_hash, title_at_version,
               status_at_version, created_by, is_current_for_blog)
            VALUES (?, 2, 'h2', 't', 'idea', 'daniel', 1)
            """,
            (blog_id,),
        )


def test_phase6_blog_versions_cascades_on_blog_delete(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('cas', 't') RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_text_hash, title_at_version,
           status_at_version, created_by)
        VALUES (?, 1, 'h', 't', 'idea', 'daniel')
        """,
        (blog_id,),
    )
    db_conn.execute("DELETE FROM blogs WHERE id = ?", (blog_id,))
    remaining = db_conn.execute(
        "SELECT COUNT(*) FROM blog_versions WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_phase6_blog_exports_format_check(db_conn: sqlite3.Connection) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('exf', 't') RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_exports
          (blog_id, format, target_path, file_size_bytes, content_sha256)
        VALUES (?, 'markdown', '/tmp/a.md', 100, 'abc')
        """,
        (blog_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO blog_exports
              (blog_id, format, target_path, file_size_bytes, content_sha256)
            VALUES (?, 'rst', '/tmp/b.rst', 50, 'def')
            """,
            (blog_id,),
        )


def test_phase6_blog_to_post_links_unique_triple(
    db_conn: sqlite3.Connection,
) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('lnk', 't') RETURNING id"
    ).fetchone()[0]
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'hi', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_to_post_links
          (blog_id, post_id, direction, relationship_kind)
        VALUES (?, ?, 'blog_to_post', 'thread_root')
        """,
        (blog_id, post_id),
    )
    # Same direction → duplicate.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO blog_to_post_links
              (blog_id, post_id, direction, relationship_kind)
            VALUES (?, ?, 'blog_to_post', 'companion_post')
            """,
            (blog_id, post_id),
        )
    # Different direction for the same pair is permitted.
    db_conn.execute(
        """
        INSERT INTO blog_to_post_links
          (blog_id, post_id, direction, relationship_kind)
        VALUES (?, ?, 'parallel', 'companion_post')
        """,
        (blog_id, post_id),
    )


def test_phase6_blog_to_post_links_cascades(db_conn: sqlite3.Connection) -> None:
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('cas2', 't') RETURNING id"
    ).fetchone()[0]
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status)
        VALUES ('2026-05-22', 'hi', 'standalone', 'manual', 'confirmed')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_to_post_links
          (blog_id, post_id, direction, relationship_kind)
        VALUES (?, ?, 'blog_to_post', 'thread_root')
        """,
        (blog_id, post_id),
    )
    db_conn.execute("DELETE FROM blogs WHERE id = ?", (blog_id,))
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM blog_to_post_links WHERE blog_id = ?", (blog_id,)
        ).fetchone()[0]
        == 0
    )


def test_phase6_settings_rows_present(db_conn: sqlite3.Connection) -> None:
    keys = {row["key"] for row in db_conn.execute("SELECT key FROM settings").fetchall()}
    missing = set(PHASE_6_SETTINGS) - keys
    assert not missing, f"missing Phase 6 settings rows: {missing}"


def test_phase6_audit_logs_migration_row_present(db_conn: sqlite3.Connection) -> None:
    row = db_conn.execute(
        """
        SELECT event_category, event_type, success
        FROM audit_logs
        WHERE event_category = 'migration' AND event_type = 'migration_applied_016'
        """
    ).fetchone()
    assert row is not None
    assert row["success"] == 1


def test_phase6_blog_versions_x_to_blog_idea_outline_action_admitted(
    db_conn: sqlite3.Connection,
) -> None:
    """P6R-17: migration 017 extends the agent_action CHECK to admit
    'x_to_blog_idea_outline' so the X→blog seed-outline path is
    distinguishable from the standalone outline_blog tool."""
    blog_id = db_conn.execute(
        "INSERT INTO blogs (slug, title) VALUES ('xtb', 't') RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_text_hash, title_at_version,
           status_at_version, created_by, agent_action)
        VALUES (?, 1, 'h', 't', 'idea', 'agent', 'x_to_blog_idea_outline')
        """,
        (blog_id,),
    )
    row = db_conn.execute(
        "SELECT agent_action FROM blog_versions WHERE blog_id = ?", (blog_id,)
    ).fetchone()
    assert row["agent_action"] == "x_to_blog_idea_outline"


def test_phase6_v_blog_pipeline_basic_rollup(db_conn: sqlite3.Connection) -> None:
    blog_id = db_conn.execute(
        """
        INSERT INTO blogs (slug, title, status, target_length_words, actual_length_words)
        VALUES ('roll', 't', 'drafting', 1000, 750) RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_text_hash, title_at_version,
           status_at_version, created_by, is_current_for_blog)
        VALUES (?, 1, 'h', 't', 'drafting', 'daniel', 1)
        """,
        (blog_id,),
    )
    row = db_conn.execute(
        """
        SELECT status, current_version_number, total_version_count,
               actual_length_words, target_length_words, length_gap_words,
               export_count
        FROM v_blog_pipeline WHERE blog_id = ?
        """,
        (blog_id,),
    ).fetchone()
    assert row["status"] == "drafting"
    assert row["current_version_number"] == 1
    assert row["total_version_count"] == 1
    assert row["length_gap_words"] == -250
    assert row["export_count"] == 0
