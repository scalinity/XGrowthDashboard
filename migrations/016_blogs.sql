-- migrations/016_blogs.sql — Phase 6 Long-form Blogs.
--
-- Four tables, one view, five settings rows, one self-audit row.
--
-- Spec anchors: §10 (blogs / blog_versions / blog_exports /
-- blog_to_post_links column lists), §11 (v_blog_pipeline rollup),
-- §25 Phase 6 migration checklist, §28.31 (state machine + versioning
-- discipline), §28.33 (export atomicity contract), §28.34 (X↔blog
-- repurposing linkage).
--
-- Slot 015 was consumed by 015_growth_layer_qol.sql during Phase 5.11
-- so Phase 6 lands at slot 016. The spec corrected the originally-drafted
-- "015_blogs.sql" reference on 2026-05-22 in the same commit that fixed
-- this number — see git log for the docs(spec) commit that precedes
-- this migration.
--
-- All statements are idempotent: CREATE TABLE / INDEX / VIEW use
-- IF NOT EXISTS / OR REPLACE; settings rows use INSERT OR IGNORE; the
-- migration_applied_016 audit row uses a guarded NOT EXISTS insert so
-- manual re-runs (outside apply_migrations) won't double-log.
-- apply_migrations records each file in schema_migrations so re-runs
-- are blocked at the runner level.

