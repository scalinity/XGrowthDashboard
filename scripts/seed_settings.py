"""Seed the ``settings`` table with documented defaults.

Sources of truth (see ``spec.md``):
- §10.2 ``settings`` block — the required initial settings list.
- §14.7 Settings view — confirms the defaults surfaced in the UI.
- §27 — the operational anchors (5,000 ceiling, 500,000 long-arc reminder).

All rows are idempotent via ``INSERT OR IGNORE``: re-running the script never
overwrites a value the user has changed. ``value_json`` is JSON-encoded so the
view layer can ``json_extract(..., '$')`` to get a native SQLite type.

Phase 1 scope: only settings rows whose semantics exist by Phase 1. The Growth
Agent + X API posting rows defined in §10.2 are deferred to Phase 5.5 (their
backing tables and code paths don't exist yet, and seeding them here would
mis-document the system state).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Iterable

# (key, value, note)
# Value is the native Python value; the writer JSON-encodes it.
_PHASE_1_SETTINGS: list[tuple[str, object, str]] = [
    ("x_handle", "dannyscalant", "X handle without @ (§2)"),
    ("x_user_id", None, "Stable user identifier; populated when known"),
    ("profile_url", "https://x.com/dannyscalant", "Public profile URL"),
    ("baseline_followers", 61, "Followers at project start (§2)"),
    ("operational_ceiling", 5000, "Operational anchor — replaces 500k goal (§27)"),
    ("long_arc_reminder", 500000, "Display-only long-arc reminder (§27)"),
    ("current_milestone", 100, "First distribution rung target (§4)"),
    ("timezone", "America/New_York", "Daily snapshot ritual timezone"),
    ("daily_snapshot_time", "09:00", "Default daily account snapshot time"),
    ("daily_post_target", 1, "Daily post minimum (§14.1)"),
    (
        "daily_reply_target",
        12,
        "Daily reply minimum — raised from 5; 21-day calibration (§10.2)",
    ),
    (
        "daily_reply_session_target",
        1,
        "Deliberate reply workout sessions/day (§14.1)",
    ),
    (
        "target_calibration_review_date",
        # Computed at module import time; INSERT OR IGNORE means whichever
        # invocation seeds the row first wins, and re-running on a later day
        # never updates the stored value. To re-calibrate, edit the row
        # explicitly in the Settings view (Phase 3).
        (date.today() + timedelta(days=21)).isoformat(),
        "Settings prompt: review reply target adherence on this date",
    ),
    (
        "weekly_report_export_path",
        "data/exports",
        "Default folder for Markdown weekly reports (§14.7)",
    ),
    (
        "data_collection_mode",
        "manual",
        "manual | xurl | api — MVP default per §17",
    ),
    # Sample-size thresholds used by v_lane_performance.
    # Stored here so the UI can expose them; the view's CASE expression hard-
    # codes the same numbers per §11.
    ("lane_sample_size_insufficient", 5, "post_count < 5 → insufficient (§11)"),
    ("lane_sample_size_low", 15, "post_count < 15 → low / scatter-only (§11)"),
    (
        "lane_sample_size_stronger",
        30,
        "post_count >= 30 AND days_covered >= 14 → stronger (§11)",
    ),
    (
        "lane_days_covered_minimum",
        3,
        "days_covered < 3 → insufficient regardless of post count (§11)",
    ),
    # Velocity suppression threshold (§13 rule 6).
    (
        "velocity_7d_display_threshold",
        10,
        "|delta_7d| >= 10 required to show velocity_7d_per_day (§13)",
    ),
    # Backup/export directories.
    ("backup_dir", "data/backups", "VACUUM INTO target (§18 rule 10)"),
    ("export_dir", "data/exports", "CSV/Markdown export output folder (§14.7)"),
    # Counterfactual enforcement (§14.6).
    (
        "counterfactual_required",
        True,
        "Weekly review requires counterfactual_note before save (§14.6)",
    ),
    # Phase 5.5 Growth Agent (§28).
    (
        "agent_default_model",
        "claude-opus-4-7",
        "Default Anthropic model for the Growth Agent (§28.4)",
    ),
    (
        "agent_monthly_cost_cap_usd",
        25.0,
        "Monthly USD cap for agent calls (§28.6); raise here when needed",
    ),
    (
        "agent_voice_sample_count",
        5,
        "Top N active voice_samples spliced into system prompt (§28.5)",
    ),
    (
        "iwh_self_score_minimum",
        2,
        "Minimum per-axis IWH score required to ship a draft (§28.2 rule #13)",
    ),
    (
        "iwh_max_revision_attempts",
        3,
        "Refuse save on attempt N+1 (§28.2 rule #13)",
    ),
    # Note: there is intentionally NO `agent_dark_pattern_lint_enabled`
    # setting. Per spec §28.2 rule #12 the lint pass is non-bypassable —
    # exposing a toggle would contradict the rule. A toggle existed in a
    # prior phase but was removed in /address W2 because the wiring was
    # also missing on the read side (decide_save_or_revise never consulted
    # the setting), so it was a UX promise the code never kept.
    (
        "x_posting_confirmation_token_ttl_seconds",
        60,
        "TTL for single-use publish_confirmation_tokens (§28.10)",
    ),
    # Phase 5.9 — Niche & Content-Type Calibration Pack (§28.16-§28.21).
    # Values mirror migration 012_niche_content_type.sql so a fresh DB
    # initialized via init_db agrees with one initialized via raw
    # migrations only; INSERT OR IGNORE on both sides keeps re-runs safe.
    (
        "niche_problem",
        "",
        "One-sentence: the problem you solve. Empty BLOCKS agent drafting (§28.2 rule #15).",
    ),
    (
        "niche_person",
        "",
        "One-sentence: the person you solve it for. Empty BLOCKS agent drafting (§28.2 rule #15).",
    ),
    (
        "reply_quality_lint_enabled",
        True,
        "Toggle the §28.18 reply-quality lint pass. False → short-circuits to passed=true.",
    ),
    (
        "personality_lore_overuse_threshold",
        8,
        "invocation_count > this AND last_invoked_at_utc > now()-30d → over-relied banner (§28.21).",
    ),
    (
        "content_type_recommendation_window_days",
        7,
        "Days of posts inspected for the §14.1 Today content-type recommendation (§28.17).",
    ),
    (
        "velocity_projection_noise_floor_followers",
        10,
        "Hard floor: |delta_7d| < this → v_follower_velocity returns NULL projections. Explicit copy of §13 velocity_7d_display_threshold so suppression is auditable in one place (§28.19).",
    ),
    (
        "personality_lore_splice_count",
        5,
        "Top-N active personality_lore rows spliced into system prompt Section 5 (§28.21).",
    ),
    # Phase 5.10 — Strategic Analysis Pack (§28.22-§28.25). Mirrors
    # migration 013_strategic_analysis.sql so a fresh DB initialized via
    # init_db agrees with one initialized via raw migrations only.
    (
        "coach_refuse_without_evidence",
        True,
        "When true (default), Coach messages with zero surviving citations + analytical claims are replaced with a canonical refusal before persistence (§28.23).",
    ),
    (
        "coach_citation_strip_log_threshold",
        3,
        "Average citations stripped per Coach message over the last 20; exceeding this surfaces a strip-rate-high banner in Settings (§28.23).",
    ),
    (
        "brain_dump_max_candidate_drafts",
        5,
        "Hard ceiling on candidate drafts returned per Brain Dump processing pass (§28.22).",
    ),
    (
        "profile_audit_recent_posts_window_days",
        30,
        "Days of recent posts fed into the §28.25 Profile Audit when Daniel doesn't override on the form.",
    ),
    (
        "profile_audit_cadence_reminder_days",
        90,
        "After this many days since last audit, §14.7 field 12 surfaces a yellow reminder banner. Audits NEVER auto-run (§28.25).",
    ),
]


def seed_settings(conn: sqlite3.Connection, rows: Iterable[tuple[str, object, str]] | None = None) -> int:
    """Insert documented settings rows. Returns the number newly inserted."""
    payload = list(rows) if rows is not None else _PHASE_1_SETTINGS
    inserted = 0
    for key, value, note in payload:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO settings (key, value_json, note)
            VALUES (?, ?, ?)
            """,
            (key, json.dumps(value), note),
        )
        inserted += cursor.rowcount or 0
    return inserted


def documented_keys() -> list[str]:
    """Return every settings key Phase 1 expects to exist after seeding."""
    return [k for (k, _v, _note) in _PHASE_1_SETTINGS]
