-- Phase 9 — Grok integration (spec §29.12; §25 Phase 9 checklist;
-- §29.1 Phase 9 block; §29.6 settings rows; §28.6 cost integration).
--
-- Three surfaces change:
--
--   1. ``reply_targets.discovered_via`` CHECK is extended with the new
--      ``'grok_semantic'`` value so the Phase 9 discovery sweep can
--      insert candidates. SQLite cannot ALTER a CHECK constraint in
--      place; we use the canonical 12-step ALTER-TABLE rebuild recipe
--      (same pattern as migration 017 for blog_versions and 018 for
--      audit_logs). v_daily_reps must be dropped first because it
--      references reply_targets; rebuilt verbatim at the end.
--
--   2. New ``grok_api_responses`` audit table — parallel to
--      ``raw_api_responses`` for the X API. Every Grok call (success
--      OR error) writes one row so the Settings "Recent Grok failures
--      (last 7 days)" panel can surface non-2xx outcomes and the audit
--      trail covers what queries hit Grok and what came back.
--
--   3. Phase 9 settings rows (§29.6):
--        - ``grok_api_enabled``                         BOOL  TRUE
--        - ``grok_query_list_json``                     JSON  []
--        - ``grok_discovery_sweep_interval_minutes``    INT   120
--      Defaults match §29.12 + Phase A confirmation #9 (ENABLED, empty
--      query list = Daniel populates).
--
-- The note on migration number: the Phase 9 prompt referenced
-- "migration 020" but ``020_force_drafted_reason_required.sql`` had
-- already shipped (the RV2-7 defense-in-depth fix). The next free
-- slot is 021, which is what this migration uses. spec.md still
-- references 020 historically — that's a documentation-only drift
-- noted in the README + docs/index.html Phase 9 block.
--
-- Final step: a ``migration_applied_021`` row in audit_logs so the
-- application history is itself traceable.

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- 0. RETRY-SAFETY (P9R-1) — defend against a crash between DROP TABLE
-- reply_targets and ALTER TABLE … RENAME further down.
--
-- If the prior run got past DROP TABLE reply_targets but crashed before
-- the RENAME, the data is sitting in reply_targets_new and the original
-- table is missing. The migration file is then re-run from the top.
-- The original "defensive scrub" pattern (DROP TABLE IF EXISTS
-- reply_targets_new) would destroy the only surviving copy.
--
-- The recovery is implemented in app/db.py::apply_migrations BEFORE
-- this script is executescript()'d: it detects the
-- (reply_targets_new exists, reply_targets missing) state and ALTER-
-- RENAMEs the new table back into place. By the time this script
-- runs, we're guaranteed that reply_targets exists; the optional
-- DROP TABLE IF EXISTS reply_targets_new at step 0b is then safe.
--
-- This SQL preamble emits a single audit-log warning if the crash
-- state somehow still holds (defense in depth — the Python recovery
-- should have handled it). The migration then proceeds and will
-- crash loudly on the subsequent INSERT INTO reply_targets_new
-- SELECT * FROM reply_targets (because reply_targets is missing),
-- which is the correct loud failure mode rather than silently
-- destroying the data via the old "scrub" path. The DROP TABLE IF
-- EXISTS reply_targets_new below is GATED on reply_targets existing.
-- ---------------------------------------------------------------------------

-- Defense-in-depth: emit an audit warning if a crash state slipped
-- past the Python pre-flight recovery. The migration will then crash
-- loudly on the SELECT FROM reply_targets below — much better than
-- silently destroying the surviving data via DROP TABLE.
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success, error_message)
SELECT 'migration', 'migration_021_crash_recovery_required', 'migration', '021',
       '{"reason":"reply_targets_new exists but reply_targets does not. ' ||
       'Python pre-flight should have recovered. Run scripts/recover_migration_021.py."}',
       0,
       'CRASH RECOVERY REQUIRED — see audit_logs.details_json'
 WHERE EXISTS (SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='reply_targets_new')
   AND NOT EXISTS (SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='reply_targets');

-- Step 0: drop the view that references reply_targets. SQLite refuses
-- DROP TABLE while a view depends on it; rebuilt at step 6.
DROP VIEW IF EXISTS v_daily_reps;

-- Step 0b: defensive — drop a leftover reply_targets_new from a prior
-- crash that happened BEFORE DROP TABLE reply_targets fired. This DROP
-- is GATED on reply_targets existing (the audit_logs warning above
-- fires in the inverse case so the loud-failure path is preserved).
-- On a normal run reply_targets_new doesn't exist anyway, so this is
-- a no-op.
DROP TABLE IF EXISTS reply_targets_new;