-- ---------------------------------------------------------------------------
-- 1. blogs — long-form post production rows (§10 blogs, §28.31).
-- ---------------------------------------------------------------------------
-- Each row is one blog; versioning lives in blog_versions. The state
-- machine (§28.31) is enforced in app/agent/blogs.py::transition_status
-- — the CHECK below only constrains the column to the eight legal
-- states; the legal *transitions* between states are application-layer
-- enforcement (CHECK constraints can't see prior row state in SQLite).
--
-- voice_profile_id_at_draft is nullable + ON DELETE SET NULL so a blog
-- survives the deletion of the voice profile it was authored under;
-- the identity context is preserved by niche_*_snapshot text columns.
CREATE TABLE IF NOT EXISTS blogs (
    id                              INTEGER PRIMARY KEY,
    slug                            TEXT NOT NULL UNIQUE,
    title                           TEXT NOT NULL,
    subtitle                        TEXT,
    current_body_markdown           TEXT NOT NULL DEFAULT '',
    status                          TEXT NOT NULL DEFAULT 'idea'
        CHECK (status IN (
            'idea', 'outlining', 'drafting', 'editing',
            'ready', 'exported', 'published_externally', 'archived'
        )),
    pillar                          TEXT,
    audience                        TEXT,
    outline_markdown                TEXT,
    seo_title                       TEXT,
    seo_description                 TEXT,
    seo_tags_json                   TEXT,
    external_url                    TEXT,
    external_published_at           TEXT,
    agent_assisted                  INTEGER NOT NULL DEFAULT 0
        CHECK (agent_assisted IN (0, 1)),
    voice_profile_id_at_draft       INTEGER
        REFERENCES voice_profiles(id) ON DELETE SET NULL,
    niche_problem_snapshot          TEXT,
    niche_person_snapshot           TEXT,
    target_length_words             INTEGER
        CHECK (target_length_words IS NULL OR target_length_words > 0),
    actual_length_words             INTEGER NOT NULL DEFAULT 0
        CHECK (actual_length_words >= 0),
    notes                           TEXT,
    created_at_utc                  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at_utc                  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_blogs_status_updated
    ON blogs (status, updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_blogs_pillar
    ON blogs (pillar)
    WHERE pillar IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_blogs_external_published
    ON blogs (external_published_at)
    WHERE external_published_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. blog_versions — immutable per-save history (§10 blog_versions, §28.31).
-- ---------------------------------------------------------------------------
-- Append-only. No-op detection lives in app/agent/blogs.py::save_blog
-- (when body_text_hash AND outline_markdown_at_version AND title_at_version
-- AND status_at_version all match the current version, the save returns
-- None without appending). The partial unique index on (blog_id) where
-- is_current_for_blog = true keeps "current pointer" honest — flipping
-- the pointer requires demoting the prior current row in the same
-- transaction.
--
-- Reverting to an older version creates a NEW version row carrying the
-- older body but a fresh version_number — forward-moving history per
-- §28.31. The older row's is_current_for_blog is NOT flipped back.
CREATE TABLE IF NOT EXISTS blog_versions (
    id                              INTEGER PRIMARY KEY,
    blog_id                         INTEGER NOT NULL
        REFERENCES blogs(id) ON DELETE CASCADE,
    version_number                  INTEGER NOT NULL
        CHECK (version_number > 0),
    body_markdown                   TEXT NOT NULL DEFAULT '',
    body_text_hash                  TEXT NOT NULL,
    title_at_version                TEXT NOT NULL,
    outline_markdown_at_version     TEXT,
    status_at_version               TEXT NOT NULL
        CHECK (status_at_version IN (
            'idea', 'outlining', 'drafting', 'editing',
            'ready', 'exported', 'published_externally', 'archived'
        )),
    created_by                      TEXT NOT NULL
        CHECK (created_by IN ('daniel', 'agent')),
    agent_message_id                INTEGER
        REFERENCES agent_messages(id) ON DELETE SET NULL,
    agent_action                    TEXT
        CHECK (agent_action IS NULL OR agent_action IN (
            'outline', 'draft', 'edit_suggestion_applied', 'seo_metadata'
        )),
    daniel_revision_note            TEXT,
    confidence_label_at_version     TEXT
        CHECK (confidence_label_at_version IS NULL
            OR confidence_label_at_version IN (
                'fact', 'inference', 'speculation', 'mixed'
            )),
    is_current_for_blog             INTEGER NOT NULL DEFAULT 0
        CHECK (is_current_for_blog IN (0, 1)),
    created_at_utc                  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_versions_blog_versionnum
    ON blog_versions (blog_id, version_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_versions_current_per_blog
    ON blog_versions (blog_id)
    WHERE is_current_for_blog = 1;
CREATE INDEX IF NOT EXISTS idx_blog_versions_blog_created
    ON blog_versions (blog_id, created_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 3. blog_exports — one row per export op (§10 blog_exports, §28.33).
-- ---------------------------------------------------------------------------
-- Atomic write-then-record: app/agent/blog_exports.py writes the file
-- first, then inserts this row. Re-exporting overwrites the file on
-- disk but inserts a NEW row — prior exports' rows are preserved as
-- audit history. content_sha256 is the audit anchor for detecting
-- later disk-side tampering or accidental overwrite.
--
-- blog_version_id is ON DELETE SET NULL so an export row survives the
-- (rare) deletion of its source blog_versions row; blog_id is ON DELETE
-- CASCADE because export history is meaningless without the parent blog.
CREATE TABLE IF NOT EXISTS blog_exports (
    id                          INTEGER PRIMARY KEY,
    blog_id                     INTEGER NOT NULL
        REFERENCES blogs(id) ON DELETE CASCADE,
    blog_version_id             INTEGER
        REFERENCES blog_versions(id) ON DELETE SET NULL,
    format                      TEXT NOT NULL
        CHECK (format IN ('markdown', 'html', 'json', 'mdx')),
    target_path                 TEXT NOT NULL,
    file_size_bytes             INTEGER NOT NULL
        CHECK (file_size_bytes >= 0),
    content_sha256              TEXT NOT NULL,
    seo_metadata_included       INTEGER NOT NULL DEFAULT 0
        CHECK (seo_metadata_included IN (0, 1)),
    repurposing_links_included  INTEGER NOT NULL DEFAULT 0
        CHECK (repurposing_links_included IN (0, 1)),
    exported_at_utc             TEXT NOT NULL DEFAULT (datetime('now')),
    daniel_notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_blog_exports_blog_exported
    ON blog_exports (blog_id, exported_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_blog_exports_format
    ON blog_exports (format);
CREATE INDEX IF NOT EXISTS idx_blog_exports_exported
    ON blog_exports (exported_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 4. blog_to_post_links — bidirectional repurposing linkage
--    (§10 blog_to_post_links, §28.34).
-- ---------------------------------------------------------------------------
-- direction = blog_to_post when an X post was derived from this blog;
-- post_to_blog when this blog was expanded from an X post idea; parallel
-- when both were authored from a shared idea concurrently. unique
-- (blog_id, post_id, direction) lets multiple directions coexist for
-- the same pair without dup-blocking.
--
-- agent_message_id is ON DELETE SET NULL so a link survives the
-- (uncommon) deletion of the agent message that proposed it; the link
-- itself is the persistent record.
CREATE TABLE IF NOT EXISTS blog_to_post_links (
    id                  INTEGER PRIMARY KEY,
    blog_id             INTEGER NOT NULL
        REFERENCES blogs(id) ON DELETE CASCADE,
    post_id             INTEGER NOT NULL
        REFERENCES posts(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL
        CHECK (direction IN ('blog_to_post', 'post_to_blog', 'parallel')),
    relationship_kind   TEXT NOT NULL
        CHECK (relationship_kind IN (
            'thread_root', 'quote_excerpt', 'summary_post',
            'teaser_with_link', 'derived_outline', 'companion_post'
        )),
    notes               TEXT,
    created_at_utc      TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT NOT NULL DEFAULT 'daniel'
        CHECK (created_by IN ('daniel', 'agent')),
    agent_message_id    INTEGER
        REFERENCES agent_messages(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_to_post_links_unique
    ON blog_to_post_links (blog_id, post_id, direction);
CREATE INDEX IF NOT EXISTS idx_blog_to_post_links_blog
    ON blog_to_post_links (blog_id);
CREATE INDEX IF NOT EXISTS idx_blog_to_post_links_post
    ON blog_to_post_links (post_id);

-- ---------------------------------------------------------------------------
-- 5. v_blog_pipeline — per-blog rollup for §14.14 Blogs index (§11).
-- ---------------------------------------------------------------------------
-- Rolls up blogs + blog_versions (current row + counts + latest
-- agent confidence) + blog_exports (count + latest). days_in_current_status
-- comes from the earliest blog_versions row whose status_at_version matches
-- the blog's current status — the moment the blog entered its current state.
-- Stale states surface to UI via this column.
DROP VIEW IF EXISTS v_blog_pipeline;
CREATE VIEW v_blog_pipeline AS
WITH version_rollup AS (
    SELECT
        blog_id,
        COUNT(*)                                                                AS total_version_count,
        MAX(created_at_utc)                                                     AS last_edited_at_utc
    FROM blog_versions
    GROUP BY blog_id
),
current_version AS (
    SELECT
        blog_id,
        version_number                                                          AS current_version_number,
        created_by                                                              AS last_edited_by,
        created_at_utc                                                          AS current_version_created_at
    FROM blog_versions
    WHERE is_current_for_blog = 1
),
latest_agent_confidence AS (
    SELECT
        blog_id,
        confidence_label_at_version                                             AS latest_confidence_label,
        ROW_NUMBER() OVER (
            PARTITION BY blog_id
            ORDER BY created_at_utc DESC, id DESC
        )                                                                       AS rn
    FROM blog_versions
    WHERE created_by = 'agent'
      AND confidence_label_at_version IS NOT NULL
),
export_rollup AS (
    SELECT
        blog_id,
        COUNT(*)                                                                AS export_count,
        MAX(exported_at_utc)                                                    AS last_exported_at_utc
    FROM blog_exports
    GROUP BY blog_id
),
latest_export AS (
    SELECT
        blog_id,
        format                                                                  AS last_export_format,
        ROW_NUMBER() OVER (
            PARTITION BY blog_id
            ORDER BY exported_at_utc DESC, id DESC
        )                                                                       AS rn
    FROM blog_exports
),
status_entry_at AS (
    -- Earliest blog_versions row whose status_at_version matches the blog's
    -- current status — i.e., the moment the blog entered this state.
    SELECT
        bv.blog_id,
        MIN(bv.created_at_utc)                                                  AS entered_at_utc
    FROM blog_versions bv
    JOIN blogs b ON b.id = bv.blog_id
    WHERE bv.status_at_version = b.status
    GROUP BY bv.blog_id
)
SELECT
    b.id                                                                        AS blog_id,
    b.title,
    b.slug,
    b.status,
    b.pillar,
    b.audience,
    cv.current_version_number,
    COALESCE(vr.total_version_count, 0)                                         AS total_version_count,
    vr.last_edited_at_utc,
    cv.last_edited_by,
    CASE
        WHEN vr.last_edited_at_utc IS NULL THEN NULL
        ELSE CAST(julianday(date('now'))
               - julianday(date(vr.last_edited_at_utc)) AS INTEGER)
    END                                                                         AS days_since_last_edit,
    b.agent_assisted,
    lac.latest_confidence_label,
    b.actual_length_words,
    b.target_length_words,
    CASE
        WHEN b.target_length_words IS NULL THEN NULL
        ELSE b.actual_length_words - b.target_length_words
    END                                                                         AS length_gap_words,
    COALESCE(er.export_count, 0)                                                AS export_count,
    er.last_exported_at_utc,
    le.last_export_format,
    b.external_url,
    b.external_published_at,
    CASE
        WHEN sea.entered_at_utc IS NULL THEN NULL
        ELSE CAST(julianday(date('now'))
               - julianday(date(sea.entered_at_utc)) AS INTEGER)
    END                                                                         AS days_in_current_status
FROM blogs b
LEFT JOIN version_rollup vr           ON vr.blog_id = b.id
LEFT JOIN current_version cv          ON cv.blog_id = b.id
LEFT JOIN latest_agent_confidence lac ON lac.blog_id = b.id AND lac.rn = 1
LEFT JOIN export_rollup er            ON er.blog_id = b.id
LEFT JOIN latest_export le            ON le.blog_id = b.id AND le.rn = 1
LEFT JOIN status_entry_at sea         ON sea.blog_id = b.id;

-- ---------------------------------------------------------------------------
-- 6. New settings rows (§25 Phase 6 migration checklist).
-- ---------------------------------------------------------------------------
-- INSERT OR IGNORE keeps re-application idempotent. Defaults match the
-- §25 Phase 6 checklist verbatim. scripts/seed_settings.py mirrors
-- these same five rows so init_db produces an identical schema state.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('blog_stale_status_warning_days', '21',
     'Rows with days_in_current_status > this surface a yellow stale-state keyline in §14.14 Blogs index (§28.31).'),
    ('blog_default_target_length_words', '1500',
     'Default target_length_words for new blogs when Daniel does not specify one (§14.14 "+ new blog" form).'),
    ('blog_export_default_directory', '"data/blog_exports/"',
     'Default target-path prefix for the §14.15 export dialog (§28.33). Relative to repo root unless absolute.'),
    ('blog_repurposing_plagiarism_check_enabled', 'true',
     'When true, blog→X repurposing outputs run through the §28.29 deterministic plagiarism floor against the source blog body. Disable only for testing (§28.34).'),
    ('blog_agent_max_draft_iterations', '3',
     'Informational ceiling on consecutive agent draft_blog calls within a single editing session (§28.32). UI surfaces a soft warning at this count; not enforced in code.');

-- ---------------------------------------------------------------------------
-- 7. Migration write-through to audit_logs (§28.30).
-- ---------------------------------------------------------------------------
-- The migration logs its own application. Guarded by NOT EXISTS so a
-- manual re-run (outside apply_migrations) won't double-log; the runner
-- itself records each .sql file in schema_migrations and skips re-runs.
INSERT INTO audit_logs (event_category, event_type, target_type, target_id, details_json, success)
SELECT 'migration', 'migration_applied_016', 'migration', '016_blogs.sql',
       '{"tables":["blogs","blog_versions","blog_exports","blog_to_post_links"],"views":["v_blog_pipeline"]}',
       1
WHERE NOT EXISTS (
    SELECT 1 FROM audit_logs
    WHERE event_category = 'migration'
      AND event_type = 'migration_applied_016'
);
