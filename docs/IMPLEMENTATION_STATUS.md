# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 2 — Manual workflows                   |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-3-dashboard-views.md`           |

---

## Completed in this phase

### Form layer (`app/forms/`)

Each module exposes a pure `submit_*(conn, payload) -> int` function (DB-only,
fully unit-tested) plus a `render(conn, *, key_prefix=...)` Streamlit fragment.

- `__init__.py` — `FormError`, `today_iso`, `now_utc_iso`,
  `get_setting` / `set_setting` helpers.
- `snapshot.py` — pinned snapshot form (§15.1); refuses duplicate
  (username, snapshot_date) per §22.
- `correction.py` — append-only `account_snapshot_corrections` writer;
  never mutates `account_snapshots` (§13 hard rule 2).
- `post_log.py` — `post`/`reply`/`quote` logging (§15.2). UI type → schema
  type mapping (`post → standalone`). Derives `manual_confirmation_status`
  from `x_post_id` presence (`confirmed` vs `needs_id`, §22).
  `add_post_id(...)` helper backfills `x_post_id` and flips to `confirmed`.
- `classify.py` — v1 taxonomy (§15.3). Refuses overwrite without explicit
  `allow_overwrite=True`.
- `daily_reps.py` — upserts `daily_activity` keyed on `activity_date`;
  derives `minimum_reps_completed` from seeded targets.
- `stir_event.py` — §15.4. Enforces §13 hard rule 11 (is_likely_icp only
  valid with `attribution_method='self_reported'`) in form-layer validation
  *before* hitting the schema CHECK.
- `stir_tester.py` — tester registration; rejects ICP attributes unless
  explicit `self_reported_icp=True` flag is set.
- `weekly_review.py` — upserts `weekly_reviews`. Blocks submit when
  `counterfactual_note` is empty AND `counterfactual_required` setting
  is true (§14.6 / §22).
- `queues.py` — `needs_tagging(conn)` and `needs_post_id(conn)` queries +
  Streamlit render fragments with inline action buttons.

### Streamlit pages (`app/pages/`)

- `8_Manual_Entry.py` — Phase 2 hub. 9-tab interface: Snapshot, Correction,
  Post/Reply, Classify, Daily reps, Stir event, Tester, Needs tagging,
  Needs post ID.
- `7_Settings.py` — surfaces only Phase 2 settings keys (daily targets,
  sample-size thresholds, velocity threshold, calibration date,
  counterfactual toggle, backup/export paths). Agent + X-API rows are
  deliberately not surfaced until their backing phases land.
- `6_Weekly_Review.py` — hosts `weekly_review.render(conn)`. Phase 3 will
  auto-fill the quantitative summary fields from `v_account_daily` etc.
- `1_Today.py` / `2_Next_Rep.py` / `3_Progress.py` /
  `4_Content_Performance.py` / `5_Funnel.py` — stubs surfacing `st.info`
  pointing at the phase that fills them.

### App scaffolding

- `app/main.py` — bootstraps DB migrations once per session via
  `st.session_state["db_initialized"]`, sets the page title. Adds the
  project root to `sys.path` so `from app.* import ...` resolves when
  Streamlit's auto-discovery launches each page.
- `app/pages/__init__.py` — `open_connection()` helper used by every page.

### Tests

- `tests/test_forms_validation.py` (20 tests) — per-form rejection paths:
  bad enums, negative counts, missing required fields, duplicate snapshot
  date, ICP-without-self-report, empty counterfactual note, etc.
- `tests/test_forms_persistence.py` (15 tests) — happy paths: rows land in
  the right tables with the right defaults; `posts_shipped` upsert
  behavior; `add_post_id` flips `manual_confirmation_status`; queues
  return the right ids.
- `tests/test_corrections.py` (4 tests) — append-only correction trail;
  original `account_snapshots` row is never mutated; correction count
  grows with each call; null `old_value` is recorded as empty string.

**Total test count: 81 (40 Phase 1 + 41 Phase 2).**

---

## Smoke-run notes

End-to-end exercise of the morning ritual via Python walked:
snapshot → post → reply-with-no-id → classify → daily reps →
stir event with self-reported ICP → tester → correction → weekly
review with counterfactual. After the walk:

- `account_snapshots[id=1].followers_count` was still 120 even after a
  correction (which lived in `account_snapshot_corrections` alone).
