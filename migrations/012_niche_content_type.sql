-- migrations/012_niche_content_type.sql — Phase 5.9 Niche & Content-Type
-- Calibration Pack.
--
-- Six concerns, one migration: structured niche definition (§28.16),
-- V/G/P/P content type axis (§28.17), reply-quality lint persistence
-- (§28.18), follower-velocity projection view (§28.19), replier-pool
-- candidate-discovery enum extension (§28.20), and the personality
-- lore registry (§28.21). All six ship as one transactional unit
-- because the rule #15 orchestrator gate (§28.2) reads the niche
-- settings rows on first boot and would refuse every draft if the
-- table had only a partial schema.
--
-- All statements are idempotent. ADD COLUMN runs unconditionally per
-- the existing migration pattern (apply_migrations records each file
-- in schema_migrations so we never re-run); CREATE TABLE / INDEX /
-- VIEW use IF NOT EXISTS / DROP+CREATE; settings rows use INSERT OR
-- IGNORE.

-- ---------------------------------------------------------------------------
-- 1. posts.content_type — V/G/P/P axis (§28.17, §10).
-- ---------------------------------------------------------------------------
-- Default 'unspecified'. Existing rows backfill via the column default,
-- so the ADD COLUMN does the backfill in one shot — no follow-up UPDATE
-- needed. §28.17 explicitly forbids retro-classification.
ALTER TABLE posts ADD COLUMN content_type TEXT NOT NULL DEFAULT 'unspecified'
    CHECK (content_type IN ('value', 'growth', 'personality', 'proof', 'unspecified'));

CREATE INDEX IF NOT EXISTS idx_posts_content_type
    ON posts (content_type);

-- ---------------------------------------------------------------------------
-- 2. agent_drafts.content_type — required-non-unspecified on save (§28.17, §10).
-- ---------------------------------------------------------------------------
-- CHECK permits 'unspecified' so legacy rows survive the migration, but
-- the orchestrator (app/agent/tools.py::_save_draft_*) refuses to write
-- a new row with that value. The split lets the migration backfill
-- cleanly without weakening the runtime contract.
ALTER TABLE agent_drafts ADD COLUMN content_type TEXT
    CHECK (content_type IS NULL
           OR content_type IN ('value', 'growth', 'personality', 'proof', 'unspecified'));

CREATE INDEX IF NOT EXISTS idx_agent_drafts_content_type
    ON agent_drafts (content_type);

-- ---------------------------------------------------------------------------
-- 3. agent_drafts.reply_quality_lint_passed — §28.18 persistence.
-- ---------------------------------------------------------------------------
-- Nullable boolean (0/1). NULL when the draft isn't a reply or the lint
-- was disabled via the gating setting; true on pass, false on fail.
-- False counts as a failed IWH revision through the same path as
-- dark_pattern_lint_passed (see decide_save_or_revise).
ALTER TABLE agent_drafts ADD COLUMN reply_quality_lint_passed INTEGER
    CHECK (reply_quality_lint_passed IS NULL
           OR reply_quality_lint_passed IN (0, 1));

