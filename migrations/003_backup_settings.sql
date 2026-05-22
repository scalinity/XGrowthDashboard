-- ---------------------------------------------------------------------------
-- 003_backup_settings.sql — Phase 4
--
-- Adds the two settings rows the backup subsystem needs to operate:
--   - last_backup_at_utc   — ISO-8601 UTC timestamp of the most recent successful
--                            VACUUM INTO backup. NULL until the first backup runs.
--   - backup_retention_days — how many days of backups to keep in data/backups/.
--                              Files older than this are pruned at the end of each
--                              backup run. Default 30 (§18 rule 10).
--
-- Notes:
--   * The settings table itself was created in 001_initial.sql; we only seed
--     new rows here. INSERT OR IGNORE keeps the migration idempotent and
--     respects any value the user has hand-edited between runs.
--   * value_json stores JSON-encoded values so json_extract(...) returns the
--     native type (see scripts/seed_settings.py).
-- ---------------------------------------------------------------------------

INSERT OR IGNORE INTO settings (key, value_json, note)
VALUES (
    'last_backup_at_utc',
    'null',
    'ISO-8601 UTC of the most recent successful backup (§18 rule 10). NULL until first run.'
);

INSERT OR IGNORE INTO settings (key, value_json, note)
VALUES (
    'backup_retention_days',
    '30',
    'Prune backups older than this many days at the end of each backup run (§18 rule 10).'
);
