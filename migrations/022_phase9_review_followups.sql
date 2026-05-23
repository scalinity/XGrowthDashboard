-- Phase 9 /review-5 follow-ups (migration 022).
--
-- Two additive changes that surfaced during the post-Phase-9 review:
--
--   1. P9R-20 (🟡) — add a covering index on
--      reply_targets.(discovered_via, last_checked_at_utc DESC) so
--      the Queue UI's new Phase 9 filter dropdown
--      (AND discovered_via = ?) has an index to lean on instead of
--      falling back to a full scan composed onto the existing
--      idx_reply_targets_status_score (which leads with status, not
--      discovered_via).
--
--   2. P9R-43 (🔵) — extend reply_targets.source CHECK to admit
--      'grok_firehose' so Grok-discovered rows can carry an honest
--      source value instead of borrowing 'paste_url'. The
--      discovered_via column is still the canonical provenance field
--      ('grok_semantic'), but a future operator running
--      `SELECT … WHERE source='paste_url'` shouldn't see Grok rows
--      mixed in.
--
-- Both items are pure additive — no data rewrite required. SQLite
-- forbids ALTER COLUMN on the CHECK constraint, so item #2 uses the
-- canonical drop-and-recreate recipe (matching the discovered_via
-- recipe migration 021 used). The reply_targets data is preserved
-- via INSERT INTO _new SELECT * FROM _old; all 9 indexes + 2
-- triggers + 1 dependent view are recreated verbatim afterward.

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- 0. Retry-safety preamble (mirrors migration 021's P9R-1 pattern).
-- ---------------------------------------------------------------------------
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success, error_message)
SELECT 'migration', 'migration_022_crash_recovery_required', 'migration', '022',
       '{"reason":"reply_targets_new exists but reply_targets does not. ' ||
       'Python pre-flight should have recovered. See app/db.py::'         ||
       '_recover_half_rebuilt_tables."}',
       0,
       'CRASH RECOVERY REQUIRED — see audit_logs.details_json'
 WHERE EXISTS (SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='reply_targets_new')
   AND NOT EXISTS (SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='reply_targets');

-- ---------------------------------------------------------------------------
-- 1. reply_targets — rebuild with extended source CHECK.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_daily_reps;
DROP TABLE IF EXISTS reply_targets_new;

CREATE TABLE reply_targets_new (
    id                              INTEGER PRIMARY KEY,
    discovered_at_utc               TEXT NOT NULL DEFAULT (datetime('now')),
    discovered_via                  TEXT NOT NULL
        CHECK (discovered_via IN ('manual', 'agent_score', 'next_rep_seed',
                                   'v1.1_api_search', 'grok_semantic')),
    source_platform                 TEXT NOT NULL DEFAULT 'x',
    target_post_url                 TEXT NOT NULL,
    target_x_post_id                TEXT,
    target_author_handle            TEXT NOT NULL,
    target_author_display_name      TEXT,
    target_author_follower_count    INTEGER,
    target_text                     TEXT,
    target_created_at_utc           TEXT,
    post_age_minutes                INTEGER,
    last_checked_at_utc             TEXT NOT NULL DEFAULT (datetime('now')),
    like_count                      INTEGER,
    reply_count                     INTEGER,
    repost_count                    INTEGER,
    quote_count                     INTEGER,
    bookmark_count                  INTEGER,
    impression_count                INTEGER,
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
    pillar                          TEXT,
    audience                        TEXT,
    reply_intent                    TEXT
        CHECK (reply_intent IS NULL OR
               reply_intent IN ('growth', 'icp_discovery', 'relationship',
                                 'product_adjacent', 'thought_leadership')),
    topic_tags_json                 TEXT,
    lint_thread_classification_json TEXT,
    lint_blocked                    INTEGER NOT NULL DEFAULT 0
        CHECK (lint_blocked IN (0, 1)),
    status                          TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'drafted', 'posted', 'skipped',
                          'expired', 'target_deleted')),
    skip_reason                     TEXT
        CHECK (skip_reason IS NULL OR
               skip_reason IN ('off_topic', 'ragebait', 'saturation',
                                'cant_add_value', 'target_deleted',
                                'blocked_by_author', 'other')),
    expired_at_utc                  TEXT,
    agent_draft_id                  INTEGER REFERENCES agent_drafts(id) ON DELETE SET NULL,
    posted_reply_post_id            INTEGER REFERENCES posts(id)        ON DELETE SET NULL,
    created_via_agent_message_id    INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
    notes                           TEXT,
    -- P9R-43: 'grok_firehose' added to the CHECK enum so Grok-
    -- discovered rows can carry honest provenance on the `source`
    -- column. discovered_via stays the primary key for provenance
    -- audits (CHECK includes 'grok_semantic' from migration 021).
    source                          TEXT NOT NULL DEFAULT 'paste_url'
        CHECK (source IN ('paste_url', 'agent_curated_account',
                          'replier_under_thread', 'grok_firehose')),
    lint_category                   TEXT
        CHECK (lint_category IS NULL OR
               lint_category IN ('ragebait',
                                 'meme_with_no_serious_reply_path',
                                 'low_quality_reply_thread',
                                 'hijacking_required_to_mention_stir')),
    force_drafted                   INTEGER NOT NULL DEFAULT 0
        CHECK (force_drafted IN (0, 1)),
    force_drafted_reason            TEXT
);

