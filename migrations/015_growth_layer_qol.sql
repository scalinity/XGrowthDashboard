-- migrations/015_growth_layer_qol.sql — Phase 5.11 Growth Layer +
-- Quality-of-Life Pack.
--
-- Five features, one migration: Campaigns (§28.26), Monthly AI reviews
-- (§28.27), Content Calendar (§28.28), Inspiration Library + transforms
-- + plagiarism guard (§28.29), and the comprehensive audit log
-- (§28.30). All six tables ship together because the audit log is the
-- write-through floor for every subsequent feature — landing them in
-- separate migrations would mean an interim DB state where (say)
-- Campaigns exist but the audit log path doesn't, which the §28.30
-- contract forbids.
--
-- Slot 014 was already consumed by 014_velocity_view_expose_noise_floor.sql
-- in Phase 5.9 (P59A-W6) so this file lands at 015 even though the
-- spec's §28.30 example event_type still references the migration as
-- conceptually "the Phase 5.11 migration." See spec.md §25 Phase 5.11
-- note for the renumber.
--
-- All statements are idempotent. CREATE TABLE / INDEX / VIEW use
-- IF NOT EXISTS / OR REPLACE; settings rows use INSERT OR IGNORE; the
-- audit_logs "migration_applied_015" row uses a guarded INSERT so
-- re-running this migration manually (outside apply_migrations) won't
-- double-log. apply_migrations records each file in schema_migrations
-- so re-runs are blocked at the runner level.

-- ---------------------------------------------------------------------------
-- 1. campaigns — multi-week themed pushes (§10, §28.26).
-- ---------------------------------------------------------------------------
-- Hypothesis + date range + dual-stream success criteria + items.
-- success_criteria_json is structurally validated in
-- app/agent/campaigns.py::create_campaign — the schema CHECK on JSON
-- shape is impractical in pure SQLite, so the application layer is
-- the load-bearing enforcer of §28.26's "≥1 distribution AND ≥1
-- validation" rule. The view that powers §14.12 only renders rows
-- already saved, so by the time anything reads success_criteria_json
-- the dual-stream invariant holds.
--
-- parent_experiment_id is nullable + ON DELETE SET NULL: a campaign
-- that started as the execution arm of an experiment survives the
-- experiment row's deletion.
CREATE TABLE IF NOT EXISTS campaigns (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL,
    theme                   TEXT,
    hypothesis              TEXT,
    start_date              TEXT NOT NULL,
    end_date                TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'planning'
        CHECK (status IN ('planning', 'active', 'completed', 'abandoned')),
    success_criteria_json   TEXT NOT NULL DEFAULT '{"distribution":[],"validation":[]}',
    parent_experiment_id    INTEGER
        REFERENCES experiments(id) ON DELETE SET NULL,
    pillar                  TEXT,
    content_type            TEXT,
    notes                   TEXT,
    created_at_utc          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at_utc        TEXT,
    abandon_reason          TEXT,
    lesson                  TEXT,
    counterfactual_note     TEXT,
    CHECK (date(start_date) <= date(end_date))
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status_start
    ON campaigns (status, start_date);
CREATE INDEX IF NOT EXISTS idx_campaigns_dates
    ON campaigns (start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_campaigns_parent_experiment
    ON campaigns (parent_experiment_id)
    WHERE parent_experiment_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. campaign_items — planned / shipped items per campaign (§10, §28.26).
-- ---------------------------------------------------------------------------
-- Generic shell over an existing `posts` / `agent_drafts` /
-- `reply_targets` row. NEVER duplicates content state; the FKs are
-- the join path. ON DELETE CASCADE for campaign_id (orphan items are
-- meaningless); ON DELETE SET NULL for the content FKs (the campaign
-- item's record of "we planned this on date X" survives the deletion
-- of its content row).
CREATE TABLE IF NOT EXISTS campaign_items (
    id                  INTEGER PRIMARY KEY,
    campaign_id         INTEGER NOT NULL
        REFERENCES campaigns(id) ON DELETE CASCADE,
    item_type           TEXT NOT NULL
        CHECK (item_type IN ('post', 'reply', 'event', 'milestone', 'reminder')),
    planned_for_date    TEXT,
    post_id             INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    agent_draft_id      INTEGER
        REFERENCES agent_drafts(id) ON DELETE SET NULL,
    reply_target_id     INTEGER
        REFERENCES reply_targets(id) ON DELETE SET NULL,
    planned_text        TEXT,
    status              TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'drafted', 'shipped', 'skipped')),
    notes               TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at_utc      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at_utc    TEXT
);

