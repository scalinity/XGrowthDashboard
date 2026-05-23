-- Phase 7 — X API reads (spec §29.1 Phase 7 block; §29.6 schema; §17 scheduling).
--
-- This migration wires the database surface that the four scheduled
-- xurl read jobs (collect_account_snapshot, import_recent_posts,
-- post_metrics_refresh, reply_target_metrics_refresh) read from and
-- write into. It also promotes the §29.10 thread-classifier lint
-- output columns and the §29.7 force-draft override columns from
-- spec-only to schema-backed.
--
-- Five surfaces change:
--
--   1. New table ``reply_target_snapshots`` (§29.6). Immutable; one
--      row per metrics-refresh call against a candidate. Velocity is
--      a function of history, so the table must persist between runs.
--
--   2. ``posts.last_metrics_refresh_at_utc`` column added. Indexed so
--      the hourly metrics-refresh job can pick the next batch in
--      <14d / 14–90d / >90d priority order.
--
--   3. ``reply_targets`` gains three columns: ``lint_category`` (the
--      denormalized primary §29.10 lint classification), ``force_drafted``
--      (whether Daniel overrode the lint block), and
--      ``force_drafted_reason`` (mandatory when force_drafted=1; logged
--      to audit_logs). The pre-existing ``lint_thread_classification_json``
--      and ``lint_blocked`` columns from migration 009 stay as-is.
--
--   4. ``audit_logs.event_category`` CHECK constraint extended with
--      ``scheduled_job`` (§28.30). SQLite cannot ALTER a CHECK
--      constraint in place — we use the 12-step ALTER-TABLE rebuild
--      recipe (the same one migration 017 used for blog_versions).
--      ``audit_logs`` has no inbound FKs, so the rebuild is simpler
--      than 017's: rename original → create new → copy rows → drop
--      original. We still toggle PRAGMA foreign_keys off/on around
--      the rebuild for symmetry with the project's standard pattern.
--
--   5. Settings rows seeded per §29.6 Phase 7 block. ``data_collection_mode``
--      is flipped from ``'manual'`` to ``'api'`` (the Phase 7
--      transition moment); the five new rows are inserted with their
--      Phase 7 defaults. INSERT … ON CONFLICT DO UPDATE handles the
--      fresh-DB case (the row is created with 'api') AND the
--      upgrade-existing-DB case (an existing 'manual' value flips to
--      'api'). seed_settings.py is updated in parallel so a fresh DB
--      doesn't re-INSERT 'manual' via INSERT OR IGNORE after this
--      migration runs.
--
-- Final step: a ``migration_applied_018`` row in audit_logs so the
-- application history is itself traceable.

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- 1. reply_target_snapshots — §29.6 v1 schema.
-- ---------------------------------------------------------------------------
-- Immutable per spec ("Snapshots are immutable. Latest values are
-- copied onto the parent reply_targets row for cheap reads; the
-- history exists for velocity computation only."). No UPDATE in any
-- application code path; rows live until the parent reply_targets
-- row is deleted (CASCADE).
CREATE TABLE IF NOT EXISTS reply_target_snapshots (
    id                          INTEGER PRIMARY KEY,
    reply_target_id             INTEGER NOT NULL
        REFERENCES reply_targets(id) ON DELETE CASCADE,
    checked_at_utc              TEXT NOT NULL DEFAULT (datetime('now')),
    -- Engagement counters (NULL when X API didn't return the field).
    like_count                  INTEGER
        CHECK (like_count        IS NULL OR like_count        >= 0),
    reply_count                 INTEGER
        CHECK (reply_count       IS NULL OR reply_count       >= 0),
    repost_count                INTEGER
        CHECK (repost_count      IS NULL OR repost_count      >= 0),
    quote_count                 INTEGER
        CHECK (quote_count       IS NULL OR quote_count       >= 0),
    bookmark_count              INTEGER
        CHECK (bookmark_count    IS NULL OR bookmark_count    >= 0),
    impression_count            INTEGER
        CHECK (impression_count  IS NULL OR impression_count  >= 0),
    -- Derived velocity metrics. NULL on the first snapshot for a
    -- given reply_target_id (no prior snapshot to diff against).
    computed_likes_per_hour     REAL,
    computed_replies_per_hour   REAL,
    computed_velocity_delta     REAL,
    -- Backreference to the raw_api_responses row that produced this
    -- snapshot. Lets the audit log walk from "this snapshot" → "the
    -- exact xurl invocation that captured it". NULL on manual
    -- backfill inserts (fixture path).
    raw_response_id             INTEGER
        REFERENCES raw_api_responses(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_reply_target_snapshots_target_time
    ON reply_target_snapshots (reply_target_id, checked_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 2. posts.last_metrics_refresh_at_utc — §29.6 / §17 Phase 7 job #3.
-- ---------------------------------------------------------------------------
-- Used by ``app/jobs/post_metrics_refresh.py`` to pick the next batch
-- via priority queue (daily for <14d posts, weekly 14–90d, monthly
-- >90d). Index ordered ascending so the hourly job's
-- "longest-unrefreshed first" SELECT lands in index order.
ALTER TABLE posts ADD COLUMN last_metrics_refresh_at_utc TEXT;

-- RV2-17: this partial index covers the WHERE pre-filter in
-- app/jobs/post_metrics_refresh.py::_select_stale_post_ids but NOT the
-- ORDER BY tier_due (a computed CASE expression). At MVP scale (<10K
-- posts) the materialized intermediate is fine. If post count crosses
-- 10K (V1.1+), add a covering index that includes
-- (last_metrics_refresh_at_utc ASC, created_date ASC) to support the
-- staleness-tier ORDER BY without forcing a full scan.
CREATE INDEX IF NOT EXISTS idx_posts_last_metrics_refresh
    ON posts (last_metrics_refresh_at_utc)
    WHERE x_post_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. reply_targets — three new columns per §29.6.
-- ---------------------------------------------------------------------------
-- ``lint_category`` is denormalized from ``lint_thread_classification_json``
-- for fast filtering in the Queue UI. The four allowed values come
-- straight from §29.10's output schema.
ALTER TABLE reply_targets ADD COLUMN lint_category TEXT
    CHECK (lint_category IS NULL OR
           lint_category IN ('ragebait',
                             'meme_with_no_serious_reply_path',
                             'low_quality_reply_thread',
                             'hijacking_required_to_mention_stir'));

-- SQLite stores booleans as INTEGER 0/1. The default is FALSE (0).
-- Daniel's "Force-draft (overrides lint)" affordance writes TRUE (1)
-- alongside a mandatory ``force_drafted_reason`` and an audit-log row.
ALTER TABLE reply_targets ADD COLUMN force_drafted INTEGER NOT NULL DEFAULT 0
    CHECK (force_drafted IN (0, 1));

ALTER TABLE reply_targets ADD COLUMN force_drafted_reason TEXT;

-- Partial index — only rows that actually had Daniel override the
-- lint get indexed. Queue's "audit overrides" surface joins from this.
CREATE INDEX IF NOT EXISTS idx_reply_targets_force_drafted
    ON reply_targets (force_drafted, discovered_at_utc DESC)
    WHERE force_drafted = 1;

-- Lint-category filter for the Queue UI dropdown.
CREATE INDEX IF NOT EXISTS idx_reply_targets_lint_category
    ON reply_targets (lint_category)
    WHERE lint_category IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. audit_logs.event_category CHECK — extend with 'scheduled_job'.
-- ---------------------------------------------------------------------------
-- SQLite cannot ALTER a CHECK constraint in place. Use the 12-step
-- ALTER-TABLE rebuild recipe. ``audit_logs`` is a leaf table (no FKs
-- reference it), so the rebuild is the simpler 7-step variant.

-- Step 1: create the replacement table with the extended CHECK.
CREATE TABLE audit_logs_new (
    id                  INTEGER PRIMARY KEY,
    occurred_at_utc     TEXT NOT NULL DEFAULT (datetime('now')),
    event_category      TEXT NOT NULL
        CHECK (event_category IN (
            'auth', 'x_op', 'publish', 'settings',
            'export', 'data', 'admin', 'migration',
            'scheduled_job'
        )),
    event_type          TEXT NOT NULL,
    actor               TEXT NOT NULL DEFAULT 'daniel',
    target_type         TEXT,
    target_id           TEXT,
    details_json        TEXT,
    success             INTEGER NOT NULL DEFAULT 1
        CHECK (success IN (0, 1)),
    error_message       TEXT
);

-- Step 2: copy rows verbatim. Existing rows pre-dated the
-- 'scheduled_job' category, so every value satisfies the new CHECK.
INSERT INTO audit_logs_new
    (id, occurred_at_utc, event_category, event_type, actor,
     target_type, target_id, details_json, success, error_message)
SELECT id, occurred_at_utc, event_category, event_type, actor,
       target_type, target_id, details_json, success, error_message
  FROM audit_logs;

-- Step 3: drop the old table.
DROP TABLE audit_logs;

-- Step 4: rename the replacement into place.
ALTER TABLE audit_logs_new RENAME TO audit_logs;

-- Step 5: recreate indexes (DROP TABLE took them down).
-- RV2-25: verified — these three are the COMPLETE set of indexes on
-- audit_logs (grep migrations/*.sql 2026-05-23). Migration 015 created
-- exactly these three; no later migration added any. If a future
-- migration adds an audit_logs index, the rebuild here must be updated
-- in parallel (or the new index must use CREATE INDEX IF NOT EXISTS
-- in its own migration which will recreate it post-rebuild).
CREATE INDEX IF NOT EXISTS idx_audit_logs_occurred
    ON audit_logs (occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_category_occurred
    ON audit_logs (event_category, occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target
    ON audit_logs (target_type, target_id)
    WHERE target_type IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. settings — Phase 7 rows per §29.6.
-- ---------------------------------------------------------------------------
-- ``data_collection_mode`` is the load-bearing flip: this migration
-- is the official Phase 7 transition. INSERT … ON CONFLICT DO UPDATE
-- covers both the fresh-DB case (row doesn't exist yet → INSERT 'api')
-- AND the upgrade-existing-DB case (existing 'manual' row → UPDATE to
-- 'api'). seed_settings.py is updated in parallel so a fresh DB's
-- seed pass (which runs AFTER migrations) doesn't re-INSERT 'manual'.
--
-- The five new keys are pure INSERT OR IGNORE — they didn't exist
-- pre-Phase-7. Defaults match §29.6 verbatim.
INSERT INTO settings (key, value_json, note) VALUES
    ('data_collection_mode', '"api"',
     'Phase 7 default: api. Toggleable to ''manual'' for fallback. §29.1.')
ON CONFLICT(key) DO UPDATE SET
    value_json = '"api"',
    note       = 'Phase 7 default: api. Toggleable to ''manual'' for fallback. §29.1.';

INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('reply_target_metrics_refresh_interval_minutes', '60',
     'Hourly default for app/jobs/reply_target_metrics_refresh.py. §17 Phase 7.'),
    ('post_metrics_refresh_interval_minutes', '60',
     'Hourly default for app/jobs/post_metrics_refresh.py. §17 Phase 7.'),
    ('combined_ai_monthly_cost_ceiling_usd', '30.0',
     'Combined Anthropic + xAI Grok ceiling per §28.6. Supersedes the historical ' ||
     'monthly_anthropic_cost_ceiling_usd at Phase 7 install.'),
    ('x_api_rate_limit_window_minutes', '15',
     'X API rate-limit window per §18 item 17 / §17 Phase 7.'),
    ('x_api_recent_failures_visible_days', '7',
     'Settings "Recent X API failures" panel lookback window. §17 Phase 7.');

-- ---------------------------------------------------------------------------
-- 6. Final audit row — migration history is itself a state-change.
-- ---------------------------------------------------------------------------
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
VALUES
    ('migration', 'migration_applied_018', 'migration', '018',
     '{"migration":"018_x_api_reads","tables_added":["reply_target_snapshots"],' ||
     '"columns_added":{"posts":["last_metrics_refresh_at_utc"],' ||
     '"reply_targets":["lint_category","force_drafted","force_drafted_reason"]},' ||
     '"check_constraints_extended":{"audit_logs.event_category":["scheduled_job"]},' ||
     '"settings_seeded":["data_collection_mode (flipped to api)",' ||
     '"reply_target_metrics_refresh_interval_minutes",' ||
     '"post_metrics_refresh_interval_minutes",' ||
     '"combined_ai_monthly_cost_ceiling_usd",' ||
     '"x_api_rate_limit_window_minutes",' ||
     '"x_api_recent_failures_visible_days"]}',
     1);

PRAGMA foreign_keys = ON;