INSERT INTO reply_targets_new (
    id, discovered_at_utc, discovered_via,
    source_platform, target_post_url, target_x_post_id,
    target_author_handle, target_author_display_name,
    target_author_follower_count,
    target_text, target_created_at_utc, post_age_minutes,
    last_checked_at_utc,
    like_count, reply_count, repost_count, quote_count,
    bookmark_count, impression_count,
    relevance_score, engagement_surface_score, saturation_score,
    reply_opportunity_score, velocity_score, timing_score,
    audience_quality_score,
    recommended_action_label, recommended_action_score, score_rationale,
    pillar, audience, reply_intent, topic_tags_json,
    lint_thread_classification_json, lint_blocked,
    status, skip_reason, expired_at_utc,
    agent_draft_id, posted_reply_post_id,
    created_via_agent_message_id, notes,
    source, lint_category, force_drafted, force_drafted_reason
)
SELECT
    id, discovered_at_utc, discovered_via,
    source_platform, target_post_url, target_x_post_id,
    target_author_handle, target_author_display_name,
    target_author_follower_count,
    target_text, target_created_at_utc, post_age_minutes,
    last_checked_at_utc,
    like_count, reply_count, repost_count, quote_count,
    bookmark_count, impression_count,
    relevance_score, engagement_surface_score, saturation_score,
    reply_opportunity_score, velocity_score, timing_score,
    audience_quality_score,
    recommended_action_label, recommended_action_score, score_rationale,
    pillar, audience, reply_intent, topic_tags_json,
    lint_thread_classification_json, lint_blocked,
    status, skip_reason, expired_at_utc,
    agent_draft_id, posted_reply_post_id,
    created_via_agent_message_id, notes,
    source, lint_category, force_drafted, force_drafted_reason
FROM reply_targets;

DROP TABLE reply_targets;
ALTER TABLE reply_targets_new RENAME TO reply_targets;

