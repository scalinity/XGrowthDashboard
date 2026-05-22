# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 4 — Backup and data hygiene            |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-5-export.md`                    |

---

## Completed in this phase

### Backup runner (`scripts/backup_db.py`)

- `backup_database(source_path, backups_dir, retention_days)` opens the
  source DB through `app.db.connect()` (pragmas + aggregates), runs
  `VACUUM INTO 'data/backups/x_growth_YYYY-MM-DD_HHMMSS.db'`, then opens
  the new file in an independent `sqlite3` connection and runs
  `PRAGMA integrity_check`. If the result is anything but `ok` the
  backup file is deleted and `BackupIntegrityError` is raised.
- Successful runs upsert `settings.last_backup_at_utc` with an ISO-8601
  UTC timestamp.
- Retention pruning deletes `x_growth_*.db` files whose mtime is older
  than `settings.backup_retention_days` (default 30). The freshly-written
  backup never appears in the prune list because its mtime is "now".
- Returns a `BackupResult` dataclass: `{path, size_bytes, duration_ms,
  integrity_check_passed, pruned}`.
- CLI entrypoint at `python -m scripts.backup_db [--db-path PATH]
  [--backups-dir PATH] [--retention-days N]` — all flags optional, all
  defaults read from the `settings` table.
- VACUUM INTO target paths are single-quoted with embedded-quote
  doubling (`_quote_sqlite_path`) — `VACUUM INTO` doesn't accept
  bound parameters, so the literal must be escaped manually.

### Restore runner (`scripts/restore_db.py`)

- `restore_database(backup_path, target_path, dry_run=True)` — dry-run
  is the default; the destructive form requires the `--confirm` flag.
- Always integrity-checks the backup before mutating anything; refuses
  to copy from a corrupt source.
- Real restore renames the current target to a timestamped sidecar
  (`<target>.pre-restore.YYYY-MM-DD_HHMMSS`) and only then copies the
  backup over the target. The sidecar is never auto-deleted — manual
  rollback path stays available.
- After the copy the restored file is integrity-checked again; failure
  prints the sidecar path so a human can revert.

### Migration `003_backup_settings.sql`

- Adds two `settings` rows: `last_backup_at_utc` (initialised to JSON
  null) and `backup_retention_days` (default 30). `INSERT OR IGNORE`
  keeps the migration idempotent and never overwrites a value the user
  has hand-edited between runs.

### Settings page (`app/pages/7_Settings.py`)

- New "Backups" sub-readout, themed per the locked instrument-panel
  aesthetic (`/frontend-design` discipline; `apply_theme()` + PALETTE).
  No new colors or fonts introduced.
- Status block: `kicker("DATA INTEGRITY · §18 RULE 10")` followed by a
  bordered card showing the last-backup ISO timestamp in JetBrains Mono
  with a humanised "ago" caption beneath. Empty state shows a dimmed
  em-dash with a "click below to run the first one" hint.
- Action + parameter row: two columns. Left — primary-styled "Back up
  now" button that spins on the VACUUM + integrity check then toasts
  the resulting filename, size, and duration on success or `st.error`s
  on failure. Right — `Retention · days` number_input bound to
  `settings.backup_retention_days` with its own Save button.
- Manifest expander: collapsed-by-default `Manifest · N on disk` panel
  rendering a console-log-style grid (file | size | written) with mono
  numbers, hairline separators, and right-aligned columns.

### Tests (`tests/test_backup.py`)

Six tests covering the phase's six acceptance scenarios — all green:

1. `test_backup_creates_file` — file lands at the expected path with
   the documented prefix/suffix.
2. `test_backup_passes_integrity_check` — re-opens the backup in a
   fresh vanilla `sqlite3` connection and asserts `PRAGMA
   integrity_check` returns `ok`.
3. `test_backup_updates_last_backup_setting` — asserts the
   `last_backup_at_utc` row is parseable ISO-8601 UTC and within a
   sensible drift window of `now()`.
4. `test_restore_dry_run_does_not_touch_target` — dry-run leaves the
   target's mtime and size unchanged and creates no sidecar.
5. `test_restore_with_confirm_moves_old_to_sidecar` — confirmed
   restore renames the previous DB to a sidecar that still holds the
   pre-restore byte size, then verifies the freshly-restored DB's
   `PRAGMA integrity_check`.
