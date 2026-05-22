-- ---------------------------------------------------------------------------
-- 004_data_exports.sql — Phase 5
--
-- Audit log of every export run. Lets Daniel answer "did I export Q3?"
-- months later without `ls -lt data/exports/` archaeology.
--
-- Why a dedicated table rather than reusing settings or an in-memory list:
--   * Manifests in the Settings page survive Streamlit reruns and process
--     restarts only if persisted.
--   * Export runs are bounded events with structured metadata (row count,
--     opt-in flag, output path) — they fit a row better than a JSON blob in
--     settings.
--   * Volume is tiny (handful per week) so the cost is negligible.
--
-- Notes:
--   * `kind` distinguishes csv / markdown_weekly / json so the page can
--     filter by export kind without parsing path strings.
--   * `table_name` is NULL for non-table exports (markdown_weekly / json).
--   * `include_opt_in` is 0/1 SQLite-bool. NULL for non-CSV exports.
--   * `notes` carries free-text context (e.g. "blocked: counterfactual missing"
--     when an export attempt fails). Successful runs typically leave this NULL.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS data_exports (
    id              INTEGER PRIMARY KEY,
    exported_at_utc TEXT NOT NULL DEFAULT (datetime('now')),
    kind            TEXT NOT NULL
        CHECK (kind IN ('csv', 'markdown_weekly', 'json')),
    table_name      TEXT,
    output_path     TEXT NOT NULL,
    row_count       INTEGER,
    include_opt_in  INTEGER
        CHECK (include_opt_in IS NULL OR include_opt_in IN (0, 1)),
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_data_exports_kind
    ON data_exports (kind);
CREATE INDEX IF NOT EXISTS idx_data_exports_exported_at
    ON data_exports (exported_at_utc);
