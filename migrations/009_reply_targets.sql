-- Phase 5.6 — Reply Target Discovery (spec §29).
--
-- This migration is one transactional unit conceptually but is applied via
-- ``executescript`` (see app/db.py::apply_migrations), so each statement
-- runs in autocommit mode and IF NOT EXISTS guards every CREATE for
-- idempotent re-application.
--
-- The order is fixed:
--   1. ``reply_targets`` table + CHECK constraints + indexes.
--   2. ``posts`` additions (the queue's "mark posted" handler depends on
--      both columns existing before the FK can be added).
--   3. ``reply_sessions`` addition.
--   4. ``settings`` seeds via INSERT OR IGNORE.
--   5. Drop and recreate ``v_daily_reps`` with the four §29.9 columns.

-- ---------------------------------------------------------------------------
-- 1. reply_targets — the new MVP table per §29.6.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reply_targets (
    id                              INTEGER PRIMARY KEY,
    discovered_at_utc               TEXT NOT NULL DEFAULT (datetime('now')),

    -- §29.6 enum — 'grok_semantic' is V1.2-deferred per §29.1 and is
    -- DELIBERATELY OMITTED from the CHECK at this phase.
    discovered_via                  TEXT NOT NULL
        CHECK (discovered_via IN ('manual', 'agent_score', 'next_rep_seed',
                                   'v1.1_api_search')),

    -- Target identity.
    source_platform                 TEXT NOT NULL DEFAULT 'x',
    target_post_url                 TEXT NOT NULL,
    target_x_post_id                TEXT,                  -- nullable until parsed
    target_author_handle            TEXT NOT NULL,
    target_author_display_name      TEXT,
    target_author_follower_count    INTEGER,

    -- Target content.
    target_text                     TEXT,
    target_created_at_utc           TEXT,
    post_age_minutes                INTEGER,

    -- Engagement snapshot (latest known; per-snapshot history is V1.1+).
    last_checked_at_utc             TEXT NOT NULL DEFAULT (datetime('now')),
    like_count                      INTEGER,
    reply_count                     INTEGER,
    repost_count                    INTEGER,
    quote_count                     INTEGER,
    bookmark_count                  INTEGER,
    impression_count                INTEGER,

    -- Scores (0..3 each; NULL when not yet scored or V1.1+).
    relevance_score                 INTEGER
        CHECK (relevance_score          IS NULL OR (relevance_score          BETWEEN 0 AND 3)),
    engagement_surface_score        INTEGER
        CHECK (engagement_surface_score IS NULL OR (engagement_surface_score BETWEEN 0 AND 3)),
    saturation_score                INTEGER
        CHECK (saturation_score         IS NULL OR (saturation_score         BETWEEN 0 AND 3)),
    reply_opportunity_score         INTEGER
        CHECK (reply_opportunity_score  IS NULL OR (reply_opportunity_score  BETWEEN 0 AND 3)),
    velocity_score                  INTEGER
        CHECK (velocity_score           IS NULL OR (velocity_score           BETWEEN 0 AND 3)),
    timing_score                    INTEGER
        CHECK (timing_score             IS NULL OR (timing_score             BETWEEN 0 AND 3)),
    audience_quality_score          INTEGER
        CHECK (audience_quality_score   IS NULL OR (audience_quality_score   BETWEEN 0 AND 3)),

    recommended_action_label        TEXT
        CHECK (recommended_action_label IS NULL OR
               recommended_action_label IN ('reply_now', 'reply_if_time', 'consider', 'skip')),
    recommended_action_score        INTEGER
        CHECK (recommended_action_score IS NULL OR (recommended_action_score BETWEEN 0 AND 3)),
    score_rationale                 TEXT,

    -- Taxonomy (Daniel's intended angle if/when he replies).
    pillar                          TEXT,
    audience                        TEXT,
    reply_intent                    TEXT
        CHECK (reply_intent IS NULL OR
               reply_intent IN ('growth', 'icp_discovery', 'relationship',
                                 'product_adjacent', 'thought_leadership')),
    topic_tags_json                 TEXT,

    -- Lint pass output (V1.1+).
    lint_thread_classification_json TEXT,
    lint_blocked                    INTEGER NOT NULL DEFAULT 0
        CHECK (lint_blocked IN (0, 1)),

    -- Status lifecycle.
    status                          TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'drafted', 'posted', 'skipped',
                          'expired', 'target_deleted')),
    skip_reason                     TEXT
        CHECK (skip_reason IS NULL OR
               skip_reason IN ('off_topic', 'ragebait', 'saturation',
                                'cant_add_value', 'target_deleted',
                                'blocked_by_author', 'other')),
    expired_at_utc                  TEXT,

    -- Cross-references.
    agent_draft_id                  INTEGER REFERENCES agent_drafts(id) ON DELETE SET NULL,
    posted_reply_post_id            INTEGER REFERENCES posts(id)        ON DELETE SET NULL,

    -- Audit.
    created_via_agent_message_id    INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
    notes                           TEXT
);