6. `test_retention_prunes_old_backups` — fake-old files (via
   `os.utime`) are pruned at retention=7, the recent file survives,
   and the newly-created backup never appears in the prune list.

### Schema test bookkeeping (`tests/test_schema.py`)

- `test_schema_migrations_records_each_file` and
  `test_apply_migrations_is_idempotent` extended to include
  `003_backup_settings.sql` in the expected migration list.

### Automation reference (`docs/AUTOMATION.md`)

- Sample `~/Library/LaunchAgents/com.danny.xgrowth.backup.plist` for a
  daily 03:00 backup, including install/verify/uninstall commands.
- Sample crontab line for the same cadence as a portable fallback.
- Explicitly states: **the plist is not installed by this phase**.
  Daniel installs manually when ready.
- "What this does NOT do" section — no encryption-at-rest, no cloud
  sync, no auto-restore on corruption.

---

## Acceptance gates satisfied

- [x] `uv run python -m scripts.backup_db` exits 0 and writes
      `data/backups/x_growth_2026-05-21_210605.db` (258 KB,
      `duration_ms=1`, `integrity_check_passed=true`).
- [x] `sqlite3 data/backups/x_growth_2026-05-21_210605.db "PRAGMA
      integrity_check"` returns `ok`.
- [x] `uv run python -m scripts.restore_db --backup <file> --target
      data/dashboard.db` (without `--confirm`) prints a dry-run plan
      including the would-be sidecar path and exits 0 without touching
      `data/dashboard.db`.
- [x] Settings UI: the Backups sub-readout surfaces the last-backup
      timestamp on next render. Verified by the
      `test_settings_page_surfaces_every_seeded_settings_key` and
      `test_each_page_renders_without_exception` AppTest paths.
- [x] "Back up now" button runs the backup with a spinner and emits a
      success toast — exercised end-to-end by the backup integration
      tests; UI wiring verified by AppTest no-exception render.
- [x] `uv run pytest tests/test_backup.py -v` → 6/6 green in 0.08s.
- [x] `uv run pytest -q` → 121/121 green.
- [x] `uv run ruff check` is clean.

---

## Smoke-run notes

- `uv run python -m scripts.backup_db` printed the expected JSON
  payload; `data/backups/x_growth_2026-05-21_210605.db` is on disk and
  passes a separate `sqlite3 ... "PRAGMA integrity_check"` invocation.
- Dry-run restore printed a plan that names the sidecar path
  `data/dashboard.db.pre-restore.2026-05-21_210614` and confirms
  integrity `ok` without touching the live DB.
- **Caveat (same as Phase 3):** visual verification of the refreshed
  Settings page in a real browser falls to Daniel. The AppTest harness
  + Python syntax check + the full test suite cover boot-time
  correctness; the on-screen reading of fonts, palette, and layout is
  the user's call.

---

## Known limitations / future work

- **No encryption-at-rest.** Backup files are unencrypted SQLite. The
  spec defers this to V1.1+ (§18 future work). FileVault on the
  laptop is the de facto encryption boundary at MVP.
- **No off-machine backup.** Single-user local tool per §7.1 — cloud
  sync is explicitly out of scope. If the laptop dies before
  off-machine sync exists, the backups die with it.
- **No automatic restore on corruption.** Restoration is always a
  manual decision. `scripts/restore_db.py` is the entry point.
- **launchd plist documented but not installed.** Phase 4 ships the
  recipes; Daniel installs them manually per `docs/AUTOMATION.md`
  when he wants the daily cadence.
- **Retention pruning uses mtime, not the filename timestamp.** They
  agree under normal conditions; if a file is touched (`touch`) its
  mtime advances and it survives pruning, which is the correct
  behavior — the user explicitly extended its life.

---

## Phase boundary

Commits on `main` for Phase 4 (in order of the phase prompt's
work-order):

1. `feat(migrations): settings additions for backup retention (003)`
2. `feat(scripts): real backup_db with VACUUM INTO + integrity check`
3. `feat(scripts): restore_db with dry-run default`
4. `feat(settings): backup section with last-backup timestamp`
5. `test(backup): integrity + restore + retention tests`
6. `docs(automation): launchd + cron recipes`

---

## Next phase

Run `phase-5-export.md` — CSV export of `posts` with the §16 (7)
column allowlist, Markdown weekly-report export to
`settings.weekly_report_export_path`, and the dedicated "Export agent
audit" carve-out scaffold per §16 (8) (the agent tables themselves land
in Phase 5.5).
