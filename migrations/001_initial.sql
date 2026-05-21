-- Phase 1 initial schema — see spec.md §10.
-- All timestamps are TEXT with `datetime('now')` defaults (UTC).
-- updated_at_utc is set by application code, never by triggers.
-- Foreign keys require PRAGMA foreign_keys = ON at the connection level
-- (see app/db.py); this migration declares them, app/db.py enforces them.

-- ---------------------------------------------------------------------------
-- settings — global key/value config (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    note        TEXT
);

-- ---------------------------------------------------------------------------
-- account_snapshots — immutable daily X account snapshots (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_snapshots (
    id                 INTEGER PRIMARY KEY,
    snapshot_date      TEXT NOT NULL,
    collected_at_utc   TEXT NOT NULL,
    x_user_id          TEXT,
    username           TEXT NOT NULL,
    profile_url        TEXT NOT NULL,
    followers_count    INTEGER NOT NULL,
    following_count    INTEGER NOT NULL,
    post_count         INTEGER NOT NULL,
    listed_count       INTEGER NOT NULL,
    like_count         INTEGER,
    media_count        INTEGER,
    bio_text           TEXT,
    baseline_followers INTEGER NOT NULL,
    source             TEXT NOT NULL
        CHECK (source IN ('manual', 'xurl', 'api', 'csv_import')),
    data_quality       TEXT NOT NULL
        CHECK (data_quality IN ('exact', 'manual', 'estimated', 'partial', 'failed')),
    raw_response_id    INTEGER REFERENCES raw_api_responses(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Unique on (x_user_id, collected_at_utc) where x_user_id is not null.
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_snapshots_user_collected
    ON account_snapshots (x_user_id, collected_at_utc)
    WHERE x_user_id IS NOT NULL;

-- Unique on (username, collected_at_utc) when x_user_id is null.
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_snapshots_username_collected
    ON account_snapshots (username, collected_at_utc)
    WHERE x_user_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_account_snapshots_date
    ON account_snapshots (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_user_date
    ON account_snapshots (x_user_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_raw_response
    ON account_snapshots (raw_response_id);

-- ---------------------------------------------------------------------------
-- account_snapshot_corrections — manual corrections without mutating raw (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_snapshot_corrections (
    id          INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES account_snapshots(id) ON DELETE RESTRICT,
    field_name  TEXT NOT NULL,
    old_value   TEXT NOT NULL,
    new_value   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_acct_corrections_snapshot
    ON account_snapshot_corrections (snapshot_id);

-- ---------------------------------------------------------------------------
-- raw_api_responses — auditability for V1.1 (§10.2). Empty until V1.1.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_api_responses (
    id                    INTEGER PRIMARY KEY,
    source                TEXT NOT NULL
        CHECK (source IN ('x_api', 'xurl', 'website_analytics', 'app_store', 'manual_import')),
    endpoint_or_command   TEXT NOT NULL,
    request_params_json   TEXT,
    response_json         TEXT NOT NULL,
    status_code           INTEGER,
    collected_at_utc      TEXT NOT NULL,
    request_cost_estimate REAL,
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_responses_source
    ON raw_api_responses (source);
CREATE INDEX IF NOT EXISTS idx_raw_responses_collected
    ON raw_api_responses (collected_at_utc);

-- ---------------------------------------------------------------------------
-- posts — one row per post/reply/quote/thread (§10.2 Phase 1 columns only;
-- agent_draft_id, publish_*, etc. are added in Phase 5.5 migrations).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS posts (
    id                         INTEGER PRIMARY KEY,
    x_post_id                  TEXT UNIQUE,
    created_at_utc             TEXT,
    created_date               TEXT NOT NULL,
    text                       TEXT NOT NULL,
    url                        TEXT,
    type                       TEXT NOT NULL
        CHECK (type IN ('standalone', 'reply', 'quote', 'thread_root', 'thread_child')),
    conversation_id            TEXT,
    in_reply_to_post_id        TEXT,
    in_reply_to_user           TEXT,
    posted_via                 TEXT NOT NULL
        CHECK (posted_via IN ('manual', 'xurl', 'api', 'imported', 'agent_assisted', 'unknown')),
    manual_confirmation_status TEXT NOT NULL
        CHECK (manual_confirmation_status IN ('confirmed', 'needs_id', 'needs_metrics', 'draft')),
    contains_link              INTEGER NOT NULL DEFAULT 0 CHECK (contains_link IN (0, 1)),
    expanded_urls_json         TEXT,
    utm_source                 TEXT,
    utm_medium                 TEXT,
    utm_campaign               TEXT,
    utm_content                TEXT,
    utm_term                   TEXT,
    raw_response_id            INTEGER REFERENCES raw_api_responses(id) ON DELETE SET NULL,
    created_in_app_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_posts_created_date
    ON posts (created_date);
CREATE INDEX IF NOT EXISTS idx_posts_type
    ON posts (type);
CREATE INDEX IF NOT EXISTS idx_posts_conversation
    ON posts (conversation_id);
CREATE INDEX IF NOT EXISTS idx_posts_utm_campaign
    ON posts (utm_campaign);
CREATE INDEX IF NOT EXISTS idx_posts_raw_response
    ON posts (raw_response_id);
CREATE INDEX IF NOT EXISTS idx_posts_x_post_id
    ON posts (x_post_id);

-- ---------------------------------------------------------------------------
-- post_metric_snapshots — metric snapshots over time (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS post_metric_snapshots (
    id                  INTEGER PRIMARY KEY,
    post_id             INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    x_post_id           TEXT NOT NULL,
    collected_at_utc    TEXT NOT NULL,
    impressions         INTEGER,
    likes               INTEGER,
    replies             INTEGER,
    reposts             INTEGER,
    quotes              INTEGER,
    bookmarks           INTEGER,
    engagements_total   INTEGER,
    profile_clicks      INTEGER,
    url_link_clicks     INTEGER,
    source              TEXT NOT NULL
        CHECK (source IN ('manual', 'xurl', 'api', 'csv_import')),
    data_quality        TEXT NOT NULL
        CHECK (data_quality IN ('exact', 'manual', 'estimated', 'partial')),
    raw_response_id     INTEGER REFERENCES raw_api_responses(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pms_post
    ON post_metric_snapshots (post_id);
CREATE INDEX IF NOT EXISTS idx_pms_post_collected
    ON post_metric_snapshots (post_id, collected_at_utc);
CREATE INDEX IF NOT EXISTS idx_pms_x_post
    ON post_metric_snapshots (x_post_id);
CREATE INDEX IF NOT EXISTS idx_pms_raw_response
    ON post_metric_snapshots (raw_response_id);

-- ---------------------------------------------------------------------------
-- post_classifications — content metadata + learning notes (§10.2)
-- pillar/audience/cta are TEXT so v2 taxonomy expansion is a config change.
-- §10.2 explicitly states "stores these as text rather than rigid enum so v2
-- is a config change, not a migration." v1 values are seeded by app dropdowns
-- and the agent system prompt; no CHECK constraint here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS post_classifications (
    id              INTEGER PRIMARY KEY,
    post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    pillar          TEXT NOT NULL,
    audience        TEXT NOT NULL,
    cta             TEXT NOT NULL,
    quality_score   INTEGER CHECK (quality_score IS NULL OR (quality_score BETWEEN 1 AND 5)),
    why_posted      TEXT,
    hypothesis      TEXT,
    expected_signal TEXT,
    actual_signal   TEXT,
    lesson          TEXT,
    classified_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_post_class_post
    ON post_classifications (post_id);
CREATE INDEX IF NOT EXISTS idx_post_class_lane
    ON post_classifications (pillar, audience, cta);

-- ---------------------------------------------------------------------------
-- daily_activity — daily reps + behavior tracking (§10.2)
-- activity_date is the PK per §10.2.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_activity (
    activity_date                    TEXT PRIMARY KEY,
    planned_posts                    INTEGER NOT NULL DEFAULT 0,
    planned_replies                  INTEGER NOT NULL DEFAULT 0,
    planned_quotes                   INTEGER NOT NULL DEFAULT 0,
    posts_shipped                    INTEGER NOT NULL DEFAULT 0,
    replies_shipped                  INTEGER NOT NULL DEFAULT 0,
    quotes_shipped                   INTEGER NOT NULL DEFAULT 0,
    high_quality_reply_targets_found INTEGER NOT NULL DEFAULT 0,
    reply_sessions_completed         INTEGER NOT NULL DEFAULT 0,
    minimum_reps_completed           INTEGER NOT NULL DEFAULT 0
        CHECK (minimum_reps_completed IN (0, 1)),
    time_spent_minutes               INTEGER,
    manual_actions_count             INTEGER,
    api_actions_count                INTEGER,
    avoidance_notes                  TEXT,
    daily_note                       TEXT,
    created_at                       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- reply_sessions — one row per deliberate reply "workout" (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reply_sessions (
    id                    INTEGER PRIMARY KEY,
    session_date          TEXT NOT NULL,
    started_at            TEXT,
    duration_minutes      INTEGER,
    target_lane           TEXT NOT NULL,
    target_accounts_json  TEXT,
    targets_found         INTEGER NOT NULL DEFAULT 0,
    replies_shipped       INTEGER NOT NULL DEFAULT 0,
    best_reply_post_id    INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    session_quality_score INTEGER
        CHECK (session_quality_score IS NULL OR (session_quality_score BETWEEN 1 AND 5)),
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_reply_sessions_date
    ON reply_sessions (session_date);
CREATE INDEX IF NOT EXISTS idx_reply_sessions_best_reply
    ON reply_sessions (best_reply_post_id);

-- ---------------------------------------------------------------------------
-- stir_conversion_events — event-level Stir funnel (§10.2)
-- 4-category schema; event_type is free-text for retroactive categorization.
-- is_likely_icp is only ever set when attribution_method='self_reported'
-- (privacy rule §10.2 / §18); enforced via CHECK.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stir_conversion_events (
    id                    INTEGER PRIMARY KEY,
    occurred_at_utc       TEXT NOT NULL,
    event_date            TEXT NOT NULL,
    event_category        TEXT NOT NULL
        CHECK (event_category IN ('acquisition', 'activation', 'usage', 'feedback')),
    event_type            TEXT NOT NULL,
    source                TEXT,
    medium                TEXT,
    campaign              TEXT,
    utm_source            TEXT,
    utm_medium            TEXT,
    utm_campaign          TEXT,
    utm_content           TEXT,
    referring_post_id     INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    referring_x_handle    TEXT,
    attribution_method    TEXT NOT NULL
        CHECK (attribution_method IN ('self_reported', 'utm', 'referrer_header', 'inferred', 'unknown')),
    is_likely_icp         INTEGER
        CHECK (is_likely_icp IS NULL OR is_likely_icp IN (0, 1)),
    qualitative_feedback  TEXT,
    source_data_quality   TEXT NOT NULL
        CHECK (source_data_quality IN ('exact', 'manual', 'estimated', 'inferred', 'unknown')),
    raw_response_id       INTEGER REFERENCES raw_api_responses(id) ON DELETE SET NULL,
    -- §10.2 privacy rule: is_likely_icp may only be set when self_reported.
    CHECK (is_likely_icp IS NULL OR attribution_method = 'self_reported')
);

CREATE INDEX IF NOT EXISTS idx_stir_events_date
    ON stir_conversion_events (event_date);
CREATE INDEX IF NOT EXISTS idx_stir_events_category
    ON stir_conversion_events (event_category);
CREATE INDEX IF NOT EXISTS idx_stir_events_attribution
    ON stir_conversion_events (attribution_method);
CREATE INDEX IF NOT EXISTS idx_stir_events_referring_post
    ON stir_conversion_events (referring_post_id);
CREATE INDEX IF NOT EXISTS idx_stir_events_raw_response
    ON stir_conversion_events (raw_response_id);

-- ---------------------------------------------------------------------------
-- stir_testers — person-level tester records (§10.2)
-- is_working_parent_home_cook is self-report-only per §10.2 / §18 rule 11.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stir_testers (
    id                          INTEGER PRIMARY KEY,
    alias                       TEXT NOT NULL,
    x_handle                    TEXT,
    contact_ref                 TEXT,
    source                      TEXT,
    first_seen_date             TEXT NOT NULL,
    is_working_parent_home_cook INTEGER
        CHECK (is_working_parent_home_cook IS NULL OR is_working_parent_home_cook IN (0, 1)),
    icp_notes                   TEXT,
    downloaded_app_at           TEXT,
    scanned_kitchen_at          TEXT,
    got_plausible_dinners_at    TEXT,
    used_cook_mode_at           TEXT,
    feedback_summary            TEXT,
    status                      TEXT NOT NULL
        CHECK (status IN ('lead', 'downloaded', 'activated', 'cook_mode_used', 'churned', 'unknown'))
);

CREATE INDEX IF NOT EXISTS idx_stir_testers_status
    ON stir_testers (status);

-- ---------------------------------------------------------------------------
-- milestones — distribution + validation + content + reps ladders (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS milestones (
    id                     INTEGER PRIMARY KEY,
    category               TEXT NOT NULL
        CHECK (category IN ('distribution', 'validation', 'content', 'reps')),
    ladder_position        INTEGER NOT NULL CHECK (ladder_position >= 1),
    name                   TEXT NOT NULL,
    start_value            INTEGER,
    target_value           INTEGER,
    current_value_override INTEGER,
    status                 TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN ('not_started', 'in_progress', 'achieved', 'skipped')),
    achieved_at            TEXT,
    notes                  TEXT,
    UNIQUE (category, ladder_position)
);

CREATE INDEX IF NOT EXISTS idx_milestones_category
    ON milestones (category);

-- ---------------------------------------------------------------------------
-- weekly_reviews — weekly postmortems (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_reviews (
    id                        INTEGER PRIMARY KEY,
    week_start_date           TEXT NOT NULL,
    week_end_date             TEXT NOT NULL,
    followers_start           INTEGER,
    followers_end             INTEGER,
    follower_delta            INTEGER,
    posts_shipped             INTEGER NOT NULL DEFAULT 0,
    replies_shipped           INTEGER NOT NULL DEFAULT 0,
    reply_sessions_completed  INTEGER NOT NULL DEFAULT 0,
    daily_reps_days_completed INTEGER NOT NULL DEFAULT 0,
    best_post_id              INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    worst_post_id             INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    strongest_pillar          TEXT,
    weakest_pillar            TEXT,
    downloads                 INTEGER NOT NULL DEFAULT 0,
    qualified_icp_testers     INTEGER NOT NULL DEFAULT 0,
    what_moved                TEXT,
    what_got_stuck            TEXT,
    lesson                    TEXT,
    next_week_experiment      TEXT,
    counterfactual_note       TEXT,
    exported_markdown_path    TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (week_start_date)
);

CREATE INDEX IF NOT EXISTS idx_weekly_reviews_best
    ON weekly_reviews (best_post_id);
CREATE INDEX IF NOT EXISTS idx_weekly_reviews_worst
    ON weekly_reviews (worst_post_id);

-- ---------------------------------------------------------------------------
-- experiments — optional named hypotheses (§10.2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS experiments (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    start_date          TEXT NOT NULL,
    end_date            TEXT,
    hypothesis          TEXT NOT NULL,
    content_lane        TEXT,
    target_audience     TEXT,
    success_metric      TEXT NOT NULL,
    minimum_sample_size INTEGER,
    result_summary      TEXT,
    status              TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'completed', 'abandoned'))
);

CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON experiments (status);