CREATE INDEX IF NOT EXISTS idx_campaign_items_campaign_sort
    ON campaign_items (campaign_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_campaign_items_planned_date
    ON campaign_items (planned_for_date)
    WHERE planned_for_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_campaign_items_status
    ON campaign_items (status);

-- ---------------------------------------------------------------------------
-- 3. monthly_reviews — month-granularity retro mirror of weekly (§10, §28.27).
-- ---------------------------------------------------------------------------
-- Same export-blocked discipline as weekly_reviews: counterfactual_note
-- required, confidence_label = 'speculation' blocks export until
-- acknowledged. Unique(iso_month) guarantees one canonical review per
-- month; updates rewrite the same row.
CREATE TABLE IF NOT EXISTS monthly_reviews (
    id                          INTEGER PRIMARY KEY,
    iso_month                   TEXT NOT NULL UNIQUE,
    created_at_utc              TEXT NOT NULL DEFAULT (datetime('now')),
    summary                     TEXT,
    key_movements               TEXT,
    what_got_stuck              TEXT,
    best_post_id                INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    worst_post_id               INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    strongest_pillar            TEXT,
    weakest_pillar              TEXT,
    strongest_content_type      TEXT,
    weakest_content_type        TEXT,
    follower_delta              INTEGER,
    stir_validation_summary     TEXT,
    campaigns_completed_json    TEXT,
    next_month_experiment       TEXT,
    counterfactual_note         TEXT,
    lesson                      TEXT,
    confidence_label            TEXT
        CHECK (confidence_label IS NULL
            OR confidence_label IN ('fact', 'inference', 'speculation', 'mixed')),
    exported_at_utc             TEXT,
    daniel_notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_monthly_reviews_created
    ON monthly_reviews (created_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 4. saved_inspiration_posts — externally-saved X content (§10, §28.29).
-- ---------------------------------------------------------------------------
-- Paste-driven, no scraping. source_text_hash is sha256 of
-- source_post_text — unique to block accidental dupes. Paraphrases
-- hash differently and so still save; exact dupes don't.
CREATE TABLE IF NOT EXISTS saved_inspiration_posts (
    id                  INTEGER PRIMARY KEY,
    source_url          TEXT,
    source_author       TEXT,
    source_post_text    TEXT NOT NULL,
    source_text_hash    TEXT NOT NULL UNIQUE,
    tags_json           TEXT,
    saved_at_utc        TEXT NOT NULL DEFAULT (datetime('now')),
    notes               TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_saved_inspiration_saved
    ON saved_inspiration_posts (saved_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_saved_inspiration_author
    ON saved_inspiration_posts (source_author)
    WHERE source_author IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. inspiration_transforms — transform outputs + plagiarism guard (§10, §28.29).
-- ---------------------------------------------------------------------------
-- One row per (saved_inspiration_id, mode, click). Multiple per
-- source is normal — Daniel may run several transforms to see angles.
-- plagiarism_risk_label is the FINAL label = max(ai_reported_risk_label,
-- deterministic_label) where the ordering is low < medium < high.
-- The application layer (app/agent/inspiration.py::final_risk) computes
-- this; the DB stores the precomputed result so views and queries can
-- gate on it without joining to settings or recomputing.
CREATE TABLE IF NOT EXISTS inspiration_transforms (
    id                              INTEGER PRIMARY KEY,
    saved_inspiration_id            INTEGER NOT NULL
        REFERENCES saved_inspiration_posts(id) ON DELETE CASCADE,
    transform_mode                  TEXT NOT NULL
        CHECK (transform_mode IN (
            'structure', 'hook_pattern', 'counterpoint',
            'original_version', 'voice_profile_version',
            'expand', 'compress'
        )),
    output_text                     TEXT NOT NULL,
    output_text_hash                TEXT NOT NULL,
    jaccard_similarity              REAL NOT NULL DEFAULT 0
        CHECK (jaccard_similarity >= 0 AND jaccard_similarity <= 1),
    longest_shared_ngram_length     INTEGER NOT NULL DEFAULT 0
        CHECK (longest_shared_ngram_length >= 0),
    ai_reported_risk_label          TEXT NOT NULL DEFAULT 'low'
        CHECK (ai_reported_risk_label IN ('low', 'medium', 'high')),
    plagiarism_risk_label           TEXT NOT NULL DEFAULT 'low'
        CHECK (plagiarism_risk_label IN ('low', 'medium', 'high')),
    model_used                      TEXT NOT NULL,
    tokens_used                     INTEGER
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    created_at_utc                  TEXT NOT NULL DEFAULT (datetime('now')),
    used_for_post_id                INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    notes                           TEXT
);

CREATE INDEX IF NOT EXISTS idx_inspiration_transforms_source_created
    ON inspiration_transforms (saved_inspiration_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_inspiration_transforms_risk
    ON inspiration_transforms (plagiarism_risk_label);

-- ---------------------------------------------------------------------------
-- 6. audit_logs — append-only state-change record (§10, §28.30).
-- ---------------------------------------------------------------------------
-- Distinct from agent_tool_calls (which logs every invocation including
-- read-only). audit_logs only records state-changes. Append-only by
-- discipline: no UPDATE / DELETE in any application code path. Pruning
-- by retention is the only deletion source, and it self-audits as
-- 'admin'/'audit_logs_pruned'.
--
-- The agent has NO read or write access to this table. No tool registry
-- entry references it; app/agent/audit_log.py is server-side only.
CREATE TABLE IF NOT EXISTS audit_logs (
    id                  INTEGER PRIMARY KEY,
    occurred_at_utc     TEXT NOT NULL DEFAULT (datetime('now')),
    event_category      TEXT NOT NULL
        CHECK (event_category IN (
            'auth', 'x_op', 'publish', 'settings',
            'export', 'data', 'admin', 'migration'
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

CREATE INDEX IF NOT EXISTS idx_audit_logs_occurred
    ON audit_logs (occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_category_occurred
    ON audit_logs (event_category, occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target
    ON audit_logs (target_type, target_id)
    WHERE target_type IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 7. v_campaign_progress — per-campaign rollup view (§11).
-- ---------------------------------------------------------------------------
-- Powers §14.12 status sections, the per-campaign progress bar, and
-- the analyze_campaign_progress agent tool (§28.26 tool #21). NULL
-- percent fields when items_total = 0 — the UI shows "no items
-- planned yet" rather than rendering a 0% bar that misrepresents
-- a planning-stage campaign.
DROP VIEW IF EXISTS v_campaign_progress;
CREATE VIEW v_campaign_progress AS
WITH item_rollup AS (
    SELECT
        campaign_id,
        COUNT(*)                                                            AS items_total,
        SUM(CASE WHEN status = 'planned'  THEN 1 ELSE 0 END)                AS items_planned,
        SUM(CASE WHEN status = 'drafted'  THEN 1 ELSE 0 END)                AS items_drafted,
        SUM(CASE WHEN status = 'shipped'  THEN 1 ELSE 0 END)                AS items_shipped,
        SUM(CASE WHEN status = 'skipped'  THEN 1 ELSE 0 END)                AS items_skipped
    FROM campaign_items
    GROUP BY campaign_id
),
latest_shipped AS (
    SELECT
        ci.campaign_id,
        ci.post_id          AS latest_shipped_post_id,
        p.published_to_x_at AS latest_shipped_at_utc,
        ROW_NUMBER() OVER (
            PARTITION BY ci.campaign_id
            ORDER BY p.published_to_x_at DESC, ci.id DESC
        ) AS rn
    FROM campaign_items ci
    JOIN posts p ON p.id = ci.post_id
    WHERE ci.status = 'shipped' AND p.published_to_x_at IS NOT NULL
)
SELECT
    c.id                                                                AS campaign_id,
    c.name                                                              AS campaign_name,
    c.status,
    c.start_date,
    c.end_date,
    CAST(julianday(c.start_date) - julianday(date('now')) AS INTEGER)   AS days_until_start,
    CAST(julianday(c.end_date)   - julianday(date('now')) AS INTEGER)   AS days_until_end,
    COALESCE(r.items_total,   0)                                        AS items_total,
    COALESCE(r.items_planned, 0)                                        AS items_planned,
    COALESCE(r.items_drafted, 0)                                        AS items_drafted,
    COALESCE(r.items_shipped, 0)                                        AS items_shipped,
    COALESCE(r.items_skipped, 0)                                        AS items_skipped,
    CASE
        WHEN COALESCE(r.items_total, 0) = 0 THEN NULL
        ELSE CAST(r.items_shipped AS REAL) / r.items_total
    END                                                                 AS percent_shipped,
    CASE
        WHEN COALESCE(r.items_planned, 0)
           + COALESCE(r.items_drafted, 0)
           + COALESCE(r.items_shipped, 0) = 0 THEN NULL
        ELSE CAST(r.items_shipped AS REAL)
           / (r.items_planned + r.items_drafted + r.items_shipped)
    END                                                                 AS percent_planned_shipped,
    ls.latest_shipped_post_id,
    ls.latest_shipped_at_utc
FROM campaigns c
LEFT JOIN item_rollup r       ON r.campaign_id = c.id
LEFT JOIN latest_shipped ls   ON ls.campaign_id = c.id AND ls.rn = 1;

-- ---------------------------------------------------------------------------
-- 8. New settings rows (§25 Phase 5.11 migration checklist).
-- ---------------------------------------------------------------------------
-- INSERT OR IGNORE keeps re-application idempotent. Defaults match the
-- §25 checklist verbatim. The inspiration plagiarism thresholds ship as
-- starting points; tune via Settings without code changes.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('inspiration_plagiarism_jaccard_high_threshold', '0.65',
     'Jaccard token similarity >= this → deterministic plagiarism risk = high (§28.29). Tune in Settings; do not patch code constants.'),
    ('inspiration_plagiarism_jaccard_medium_threshold', '0.35',
     'Jaccard token similarity >= this (and < high threshold) → deterministic plagiarism risk = medium (§28.29).'),
    ('inspiration_plagiarism_ngram_high_threshold', '8',
     'Longest shared n-gram (in words) >= this → deterministic plagiarism risk = high (§28.29).'),
    ('inspiration_plagiarism_ngram_medium_threshold', '5',
     'Longest shared n-gram (in words) >= this (and < high threshold) → deterministic plagiarism risk = medium (§28.29).'),
    ('monthly_review_auto_draft_enabled', 'false',
     'When true, surfaces an auto-draft banner at the start of each month. Default OFF — same anti-anxiety stance as profile audit cadence (§28.27).'),
    ('audit_log_retention_days', '365',
     'audit_logs rows older than this are deleted by the daily prune job; the prune itself audit-logs as admin/audit_logs_pruned. 0 disables pruning (§28.30).'),
    ('calendar_default_view', '"week"',
     'Default §14.11 Content Calendar window: "week" | "two_weeks" | "month". Persisted per Daniel in st.session_state at runtime; this is the boot default (§28.28).'),
    ('calendar_am_cutoff_hour', '12',
     'Hour-of-day local time below which a slot is AM; at or above is PM. Default noon. Planned items without a time default to PM unless Daniel overrides (§28.28).');

-- ---------------------------------------------------------------------------
-- 9. Migration write-through to audit_logs.
-- ---------------------------------------------------------------------------
-- Audit log writes-through from day one — the migration itself logs
-- its own application. The application layer (app/agent/audit_log.py)
-- is the canonical path for everything else; here we go direct because
-- the helper module imports from the schema this very statement
-- creates. Guarded by NOT EXISTS so a manual re-run won't double-log.
INSERT INTO audit_logs (event_category, event_type, target_type, target_id, details_json, success)
SELECT 'migration', 'migration_applied_015', 'migration', '015_growth_layer_qol.sql',
       '{"tables":["campaigns","campaign_items","monthly_reviews","saved_inspiration_posts","inspiration_transforms","audit_logs"],"views":["v_campaign_progress"]}',
       1
WHERE NOT EXISTS (
    SELECT 1 FROM audit_logs
    WHERE event_category = 'migration'
      AND event_type = 'migration_applied_015'
);
