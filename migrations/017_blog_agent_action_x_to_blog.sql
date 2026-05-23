-- migrations/017_blog_agent_action_x_to_blog.sql — Phase 6 review fix
-- #P6R-17 (and #P6R-24 piggybacked since the view rebuild is free).
--
-- Two changes:
--
-- 1. #P6R-17: extend blog_versions.agent_action CHECK to admit
--    'x_to_blog_idea_outline'. Pre-fix, repurpose_x_to_blog_idea
--    wrote agent_action='outline', indistinguishable from the
--    standalone tool #25. Analytics needs the disambiguation.
--
-- 2. #P6R-24: switch v_blog_pipeline.days_in_current_status from
--    MIN(blog_versions.created_at_utc) to MAX(...). Pre-fix, a blog
--    going editing → drafting → editing reported days since the
--    FIRST entry into editing, overstating staleness. The fix uses
--    the latest contiguous-run entry (approximated via MAX).
--
-- SQLite can't ALTER a CHECK constraint in place — the only path is
-- the canonical 12-step ALTER-TABLE rebuild recipe. Important
-- subtlety: we create the replacement table with a DIFFERENT name,
-- copy rows, drop the original, then rename. This preserves FK
-- references pointing at "blog_versions" (from blog_exports) so they
-- end up bound to our new table; the alternative (rename original
-- aside, then create new) leaves dangling FKs pointing at the
-- aside-renamed shell.
--
-- PRAGMA foreign_keys is toggled off before the rebuild and on
-- after, per the SQLite docs. apply_migrations() runs each .sql via
-- executescript(), which auto-commits any open txn first, so the
-- PRAGMA statements take effect at the connection level for the
-- duration of the script.

PRAGMA foreign_keys = OFF;

-- Step 0: drop the view first — SQLite refuses DROP TABLE while a
-- view references it. We rebuild the view at Step 6 below.
DROP VIEW IF EXISTS v_blog_pipeline;

-- Step 1: create the replacement table under a temporary name.
CREATE TABLE blog_versions_new (
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
            'outline', 'draft', 'edit_suggestion_applied', 'seo_metadata',
            'x_to_blog_idea_outline'
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

-- Step 2: copy all existing rows over verbatim. Column order matches.
INSERT INTO blog_versions_new
SELECT * FROM blog_versions;

-- Step 3: drop the old table. Its indexes get dropped with it.
-- blog_exports.blog_version_id FK was pointing at "blog_versions" by
-- name; with PRAGMA foreign_keys=OFF, the drop is permitted, and the
-- subsequent rename re-binds the name to the new table.
DROP TABLE blog_versions;

-- Step 4: rename the new table into place.
ALTER TABLE blog_versions_new RENAME TO blog_versions;

-- Step 5: rebuild indexes (they don't carry over with the rename).
CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_versions_blog_versionnum
    ON blog_versions (blog_id, version_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_blog_versions_current_per_blog
    ON blog_versions (blog_id)
    WHERE is_current_for_blog = 1;
CREATE INDEX IF NOT EXISTS idx_blog_versions_blog_created
    ON blog_versions (blog_id, created_at_utc DESC);

-- Step 6: rebuild v_blog_pipeline. The view's status_entry_at CTE
-- was previously MIN(created_at_utc); switch to MAX(...) for
-- #P6R-24. The view was dropped at Step 0 so SQLite would allow the
-- DROP TABLE in Step 3.
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
    -- #P6R-24: use MAX (latest entry into the current status), not
    -- MIN (earliest entry — overstated staleness when a status was
    -- entered multiple times via backward edges like
    -- editing → drafting → editing). A perfectly-correct "last
    -- contiguous run" calculation needs a window function with
    -- gap-and-island logic; MAX is a strict improvement and matches
    -- Daniel's intuition of "how long since I last touched this in
    -- this state".
    SELECT
        bv.blog_id,
        MAX(bv.created_at_utc)                                                  AS entered_at_utc
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

PRAGMA foreign_keys = ON;

-- Migration write-through to audit_logs (§28.30).
INSERT INTO audit_logs (event_category, event_type, target_type, target_id, details_json, success)
SELECT 'migration', 'migration_applied_017', 'migration', '017_blog_agent_action_x_to_blog.sql',
       '{"changes":["blog_versions.agent_action CHECK now admits x_to_blog_idea_outline (#P6R-17)","v_blog_pipeline.days_in_current_status switched MIN→MAX entry (#P6R-24)"]}',
       1
WHERE NOT EXISTS (
    SELECT 1 FROM audit_logs
    WHERE event_category = 'migration'
      AND event_type = 'migration_applied_017'
);