-- §29.6 unique on target_post_url — the duplicate-URL rejection enforced by
-- the Queue's "Add candidate" form ultimately leans on this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_targets_url
    ON reply_targets (target_post_url);

-- §29.6 partial unique on target_x_post_id where not null. SQLite supports
-- partial indexes natively.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_targets_x_post_id
    ON reply_targets (target_x_post_id)
    WHERE target_x_post_id IS NOT NULL;

-- §29.6 driving the Queue + §14.2 panel ordering.
CREATE INDEX IF NOT EXISTS idx_reply_targets_status_score
    ON reply_targets (status, recommended_action_score DESC, last_checked_at_utc DESC);

-- §29.6 partial index on reply_intent for postmortem queries; only shipped
-- replies need the lookup.
CREATE INDEX IF NOT EXISTS idx_reply_targets_intent_posted
    ON reply_targets (reply_intent)
    WHERE status = 'posted';

-- ---------------------------------------------------------------------------
-- 2. posts additions per §29.6.
-- SQLite cannot add a column with an inline REFERENCES clause via ALTER
-- TABLE, but it DOES allow adding the column nullable and relying on the
-- ON DELETE SET NULL rule via the existing FK enforcement (PRAGMA
-- foreign_keys = ON in app/db.py::connect). The FK is declared via a
-- generated index reference recorded in sqlite_master; the simpler safe
-- pattern is to add the column nullable and document the FK semantics. The
-- "Mark posted (manual)" handler enforces referential integrity in the
-- application layer.
--
-- We add an explicit index so the queue can join the other direction
-- (reply_targets.posted_reply_post_id is already FK'd above).
-- ---------------------------------------------------------------------------
ALTER TABLE posts ADD COLUMN in_reply_to_reply_target_id INTEGER
    REFERENCES reply_targets(id) ON DELETE SET NULL;

ALTER TABLE posts ADD COLUMN reply_intent TEXT
    CHECK (reply_intent IS NULL OR
           reply_intent IN ('growth', 'icp_discovery', 'relationship',
                             'product_adjacent', 'thought_leadership'));

CREATE INDEX IF NOT EXISTS idx_posts_in_reply_to_reply_target
    ON posts (in_reply_to_reply_target_id);

-- ---------------------------------------------------------------------------
-- 3. reply_sessions addition per §29.6.
-- ---------------------------------------------------------------------------
ALTER TABLE reply_sessions ADD COLUMN target_reply_target_ids_json TEXT;

-- ---------------------------------------------------------------------------
-- 4. settings seeds (eight rows per §29.6).
-- Each value is stored under ``value_json`` per the §10 settings convention
-- (JSON-scalar at the root). INSERT OR IGNORE keeps reseeding idempotent.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('engagement_surface_floor_likes',         '15',
     'Engagement-surface medium threshold floor (likes). §29.4.'),
    ('engagement_surface_pct_of_author',       '0.001',
     'Engagement-surface medium threshold as fraction of target author followers. §29.4.'),
    ('engagement_surface_high_floor_likes',    '50',
     'Engagement-surface high threshold floor (likes). §29.4.'),
    ('engagement_surface_high_pct',            '0.005',
     'Engagement-surface high threshold as fraction of target author followers. §29.4.'),
    ('reply_candidate_review_daily_target',    '15',
     'Daniel''s daily target for candidates reviewed (any status transition). §29.9.'),
    ('reply_high_engagement_mix_pct',          '0.5',
     'Target fraction of shipped replies with engagement_surface_score >= 2. §29.9.'),
    ('reply_target_expiry_hours',              '24',
     'Hours after which a candidate transitions to expired. §29.11.'),
    ('reply_target_lint_enabled',              'true',
     'V1.1+: thread-classifier lint runs on each candidate. §29.10.');

-- ---------------------------------------------------------------------------
-- 5. v_daily_reps extension per §29.9.
--
-- Drop and recreate (SQLite cannot ALTER VIEW). The original column set
-- from 002_views.sql is preserved verbatim; four new §29.9 columns are
-- appended. Computation rules:
--
-- * ``candidates_reviewed_today`` — distinct reply_targets touched today.
--   "Touched" is approximated at MVP as: discovered today OR expired today
--   OR (skipped/posted/drafted today, joined via posts.created_date for
--   posted and via reply_targets.discovered_at_utc / expired_at_utc).
--   The conservative interpretation chosen here: rows whose
--   discovered_at_utc, expired_at_utc, or last_checked_at_utc falls on
--   today's calendar date. V1.1+ snapshots will give a richer audit trail.
--
-- * ``high_engagement_replies_shipped`` — posts created today linked to
--   a reply_targets row with engagement_surface_score >= 2.
--
-- * ``icp_intent_replies_shipped`` — posts created today with
--   reply_intent='icp_discovery'.
--
-- * ``average_engagement_surface_of_posted`` — mean of
--   engagement_surface_score over today's shipped replies (NULL when zero).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_daily_reps;
CREATE VIEW v_daily_reps AS
SELECT
    da.activity_date,
    da.posts_shipped,
    da.replies_shipped,
    da.quotes_shipped,
    da.reply_sessions_completed,
    da.minimum_reps_completed,
    da.planned_posts,
    da.planned_replies,
    CASE
        WHEN da.posts_shipped >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_post_target'),
            1
        ) THEN 1 ELSE 0
    END                                       AS post_target_met,
    CASE
        WHEN da.replies_shipped >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_reply_target'),
            12
        ) THEN 1 ELSE 0
    END                                       AS reply_target_met,
    CASE
        WHEN da.reply_sessions_completed >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_reply_session_target'),
            1
        ) THEN 1 ELSE 0
    END                                       AS session_target_met,
    da.time_spent_minutes,
    -- §29.9 — candidates touched today. Counts rows whose discovered_at_utc,
    -- expired_at_utc, OR last_checked_at_utc falls on the activity_date.
    -- Posted candidates fall under "touched" via discovered_at_utc; the
    -- posted_reply_post_id linkage carries the join into the next column.
    (
        SELECT COUNT(DISTINCT rt.id)
        FROM reply_targets rt
        WHERE DATE(rt.discovered_at_utc)   = da.activity_date
           OR DATE(rt.last_checked_at_utc) = da.activity_date
           OR DATE(rt.expired_at_utc)      = da.activity_date
    )                                          AS candidates_reviewed_today,
    -- §29.9 — replies shipped today against a candidate whose engagement
    -- surface was already ≥ 2 at the time of scoring. The MVP source of
    -- "at posting time" is reply_targets.engagement_surface_score itself;
    -- V1.1+ will move this to a frozen snapshot taken at status='posted'
    -- transition.
    (
        SELECT COUNT(*)
        FROM posts p
        JOIN reply_targets rt ON rt.id = p.in_reply_to_reply_target_id
        WHERE p.created_date = da.activity_date
          AND p.type = 'reply'
          AND COALESCE(rt.engagement_surface_score, -1) >= 2
    )                                          AS high_engagement_replies_shipped,
    -- §29.9 — replies shipped today with reply_intent='icp_discovery'.
    (
        SELECT COUNT(*)
        FROM posts p
        WHERE p.created_date = da.activity_date
          AND p.type         = 'reply'
          AND p.reply_intent = 'icp_discovery'
    )                                          AS icp_intent_replies_shipped,
    -- §29.9 — mean engagement_surface_score across today's shipped replies,
    -- NULL when no replies have been linked back to a candidate yet today.
    (
        SELECT AVG(rt.engagement_surface_score)
        FROM posts p
        JOIN reply_targets rt ON rt.id = p.in_reply_to_reply_target_id
        WHERE p.created_date = da.activity_date
          AND p.type         = 'reply'
          AND rt.engagement_surface_score IS NOT NULL
    )                                          AS average_engagement_surface_of_posted
FROM daily_activity da;
