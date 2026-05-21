# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 1 — Core database                      |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-2-manual-workflows.md`          |

---

## Completed in this phase

- `migrations/001_initial.sql` — every §10 Phase 1 table: `settings`,
  `account_snapshots`, `account_snapshot_corrections`, `raw_api_responses`,
  `posts`, `post_metric_snapshots`, `post_classifications`, `daily_activity`,
  `reply_sessions`, `stir_conversion_events`, `stir_testers`, `milestones`,
  `weekly_reviews`, `experiments`. FKs declared with explicit `ON DELETE`
  behavior; CHECK constraints on every enum-valued column (including the
  §10.2 privacy CHECK on `stir_conversion_events.is_likely_icp`).
- `migrations/002_views.sql` — all five §11 views: `v_account_daily`,
  `v_post_latest_metrics`, `v_daily_reps`, `v_funnel_daily`,
  `v_lane_performance`. The lane-performance view uses a user-defined
  `percentile(value, p)` aggregate for medians and IQR.
- `app/db.py` — single chokepoint for DB connections:
  - `connect(path)` sets `PRAGMA foreign_keys = ON` + `PRAGMA journal_mode = WAL`
    and registers the `percentile` aggregate.
  - `apply_migrations(conn)` runs every `migrations/*.sql` in lex order
    idempotently, tracked in `schema_migrations`.
  - `get_st_connection()` is included for forward compatibility with later
    phases but is not exercised in Phase 1.
- `scripts/init_db.py` — `uv run python -m scripts.init_db [--db-path …]`
  orchestrator (migrations + seed pipeline).
- `scripts/seed_settings.py` — 23 documented `settings` rows.
- `scripts/seed_taxonomy.py` — no-op stub (v1 taxonomy is text-typed per
  §10.2; UI dropdowns own the values).
- `scripts/seed_milestones.py` — 17 milestones total (6 distribution + 6
  validation + 3 content + 2 reps).
- `tests/test_schema.py` — schema sanity, FK enforcement is load-bearing
  (asserted by comparing FK-on vs FK-off behavior), CHECK constraint
  spot-checks (including the `is_likely_icp` privacy CHECK), seed assertions,
  idempotency.
- `tests/test_views.py` — `v_lane_performance` confidence-label boundary
  sweep at sample sizes 4, 5, 14, 15, 29, 30 (the load-bearing Phase 1 test
  per the phase prompt); percentile/IQR sanity; classification
  multi-row handling; `v_daily_reps`, `v_funnel_daily`,
  `v_post_latest_metrics`, `v_account_daily` correctness.

---

## Spec ambiguity flagged

- **`v_lane_performance` confidence-label ordering bug in §11.** The
  Python-style pseudocode lists the moderate branch (`post_count >= 15 AND
  days_covered >= 7`) BEFORE the stronger branch (`post_count >= 30 AND
  days_covered >= 14`), which makes `'stronger'` unreachable in a strict
  top-down evaluation. The phase prompt's boundary tests (30/14 → stronger)
  and §13's "insufficient → directional → confident" framing both make the
  intent clear. The view evaluates the stronger branch first; spec §11
  should be updated to match in a follow-up doc-only PR.

---

## Known limitations

- **No UI.** Phase 1 builds the storage layer; the nine §19 views land in
  Phase 3. `app/pages/` is still empty.
- **No forms / no agent / no `reply_targets`.** Those land in Phase 2,
  Phase 5.5, and Phase 5.6 respectively.
- **`v_account_daily` correction layering is partial.** Only
  `followers_count` corrections are layered onto the canonical row; other
  field corrections are stored but not surfaced through the view (Phase 3
  surfaces them per-field in the UI).
- **`v_account_daily` canonical-snapshot pick uses "earliest snapshot per
  day".** §11 says "closest to configured daily snapshot time"; the time
  setting is consumed by the UI in Phase 3.

---

## Acceptance gates satisfied

- [x] `uv run python -m scripts.init_db --db-path ./data/dashboard.db`
      succeeds from a clean state; `data/dashboard.db` exists.
- [x] `uv run pytest -v` shows 37/37 green.
- [x] `v_lane_performance` confidence-label boundary tests at sample sizes
      4 / 5 / 14 / 15 / 29 / 30 all pass.
- [x] FK enforcement test confirms `PRAGMA foreign_keys = ON` is doing real
      work (insert that fails with FK-on succeeds with FK-off).
- [x] `settings` has every documented row (23 keys).
- [x] `milestones` has 17 rows split 6 distribution / 6 validation / 3
      content / 2 reps.
- [x] Phase commits land per logical unit (see `git log`).
- [x] `docs/IMPLEMENTATION_STATUS.md` updated; next phase queued.

---

## Next phase

Run `phase-2-manual-workflows.md` — implements the manual entry workflows
(account snapshot, post/reply logging, classification, daily reps, Stir
conversion events, correction form, "needs tagging" and "needs post ID"
queues) on top of the Phase 1 schema.