- `needs_tagging` returned exactly 1 row (the unclassified reply).
- `needs_post_id` returned exactly 1 row (the same reply, which had no
  `x_post_id`).
- `daily_activity.minimum_reps_completed` was set to 1 because
  posts/replies/sessions met the seeded targets.

Streamlit boot test:

- `uv run streamlit run app/main.py --server.headless true` boots cleanly;
  every page (Today, Next_Rep, Progress, Content_Performance, Funnel,
  Weekly_Review, Settings, Manual_Entry) returned HTTP 200 from the home
  shell.
- Each page module imported cleanly under bare-mode Python (no
  `ScriptRunContext`-fatal errors).
- **Caveat:** the Chrome bridge for this session was not connected, so I
  did not click through the forms in a real browser. The HTTP 200s prove
  the Streamlit static shell loads; the bare-mode imports prove the
  Python modules don't crash; the end-to-end DB exercise proves the
  submit functions write the expected rows. A real browser walkthrough
  of the Manual Entry tabs is recommended before relying on the UI for
  the actual morning ritual.

---

## Spec ambiguity flagged

- **Stir event source vs source_data_quality.** §15.4 lists `source` as
  free text and a separate `attribution_method` field; the schema also has
  `source_data_quality` (NOT NULL) which the spec describes implicitly
  ("manual" forms imply `manual` quality). The form sets it to `manual` by
  default and lets the user change it — fine for now, but the spec should
  spell out the relationship between `source` (free text) /
  `attribution_method` (enum) / `source_data_quality` (data-quality enum)
  more explicitly. The three columns capture different concerns; clarifying
  in §10.2 would prevent future confusion.
- **Post type taxonomy.** Spec §15.2 colloquially says "post / reply", but
  the schema enum is `standalone | reply | quote | thread_root | thread_child`.
  The form exposes `post | reply | quote` and maps `post → standalone`.
  Spec should clarify in §15.2 whether `thread_root` / `thread_child` are
  ever user-entered manually (we suspect not — they land via API in
  Phase 5+).
- **Daily reps targets.** Spec §14.1 mentions distinct post / reply / reply-
  session targets. There is no `daily_quote_target` setting, so
  `planned_quotes` is always 0 on insert. That matches the absence of a
  documented quote-target in §10.2 / §14.7, but if quote reps ever become
  a first-class lane, the form will need wiring.

---

## Known limitations

- **No real-browser smoke walk.** See "Smoke-run notes" caveat above.
- **No analytical views.** The Today / Next Rep / Progress /
  Content Performance / Funnel pages are stubs; Phase 3 fills them.
- **No CSV/Markdown export.** Phase 5.
- **No reply-target queue.** Phase 5.6.
- **No agent integration.** Phase 5.5.
- **Classification is single-row, not versioned.** Spec §10.2 leaves
  versioned classifications for V1.1; the overwrite-with-flag flow in
  `classify.submit_classification` does an UPDATE in place. Prior
  classification values are not preserved.
- **Settings page only surfaces Phase 2 keys.** Other seeded keys
  (`x_handle`, `profile_url`, etc.) are referenced internally but not
  editable from the UI yet.

---

## Acceptance gates satisfied

- [x] `uv run streamlit run app/main.py` launches; Manual Entry page is
      reachable; every page returns HTTP 200 and imports cleanly.
- [x] Each form submits correctly and writes the expected rows (validated
      by `pytest`).
- [x] "Needs tagging" queue lists exactly the posts without classifications.
- [x] "Needs post ID" queue lists exactly the posts with `x_post_id IS NULL`.
- [x] A correction creates a new `account_snapshot_corrections` row; the
      original `account_snapshots` row is untouched.
- [x] Weekly review submit is blocked when counterfactual is empty (and the
      setting flag is true).
- [x] Stir event form refuses to auto-attribute downloads; manual
      `referring_post_id` link is allowed but optional. ICP attribute is
      only stored when `attribution_method='self_reported'`.
- [x] `docs/IMPLEMENTATION_STATUS.md` updated; next phase queued.
- [x] `uv run pytest -q` shows 81/81 green.

---

## Next phase

Run `phase-3-dashboard-views.md` — fills the five stub pages (Today,
Next Rep, Progress, Content Performance, Funnel) by consuming the
Phase 1 views; surfaces context-aware launchers that hand off into the
Phase 2 forms; adds the auto-filled quantitative summary to the weekly
review page.