-- Step 1: create the replacement table under a temporary name. Column
-- list is verbatim from migrations 009 + 012 + 018 (the cumulative
-- schema dumped from a freshly-migrated DB on 2026-05-23). Only the
-- ``discovered_via`` CHECK is extended — 'grok_semantic' added at the
-- end of the IN-list per §29.12.
CREATE TABLE reply_targets_new (
    id                              INTEGER PRIMARY KEY,
    discovered_at_utc               TEXT NOT NULL DEFAULT (datetime('now')),

    -- §29.6 enum — Phase 9 (this migration) adds 'grok_semantic' for
    -- the Grok firehose discovery path per §29.12. The 'v1.1_api_search'
    -- value stays for backward compatibility with the historical Phase 7
    -- spec text; current xurl-driven discovery uses 'manual' /
    -- 'agent_score' / 'next_rep_seed' depending on the entry path.
    discovered_via                  TEXT NOT NULL
        CHECK (discovered_via IN ('manual', 'agent_score', 'next_rep_seed',
                                   'v1.1_api_search', 'grok_semantic')),

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

    -- Engagement snapshot (latest known; per-snapshot history in
    -- reply_target_snapshots from Phase 7).
    last_checked_at_utc             TEXT NOT NULL DEFAULT (datetime('now')),
    like_count                      INTEGER,
    reply_count                     INTEGER,
    repost_count                    INTEGER,
    quote_count                     INTEGER,
    bookmark_count                  INTEGER,
    impression_count                INTEGER,

    -- Scores (0..3 each; NULL when not yet scored or Phase 7+ only).
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

    -- Lint pass output (Phase 7+).
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
    notes                           TEXT,

    -- §28.20 third discovery path (migration 012).
    source                          TEXT NOT NULL DEFAULT 'paste_url'
        CHECK (source IN ('paste_url', 'agent_curated_account', 'replier_under_thread')),

    -- Phase 7 §29.10 thread-classifier lint output (migration 018).
    lint_category                   TEXT
        CHECK (lint_category IS NULL OR
               lint_category IN ('ragebait',
                                 'meme_with_no_serious_reply_path',
                                 'low_quality_reply_thread',
                                 'hijacking_required_to_mention_stir')),

    -- Phase 7 §29.7 force-draft override (migration 018; non-empty
    -- reason invariant enforced by triggers from migration 020).
    force_drafted                   INTEGER NOT NULL DEFAULT 0
        CHECK (force_drafted IN (0, 1)),
    force_drafted_reason            TEXT
);

-- Step 2: copy every existing row verbatim. Column names match the
-- original table 1:1.
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

-- Step 3: drop the original. Indexes on reply_targets drop with it.
-- Triggers also drop with it. PRAGMA foreign_keys = OFF (above) lets
-- this proceed while four other tables hold FKs into reply_targets.id;
-- those rows keep their integer values, which realign at step 4 since
-- we preserved id during the copy.
DROP TABLE reply_targets;

-- Step 4: rename the new table into place.
ALTER TABLE reply_targets_new RENAME TO reply_targets;

-- Step 5: recreate every index from migrations 009 + 012 + 018. The
-- IF NOT EXISTS guards keep this idempotent under a partial-failure
-- retry, but on a clean run none of them exist yet.
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

-- Step 5b: recreate triggers from migration 020 (force-draft requires
-- non-empty reason — RV2-7 defense-in-depth). Same shape as 020 verbatim.
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

-- Step 6: rebuild v_daily_reps verbatim (column set unchanged).
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

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 2. grok_api_responses — parallel of raw_api_responses for xAI Grok.
-- ---------------------------------------------------------------------------
-- Every Grok call (success OR error) writes one row. The Settings
-- "Recent Grok failures (last 7 days)" panel reads from this. Per
-- §18 item 20, this table is on the export carve-out: it's never
-- included in the default Markdown / CSV export bundle.
CREATE TABLE IF NOT EXISTS grok_api_responses (
    id                     INTEGER PRIMARY KEY,
    query                  TEXT NOT NULL,
    request_payload_json   TEXT,
    response_status_code   INTEGER,
    response_body_json     TEXT,
    rate_snapshot_json     TEXT,
    -- Reason the candidate flow rejected the response, if any.
    -- NULL = success path (response was accepted into the verification
    -- pipeline). 'verification_404' = X API said the post is gone
    -- before we could score it. 'rate_limit_429' = xAI throttled us.
    -- 'cost_ceiling_hit' = §28.6 combined cap reached pre-call.
    -- 'http_error_5xx' = xAI server error after the bounded retry.
    -- 'http_error_other' = any other non-2xx the wrapper saw.
    rejection_reason       TEXT
        CHECK (rejection_reason IS NULL OR rejection_reason IN (
            'verification_404',
            'rate_limit_429',
            'cost_ceiling_hit',
            'http_error_5xx',
            'http_error_other'
        )),
    created_at_utc         TEXT NOT NULL DEFAULT (datetime('now')),
    duration_ms            INTEGER
);

-- Failures-by-recency for the Settings panel.
CREATE INDEX IF NOT EXISTS idx_grok_api_responses_created
    ON grok_api_responses (created_at_utc DESC);

-- Per-query timeline (debugging "why did this query stop returning
-- candidates?" without scanning the whole audit table).
CREATE INDEX IF NOT EXISTS idx_grok_api_responses_query
    ON grok_api_responses (query, created_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 3. Phase 9 settings rows per §29.6 / §29.12.
-- ---------------------------------------------------------------------------
-- All three rows are INSERT OR IGNORE — Phase 9 is the first time
-- these keys exist. Defaults match §29.12 + Phase A confirmation #9:
-- grok_api_enabled defaults TRUE (opt-out kill switch, not opt-in).
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('grok_api_enabled', 'true',
     'Phase 9 default: TRUE. Kill switch is opt-out per §29.12.'),
    ('grok_query_list_json', '[]',
     'JSON array of natural-language queries; Daniel maintains. Empty list = no Grok calls. §29.12.'),
    ('grok_discovery_sweep_interval_minutes', '120',
     'Cadence for app/jobs/grok_discovery_sweep.py via launchd. §17 Phase 9.');

-- ---------------------------------------------------------------------------
-- 4. Final audit row — migration history is itself a state-change.
-- ---------------------------------------------------------------------------
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
VALUES
    ('migration', 'migration_applied_021', 'migration', '021',
     '{"migration":"021_grok_integration",'                       ||
     '"tables_added":["grok_api_responses"],'                     ||
     '"check_constraints_extended":'                              ||
     '{"reply_targets.discovered_via":["grok_semantic"]},'        ||
     '"settings_seeded":["grok_api_enabled",'                     ||
     '"grok_query_list_json",'                                    ||
     '"grok_discovery_sweep_interval_minutes"]}',
     1);