-- ---------------------------------------------------------------------------
-- 4. personality_lore — Daniel-curated lore registry (§10, §28.21).
-- ---------------------------------------------------------------------------
-- The agent has NO write access; no AGENT_TOOLS entry references this
-- table. Startup-time assertion in app/main.py enforces the exclusion
-- (same pattern as _assert_publish_tools_unreachable).
CREATE TABLE IF NOT EXISTS personality_lore (
    id                     INTEGER PRIMARY KEY,
    theme                  TEXT NOT NULL,
    description            TEXT NOT NULL,
    example_posts_json     TEXT,
    invocation_count       INTEGER NOT NULL DEFAULT 0
        CHECK (invocation_count >= 0),
    last_invoked_at_utc    TEXT,
    is_active              INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    priority               INTEGER NOT NULL DEFAULT 100,
    added_at_utc           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Partial index — splice query orders by (priority, id) where active.
CREATE INDEX IF NOT EXISTS idx_personality_lore_active_priority
    ON personality_lore (priority, id) WHERE is_active = 1;

-- ---------------------------------------------------------------------------
-- 5. reply_targets.source — third discovery path (§28.20).
-- ---------------------------------------------------------------------------
-- Phase 5.6 (migration 009) gave reply_targets a `source_platform`
-- column ('x' default) but NO `source` column. Add it here. CHECK
-- carries the three §28.20 values; default 'paste_url' so the existing
-- two discovery paths (manual paste, agent-curated account suggestion)
-- have a sensible backfill.
ALTER TABLE reply_targets ADD COLUMN source TEXT NOT NULL DEFAULT 'paste_url'
    CHECK (source IN ('paste_url', 'agent_curated_account', 'replier_under_thread'));

CREATE INDEX IF NOT EXISTS idx_reply_targets_source
    ON reply_targets (source);

-- ---------------------------------------------------------------------------
-- 6. v_content_type_performance — graduated-confidence slice by content_type (§11).
-- ---------------------------------------------------------------------------
-- Mirrors v_lane_performance: same percentile aggregate, same threshold
-- ladder, same confidence_label strings. Rows with content_type =
-- 'unspecified' are EXCLUDED — the view is for active learning, not for
-- displaying an unclassified backlog.
DROP VIEW IF EXISTS v_content_type_performance;
CREATE VIEW v_content_type_performance AS
WITH post_ct AS (
    SELECT
        p.content_type,
        plm.post_id,
        plm.impressions,
        plm.engagement_rate,
        plm.bookmarks,
        plm.replies,
        p.created_date
    FROM v_post_latest_metrics plm
    JOIN posts p ON p.id = plm.post_id
    WHERE p.content_type IS NOT NULL
      AND p.content_type != 'unspecified'
),
agg AS (
    SELECT
        content_type,
        COUNT(*)                                       AS post_count,
        COUNT(DISTINCT created_date)                   AS days_covered,
        percentile(impressions, 0.5)                   AS median_impressions,
        percentile(impressions, 0.25)                  AS iqr_impressions_low,
        percentile(impressions, 0.75)                  AS iqr_impressions_high,
        percentile(engagement_rate, 0.5)               AS median_engagement_rate,
        percentile(engagement_rate, 0.25)              AS iqr_engagement_rate_low,
        percentile(engagement_rate, 0.75)              AS iqr_engagement_rate_high,
        SUM(COALESCE(bookmarks, 0))                    AS total_bookmarks,
        SUM(COALESCE(replies, 0))                      AS total_replies
    FROM post_ct
    GROUP BY content_type
)
SELECT
    a.content_type,
    a.post_count,
    a.days_covered,
    a.median_impressions,
    a.iqr_impressions_low,
    a.iqr_impressions_high,
    a.median_engagement_rate,
    a.iqr_engagement_rate_low,
    a.iqr_engagement_rate_high,
    a.total_bookmarks,
    a.total_replies,
    -- Mirror v_lane_performance: stir signal count anchored to LATEST
    -- per-post content_type via the posts row (no reclassification here
    -- because content_type lives on posts, not on a classification
    -- side-table — one value per post by construction).
    (SELECT COUNT(*)
       FROM stir_conversion_events sce
       JOIN posts p2 ON p2.id = sce.referring_post_id
      WHERE p2.content_type = a.content_type)         AS stir_signal_count,
    CASE
        WHEN a.post_count < 5 OR a.days_covered < 3 THEN 'insufficient sample'
        WHEN a.post_count < 15                      THEN 'low — show scatter, do not rank'
        WHEN a.post_count >= 30 AND a.days_covered >= 14 THEN 'stronger'
        WHEN a.post_count >= 15 AND a.days_covered >= 7  THEN 'moderate'
        ELSE 'moderate'
    END                                                AS confidence_label
FROM agg a;

-- ---------------------------------------------------------------------------
-- 7. v_follower_velocity — projection math with noise-floor suppression (§11, §28.19).
-- ---------------------------------------------------------------------------
-- Anchored on the latest v_account_daily row. Suppresses ALL projection
-- columns to NULL when |delta_7d| < velocity_projection_noise_floor_followers
-- OR velocity_7d_per_day <= 0. §28.19's hard rule: never display a precise
-- date when the input is noise.
--
-- current_milestone_target sourced from the same `current_milestone`
-- setting v_account_daily already reads.
--
-- projected_milestone_hit_date_at_7d_pace uses julianday math:
--   date('now', '+N days') where N = distance_to_current_milestone / velocity_7d_per_day
--   Computed via SQLite's datetime() with rounded day count.
DROP VIEW IF EXISTS v_follower_velocity;
CREATE VIEW v_follower_velocity AS
WITH latest AS (
    SELECT *
    FROM v_account_daily
    ORDER BY snapshot_date DESC
    LIMIT 1
),
noise_floor AS (
    SELECT COALESCE(
        (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
           FROM settings WHERE key = 'velocity_projection_noise_floor_followers'),
        10
    ) AS threshold
),
milestone AS (
    SELECT COALESCE(
        (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
           FROM settings WHERE key = 'current_milestone'),
        100
    ) AS current_milestone_target
)
SELECT
    l.snapshot_date,
    l.followers_count,
    l.velocity_7d_per_day,
    l.velocity_30d_per_day,
    m.current_milestone_target,
    (m.current_milestone_target - l.followers_count) AS distance_to_current_milestone,

    -- projected_milestone_hit_date_at_7d_pace: NULL on noise floor, on
    -- zero/negative velocity, or when the milestone is already met.
    CASE
        WHEN l.delta_7d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_7d_per_day IS NULL
          OR l.velocity_7d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE DATE('now', '+' ||
                  CAST(ROUND(
                      (m.current_milestone_target - l.followers_count) * 1.0
                      / l.velocity_7d_per_day
                  ) AS INTEGER) || ' days')
    END                                              AS projected_milestone_hit_date_at_7d_pace,

    CASE
        WHEN l.delta_30d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_30d_per_day IS NULL
          OR l.velocity_30d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE DATE('now', '+' ||
                  CAST(ROUND(
                      (m.current_milestone_target - l.followers_count) * 1.0
                      / l.velocity_30d_per_day
                  ) AS INTEGER) || ' days')
    END                                              AS projected_milestone_hit_date_at_30d_pace,

    CASE
        WHEN l.delta_7d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_7d_per_day IS NULL
          OR l.velocity_7d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE CAST(ROUND(
                (m.current_milestone_target - l.followers_count) * 1.0
                / l.velocity_7d_per_day
             ) AS INTEGER)
    END                                              AS days_until_milestone_at_7d_pace,

    CASE
        WHEN l.delta_30d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_30d_per_day IS NULL
          OR l.velocity_30d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE CAST(ROUND(
                (m.current_milestone_target - l.followers_count) * 1.0
                / l.velocity_30d_per_day
             ) AS INTEGER)
    END                                              AS days_until_milestone_at_30d_pace
FROM latest l
CROSS JOIN noise_floor n
CROSS JOIN milestone m;

-- ---------------------------------------------------------------------------
-- 8. New settings rows (§25 Phase 5.9 migration checklist).
-- ---------------------------------------------------------------------------
-- INSERT OR IGNORE keeps re-application idempotent. The
-- velocity_projection_noise_floor_followers value is documented as an
-- explicit copy of §13's velocity_7d_display_threshold so the
-- suppression rule is auditable from one place — do NOT silently alias.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('niche_problem', '""',
     'One-sentence: the problem you solve. Empty BLOCKS agent drafting per §28.2 rule #15.'),
    ('niche_person', '""',
     'One-sentence: the person you solve it for. Empty BLOCKS agent drafting per §28.2 rule #15.'),
    ('reply_quality_lint_enabled', 'true',
     'Toggle the §28.18 reply-quality lint pass. When false the lint short-circuits to passed=true with notes="lint disabled".'),
    ('personality_lore_overuse_threshold', '8',
     'invocation_count > this AND last_invoked_at_utc > now() - 30 days → "over-relied on" banner (§28.21).'),
    ('content_type_recommendation_window_days', '7',
     'Days of posts to inspect for the §14.1 Today content-type recommendation (§28.17).'),
    ('velocity_projection_noise_floor_followers', '10',
     'Hard floor: when |delta_7d| < this, v_follower_velocity returns NULL projections. Explicit copy of §13 velocity_7d_display_threshold so the suppression rule is auditable in one place (§28.19).'),
    ('personality_lore_splice_count', '5',
     'Top-N active personality_lore rows spliced into system prompt Section 5 (§28.21).');
