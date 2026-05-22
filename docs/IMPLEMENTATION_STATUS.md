# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 5 — Export                             |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-5.5-growth-agent.md`            |

---

## Completed in this phase

### Allowlist module (`app/exports/allowlists.py`)

- `TableAllowlist` TypedDict with `default_columns` / `opt_in_columns` /
  `excluded_columns`.
- Per-table allowlists for 11 MVP tables: `account_snapshots`, `posts`,
  `post_metric_snapshots`, `post_classifications`, `daily_activity`,
  `reply_sessions`, `stir_conversion_events`, `stir_testers`,
  `milestones`, `weekly_reviews`, `experiments`.
- `POSTS_ALLOWLIST` ships only the currently-existing Phase 1 columns;
  the file body has `# PHASE 5.5 INSERT HERE` / `# PHASE 5.6 INSERT HERE`
  markers that the future phase prompts cite directly.
- `columns_for_export(table_name, include_opt_in=False)` — single-helper
  surface that fails fast (`ValueError`) when the allowlist for a table
  is internally inconsistent (a column in both an inclusion list and the
  excluded list).
- `UnknownTableError(KeyError)` for callers passing an unregistered table.
- `docs/ARCHITECTURE.md` "Export allowlist contract" section documents
  the per-column policy for Phase 5.5 / 5.6 additions.

### CSV exporter (`app/exports/csv_exporter.py`)

- `export_table_to_csv(table_name, output_path, *, include_opt_in=False,
  conn=None, db_path=None)` — UTF-8 CSV with header.
- Defensive `_quote_identifier()` for the SELECT (allowlist column names
  come from this repo's own source, but quoting future-proofs against a
  keyword collision).
- Records each run in `data_exports` (kind=`csv`).
- CLI: `python -m app.exports.csv_exporter --table <t> --output <p> [--opt-in]`.

### Markdown weekly exporter (`app/exports/markdown_weekly.py`)

- `export_weekly_report(week_iso, output_path=None)` renders a §16 / §24
  weekly report.
- **Counterfactual gating** raises `CounterfactualMissingError` when the
  `weekly_reviews` row is missing OR `counterfactual_note` is NULL OR
  whitespace-only. The gate sits at the export layer regardless of the
  `counterfactual_required` settings toggle (per `docs/ARCHITECTURE.md`).
- Sections rendered: Summary, Reps shipped, Content performance top 3
  lanes (with confidence label visible), Stir funnel with App-Store-gap
  block, What moved / What got stuck, Lesson, Next week's experiment,
  Counterfactual (verbatim), §13 hard rules bulleted, Open hypotheses.
- Stamps `weekly_reviews.exported_markdown_path` and bumps `updated_at`
  on successful export so the Weekly Review form can show "last
  exported" later.
- CLI: `python -m app.exports.markdown_weekly --week 2026-W21 [--output …]`.
  Exit 2 with a clear message on `CounterfactualMissingError`.

### Raw JSON exporter (`app/exports/json_exporter.py`)

- `export_database_to_json(output_path, *, redact_secrets=True,
  include_stir_pii=False)` dumps 13 MVP tables.
- Schema: `{schema_version, exported_at_utc, db_schema_migrations_applied,
  redactions, tables}`.
- Column-name redaction: `*_token`, `*_key`, `*_secret`, `*_password`,
  `*_credential` and plural variants — replaced with `[REDACTED]`.
- Nested redaction of `Authorization`, `X-API-Key`, `Cookie`,
  `set-cookie`, `Proxy-Authorization`, `X-Amz-Security-Token`, and a list
  of OAuth top-level secret keys inside `raw_api_responses.response_json`
  / `request_params_json` blobs.
- `stir_testers` and `stir_conversion_events.qualitative_feedback` are
  excluded by default per §18 rules 4-6; opt-in via `--include-stir-pii`.
- CLI: `python -m app.exports.json_exporter --output <p>
  [--include-stir-pii] [--minified]`.

### Migration 004 — `data_exports` audit table

- `data_exports(id, exported_at_utc, kind, table_name, output_path,
  row_count, include_opt_in, notes)`. `kind` CHECK in `('csv',
  'markdown_weekly', 'json')`. `include_opt_in` 0/1 nullable.
- Two indexes (`kind`, `exported_at_utc`) for the Settings page manifest.

### Settings page Exports section

- New "Exports" sub-readout in `app/pages/7_Settings.py` themed with the
  existing instrument-panel tokens — no new PALETTE keys or fonts.
- Per-table CSV: dropdown of allowlisted tables, opt-in checkbox,
  primary-styled "Export CSV" button.
- Markdown weekly: ISO-week text input (defaults to the current ISO
  week), disabled-state explanation when the counterfactual is missing,
  primary-styled "Export Markdown weekly" button.
- Raw JSON: confirmation checkbox + PII opt-in, primary-styled "Export
  raw JSON" button.
- Recent exports manifest: collapsed expander rendering the last 20 rows
  from `data_exports` as a console-log grid (when · kind · table · file
  · rows · opt-in), with a "Keep open across reruns" pin.

### Tests (`tests/test_exports.py`)

Ten tests — seven prompt-required, three defensive:

1. `test_csv_export_uses_allowlist_default_columns`
2. `test_csv_export_opt_in_includes_opt_in_columns`
3. `test_csv_export_excludes_excluded_columns_even_with_opt_in`
4. `test_markdown_weekly_requires_counterfactual` (all three blank-states
   — missing row, empty string, whitespace-only — plus the success path)
5. `test_markdown_weekly_includes_app_store_gap_label`
6. `test_json_export_redacts_secret_columns`
7. `test_csv_round_trip_preserves_data` (comma + quote-bearing rows)

Defensive guards:

- `test_every_allowlist_column_exists_in_schema` — fails fast if a future
  edit appends to `default_columns` before the column's migration lands.
- `test_opt_in_and_excluded_columns_are_disjoint` — protects against
  copy-paste mistakes between lists.
- `test_data_exports_audit_records_each_run` — every export kind writes
  the audit row with the right `(kind, table_name, include_opt_in)`
  tuple.

### Schema test updates

- `test_schema_migrations_records_each_file` and
  `test_apply_migrations_is_idempotent` now include
  `004_data_exports.sql` in the expected list.

---

## Acceptance gates satisfied

- [x] `uv run python -m app.exports.csv_exporter --table posts --output
      data/exports/posts.csv` → 20-column header matching
      `POSTS_ALLOWLIST.default_columns` byte-for-byte.
- [x] Same with `--opt-in` → header equals `default_columns +
      opt_in_columns` (currently 20 columns since opt_in is empty in
      Phase 5; the structure handles non-empty correctly per
      `test_csv_export_opt_in_includes_opt_in_columns`).
- [x] `uv run python -m app.exports.markdown_weekly --week 2026-W21`
      against a DB with no `weekly_reviews` row for that week exits 2
      with the `CounterfactualMissingError` message.
- [x] `uv run python -m app.exports.json_exporter --output
      data/exports/dump.json` produces a valid JSON file; `grep -c
      Authorization data/exports/dump.json` returns `0`.
- [x] `uv run pytest tests/test_exports.py -v` → 10/10 green in 0.09s.
- [x] `uv run pytest -q` → 137/137 green.
- [x] `uv run ruff check` clean.
- [x] Settings page renders the Exports section with all three export
      kinds; AppTest harness via `test_each_page_renders_without_exception`
      confirms no exceptions on render.

---

## Sample output

Generated against a populated `weekly_reviews` row for 2026-W21
(63-line Markdown, well under the 1,000-line "permalink instead" guard):

```markdown
# X Growth Weekly Review — 2026-W21 (2026-05-18 → 2026-05-24)

## 1. Summary

- Followers · start `61` → end `73` (Δ `+12`)
- Posts shipped: `7`
- Replies shipped: `30`
- Reply sessions completed: `5`
- Daily reps days completed: `6 / 7`

## 4. Stir funnel — App-Store-attribution-gap visible

**Distribution signal (X-side)**
- X impressions (estimate): `0`
- getstir.app visits (UTM-attributed): `0`

*App Store attribution gap (§14.5):* UTM tagging works fine for
getstir.app visits but does NOT survive the jump to the App Store.
Everything below is self-reported by testers, not auto-attributed.

**Validation signal (Stir-side, self-reported)**
- Downloads: `0` (self-reported source)
- Working-parent / home-cook testers (self-reported): `0`

## 8. Counterfactual — what this tool could not measure

Working-parent cohort discovered Stir via Reddit threads two weeks ago,
so this week's growth may not be from X at all.

## 9. What we know / what we don't know (§13 hard rules)

- Follower count is a *stock*; posts/replies/downloads are *flow*. …
- App Store downloads are NEVER auto-attributed to a specific X post or
  reply — the UTM chain doesn't survive the App Store jump. (§14.5)
```

---

## Known limitations / future work

- **CSV import is V1.5+.** This phase ships export-only; the reverse path
  is a separate future feature.
- **Selective row export (e.g., "only posts from last 7 days")** is
  future work.
- **Export scheduling** is intentionally absent — Phase 4's launchd
  recipes cover backups; exports stay manual-trigger per spec §17.
- **Encrypted exports** deferred to V1.1+ per §18.
- **Phase 5.5 placeholders** are comments-only. When the publish-flow
  migration lands, the corresponding allowlist edits are a single-line
  insertion at the marked sites in `app/exports/allowlists.py`.

---

## Phase boundary

Commits on `main` for Phase 5 (in order of the phase prompt's work-order):

1. `feat(exports): per-table CSV allowlist module`
2. `feat(exports): CSV exporter`
3. `feat(exports): raw JSON exporter with secret redaction`
4. `feat(exports): markdown weekly report with counterfactual gating`
5. `feat(migrations): data_exports audit table (004)`
6. `feat(settings): exports section`
7. `test(exports): allowlist, redaction, counterfactual, round-trip`

---

## Next phase

Run `phase-5.5-growth-agent.md` — adds Anthropic-powered draft/reply
flow, the publish_confirmation_tokens table, and the X API OAuth shim.
Phase 5.5 must extend `app/exports/allowlists.py::POSTS_ALLOWLIST` at
the marked insertion sites; the contract is documented in
`docs/ARCHITECTURE.md` "Export allowlist contract".