-- Recreate every index from migrations 009 + 012 + 018, PLUS the new
-- P9R-20 covering index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_targets_url
    ON reply_targets (target_post_url);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_targets_x_post_id
    ON reply_targets (target_x_post_id)
    WHERE target_x_post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reply_targets_status_score
    ON reply_targets (status, recommended_action_score DESC, last_checked_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_reply_targets_intent_posted
    ON reply_targets (reply_intent)
    WHERE status = 'posted';
CREATE INDEX IF NOT EXISTS idx_reply_targets_last_checked
    ON reply_targets (last_checked_at_utc);
CREATE INDEX IF NOT EXISTS idx_reply_targets_expired_at
    ON reply_targets (expired_at_utc)
    WHERE expired_at_utc IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reply_targets_source
    ON reply_targets (source);
CREATE INDEX IF NOT EXISTS idx_reply_targets_force_drafted
    ON reply_targets (force_drafted, discovered_at_utc DESC)
    WHERE force_drafted = 1;
CREATE INDEX IF NOT EXISTS idx_reply_targets_lint_category
    ON reply_targets (lint_category)
    WHERE lint_category IS NOT NULL;

-- P9R-20: covering index for the Queue UI's discovered_via filter.
-- Composite (discovered_via, last_checked_at_utc DESC) supports both
-- the WHERE clause and the existing ORDER BY at the Queue page's
-- SELECT site. Without it, picking 'manual' / 'grok_semantic' in the
-- filter dropdown falls back to a full scan (acceptable at MVP scale
-- but not as the table grows).
CREATE INDEX IF NOT EXISTS idx_reply_targets_discovered_via
    ON reply_targets (discovered_via, last_checked_at_utc DESC);

-- Triggers from migration 020 (force_drafted requires non-empty reason).
CREATE TRIGGER IF NOT EXISTS trg_reply_targets_force_drafted_requires_reason_insert
BEFORE INSERT ON reply_targets
FOR EACH ROW
WHEN NEW.force_drafted = 1
 AND (NEW.force_drafted_reason IS NULL
      OR length(trim(replace(replace(replace(
              NEW.force_drafted_reason,
              char(9), ' '), char(10), ' '), char(13), ' '))) = 0)
BEGIN
    SELECT RAISE(ABORT,
        'reply_targets.force_drafted=1 requires non-empty force_drafted_reason (RV2-7)');
END;

CREATE TRIGGER IF NOT EXISTS trg_reply_targets_force_drafted_requires_reason_update
BEFORE UPDATE OF force_drafted, force_drafted_reason ON reply_targets
FOR EACH ROW
WHEN NEW.force_drafted = 1
 AND (NEW.force_drafted_reason IS NULL
      OR length(trim(replace(replace(replace(
              NEW.force_drafted_reason,
              char(9), ' '), char(10), ' '), char(13), ' '))) = 0)
BEGIN
    SELECT RAISE(ABORT,
        'reply_targets.force_drafted=1 requires non-empty force_drafted_reason (RV2-7)');
END;

-- v_daily_reps (verbatim from migration 021).
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
    (
        SELECT COUNT(DISTINCT rt.id)
        FROM reply_targets rt
        WHERE DATE(rt.discovered_at_utc)   = da.activity_date
           OR DATE(rt.last_checked_at_utc) = da.activity_date
           OR DATE(rt.expired_at_utc)      = da.activity_date
    )                                          AS candidates_reviewed_today,
    (
        SELECT COUNT(*)
        FROM posts p
        JOIN reply_targets rt ON rt.id = p.in_reply_to_reply_target_id
        WHERE p.created_date = da.activity_date
          AND p.type = 'reply'
          AND COALESCE(rt.engagement_surface_score, -1) >= 2
    )                                          AS high_engagement_replies_shipped,
    (
        SELECT COUNT(*)
        FROM posts p
        WHERE p.created_date = da.activity_date
          AND p.type         = 'reply'
          AND p.reply_intent = 'icp_discovery'
    )                                          AS icp_intent_replies_shipped,
    (
        SELECT AVG(rt.engagement_surface_score)
        FROM posts p
        JOIN reply_targets rt ON rt.id = p.in_reply_to_reply_target_id
        WHERE p.created_date = da.activity_date
          AND p.type         = 'reply'
          AND rt.engagement_surface_score IS NOT NULL
    )                                          AS average_engagement_surface_of_posted
FROM daily_activity da;

-- P9R-58: re-enable foreign keys BEFORE the final audit-log INSERT so
-- the audit row writes with the same PRAGMA state the rest of the
-- application sees. (In migration 021 the audit INSERT ran with FKs
-- still OFF — harmless because audit_logs has no FKs, but the
-- placement was ambiguous.)
PRAGMA foreign_keys = ON;

INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
VALUES
    ('migration', 'migration_applied_022', 'migration', '022',
     '{"migration":"022_phase9_review_followups",'                       ||
     '"check_constraints_extended":'                                     ||
     '{"reply_targets.source":["grok_firehose"]},'                       ||
     '"indexes_added":["idx_reply_targets_discovered_via"]}',
     1);
