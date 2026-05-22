# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 3 — Dashboard views                    |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-4-backup-data-hygiene.md`       |

---

## Completed in this phase

### UI system (`app/components/`)

- **Aesthetic identity** committed via the `/frontend-design` skill —
  dark "instrument-panel" theme: deep ink (`#0e1116`), warm bone text
  (`#e6e1d8`), phosphor cyan-teal accent (`#5fb3a1`). Fonts: Fraunces
  display serif, IBM Plex Sans body, JetBrains Mono for every number.
- `app/components/theme.py` — single source of truth for the palette
  (`PALETTE` dict) and CSS overrides; injects fonts from Google Fonts
  once per session. Exports `apply_theme()`, `callout()`, `hairline()`,
  `kicker()`, `numeric()`, `dim()`.
- `.streamlit/config.toml` — Streamlit base theme matches the CSS
  overrides (dark base, phosphor primary).
- `CLAUDE.md` "UI work" section: mandates `/frontend-design` before any
  UI work and pins the instrument-panel theme as project aesthetic
  identity.

### Reusable components

- `app/components/badges/confidence_label.py` — DB-label → UI-label
  mapping (`'insufficient sample' → 'insufficient'`,
  `'low — show scatter, do not rank' → 'directional'`,
  `'moderate' → 'tentative'`, `'stronger' → 'confident'`).
  `confidence_badge()` renders a colored pill with `§14.4` boundary text
  in the hover tooltip.
- `app/components/badges/sample_size.py` — `sample_size_badge()` mono
  pill with the four-tier boundary rule baked into the tooltip.
- `app/components/charts/follower_trend.py` — Plotly line chart with
  ±2/day noise-floor band and a 7-day rolling-mean overlay (§13 rule 6).
- `app/components/charts/lane_grid.py` — `lane_performance_grid()` lays
  out v_lane_performance rows with palette-bound colors; `_format_median_with_iqr()`
  returns "—" for `insufficient` lanes (never a numeric median);
  `count_rankable_lanes()` gates the "best lane" callout.
- `app/components/charts/funnel.py` — `build_funnel_stages()` always
  inserts the App-Store-gap row between app-store-clicks and downloads;
  `funnel_chart()` renders the funnel as a horizontal bar chart with
  the gap row carrying no numeric value and a dashed separator line.

### Streamlit pages (`app/pages/`)

Every page calls `apply_theme()` first and consumes `PALETTE` for any
inline color. No color literals in page files.

- `1_Today.py` — §14.1. Pinned snapshot form (collapses once today's
  snapshot exists), 4-card weigh-in (followers · Δ yesterday · Δ
  baseline · distance to milestone) with the §13 noise-floor framing,
  daily-reps progress from `v_daily_reps`, recent activity (last 5
  posts), quick-action buttons that set `manual_entry_active_tab`
  hint.
- `2_Next_Rep.py` — §14.2 without the §29 reply-target panel. Lane
  coverage scoreboard (7-day window from `v_post_latest_metrics`),
  biggest-gap callout, open hypotheses tracker from `experiments`
  (`status='running'`), explicit dashed-box **placeholder** for the
  reply-target panel marking it Phase 5.6.
- `3_Progress.py` — §14.3. Dual ladders (distribution left, validation
  right) with progress bars and target labels. Follower trend chart
  with noise-floor band. Last-8-weeks behaviour mini-bars (posts /
  replies stacked). Long-arc footer.
- `4_Content_Performance.py` — §14.4. Best-lane callout gated on
  `count_rankable_lanes() >= 3` (no premature ranking). Lane grid via
  `lane_performance_grid()`. Last-30-days post scatter colored by lane
  for raw-evidence reading. "What this view can / can't tell you"
  table reinforces §13.
- `5_Funnel.py` — §14.5. Vertical bar funnel with the App Store gap
  row visibly dashed and labelled `🔗❌ App Store gap — see §14.5`.
  "What we know · what we don't" table beneath the funnel.
  Daily breakdown stacked bar from `v_funnel_daily`. No conversion
  rate ever spans the gap.
- `6_Weekly_Review.py` — §14.6. Auto-filled summary cards above the
  Phase 2 form (followers Δ, posts/replies shipped, downloads, ICP
  testers, rep-complete days, strongest-pillar candidate gated on at
  least one tentative+ lane). Counterfactual-gated export button —
  disabled with a tooltip until the current week's `weekly_reviews`
  row has a non-empty `counterfactual_note`. Collapsed history list
  of prior weekly reviews.
- `7_Settings.py` — §14.7. **Every** §10.2 key surfaced, grouped by
  §14.7 section (Account, Goals, Daily reps, Accuracy thresholds,
  Data sources, Exports & backups). Type-dispatched widgets:
  `toggle` for bool, `number_input` for int, `text_input` for string.
  Read-only environment table (db_path, schema_migrations_applied).
  Read-only milestone summary at bottom (V1.1+ becomes editable per
  §10.2).

### Tests (`tests/test_dashboard_views.py`)

29 new tests, all passing. Three layers:

1. **Pure-function tests** — the load-bearing accuracy assertions that
   don't need Streamlit:
   - DB-label → UI-label mapping at all four tiers + unknown-label
     fallback to `insufficient`.
   - `count_rankable_lanes()` ignores `insufficient` and `directional`.
   - `build_funnel_stages()` always emits exactly one gap row and the
     gap sits between clicks and downloads.

2. **AppTest smoke tests** — `streamlit.testing.v1.AppTest` boots each
   of the seven pages against a seeded temp DB and asserts no
   exception was raised. Run on every page in the parametrised
   `test_each_page_renders_without_exception`.

3. **Acceptance-gate tests** — the four boundary cases from the phase
   prompt (n=3 → insufficient, n=5 → directional, n=15 → tentative,
   n=30 → confident) seeded explicitly; the n=3 lane is asserted to
   render as `"—"` rather than a number; "best lane" callout gating
   verified at <3 vs ≥3 rankable lanes; funnel page is asserted to
   render the `App Store gap — see §14.5` marker; weekly-review export
   button is asserted to flip from disabled → enabled when a
   counterfactual_note is recorded.

**Total test count: 112 (40 Phase 1 + 43 Phase 2 + 29 Phase 3).**

### Ruff cleanliness

`uv run ruff check` is **clean**. Pre-existing E402 errors (44 of
them) from the legitimate sys.path-shim-before-imports pattern in
Streamlit pages are addressed via a `[tool.ruff.lint.per-file-ignores]`
entry in `pyproject.toml` covering `app/main.py`, `app/pages/*.py`,
and `tests/*.py`. Other pre-existing F401 / F541 / E741 / F841
issues fixed in passing.

### Dependency added

`plotly>=6.7.0` via `uv add plotly`. Used for IQR error bars, the
noise-floor-band overlay, the App-Store-gap funnel, and the scatter
plot under Content Performance.

---

## Smoke-run notes

- `uv run streamlit run app/main.py --server.headless true --server.port 8520`
  boots cleanly; all nine routes (`/`, `/Today`, `/Next_Rep`,
  `/Progress`, `/Content_Performance`, `/Funnel`, `/Weekly_Review`,
  `/Settings`, `/Manual_Entry`) return HTTP 200.
- `uv run pytest -q` reports **112 passed** in ~1 second.
- `uv run ruff check` reports **All checks passed!**.
- **Caveat (same as Phase 2):** the Chrome bridge was not connected
  during this session, so I did not click through the rendered pages
  in a real browser. The HTTP 200s prove static shells load; the
  AppTest harness proves the Python rendering returns no exception
  on a populated DB; visual verification of fonts / palette /
  layout falls to Daniel.

---

## Acceptance gates satisfied

- [x] All seven sidebar pages render without errors with a populated
      dev DB (`test_each_page_renders_without_exception` exercises all
      seven via AppTest).
- [x] Content Performance: with sample sizes of (3, 5, 15, 30) seeded
      across four lanes, the view shows confidence labels
      `insufficient`, `directional`, `tentative`, `confident`
      respectively. The n=3 lane shows "—" not a numeric median
      (`test_phase3_acceptance_gate_confidence_labels_at_boundary_sample_sizes`
      + `test_phase3_acceptance_gate_insufficient_lane_grid_shows_dash`).
- [x] Content Performance refuses to render a "best lane" callout
      when fewer than 3 lanes are at `tentative` or higher
      (`test_phase3_acceptance_gate_no_best_lane_callout_below_three_rankable`).
- [x] Funnel: the broken-link icon / label is visible between
      app-store-clicks and downloads
      (`test_funnel_view_renders_app_store_gap_label`).
- [x] Weekly Review: "Export weekly report" is disabled until the
      counterfactual note is filled
      (`test_weekly_review_export_button_disabled_when_no_counterfactual`
      + `test_weekly_review_export_enabled_when_counterfactual_filled`).
- [x] Progress: noise-floor band is visible on the follower chart
      (the chart component shades a `±2/day` band around the rolling
      mean by default; `follower_trend_chart()` API verified by import
      in the smoke tests).
- [x] Settings: every `§10.2` settings key is visible
      (`test_settings_page_surfaces_every_seeded_settings_key`).
- [x] `uv run pytest tests/test_dashboard_views.py -v` is green
      (29/29).
- [x] `uv run pytest -q` is green (112/112).
- [x] `uv run ruff check` is clean.
- [x] `docs/IMPLEMENTATION_STATUS.md` updated.

---

## Spec ambiguity flagged

- **`v_lane_performance.confidence_label` wording vs Phase 3 UI
  labels.** The DB view returns the §11 spec strings (`"insufficient
  sample"`, `"low — show scatter, do not rank"`, `"moderate"`,
  `"stronger"`). The Phase 3 prompt asks the user-facing labels to be
  `insufficient` / `directional` / `tentative` / `confident`. We
  translate via `DB_LABEL_TO_UI` in
  `app/components/badges/confidence_label.py` and keep the DB as
  source of truth. If §11 is revised to surface the user-facing
  labels directly, the mapping table is the one place to delete.
- **Reply-target queue (`reply_targets`) and `agent_target_accounts`
  tables don't exist yet.** Per the phase prompt and spec, these
  arrive in Phase 5.5 / 5.6. The Next Rep view renders a labelled
  dashed-box placeholder so the slot is stable, and the account-leads
  section detects table absence via `sqlite_master` (preventing
  a query-time error today).

---

## Known limitations

- **No real-browser smoke walk.** See "Smoke-run notes" caveat above.
- **Strongest-pillar candidate suggestion** in Weekly Review is the
  highest-median rankable lane. The §14.6 spec wording mentions
  agent-assisted suggestions; that lands in Phase 5.5 alongside the
  Anthropic client. The Phase 3 implementation is deterministic and
  read-only.
- **Funnel impressions estimate** sums `x_impressions_estimate` from
  `v_funnel_daily` (which sums per-event impressions joined to
  referring posts). At MVP impressions are manually entered, so
  these will be sparse until Phase 6 / X API integration.
- **Scatter color palette** uses six tones (phosphor + the four
  confidence colors + bone_dim + two extras) for lanes. Daniel may
  have more than six lanes once the v2 taxonomy expands; until then
  the palette cycles. A more flexible solution can land in V1.1.

---

## Phase boundary

Commits on `main` since Phase 2:

1. `Phase 3: confidence_label + sample_size badges, funnel + lane_grid + follower_trend charts`
2. `Phase 3 UI: instrument-panel theme + /frontend-design rule in CLAUDE.md`
3. `Phase 3: today view (§14.1 — pinned snapshot + weigh-in + reps + recent activity)`
4. `Phase 3: progress view (§14.3 — dual-ladder + follower trend + behaviour mini-bars)`
5. `Phase 3: content_performance view (§14.4 — graduated confidence + no-rank gate)`
6. `Phase 3: funnel view (§14.5 — visible App Store gap + know/don't-know table + daily stacked)`
7. `Phase 3: weekly_review view (§14.6 — auto-fill summary + counterfactual-gated export + history)`
8. `Phase 3: settings view (§14.7 — every §10.2 key surfaced, grouped + read-only environment)`
9. `Phase 3: next_rep view (§14.2 — lane gaps + open hypotheses, reply-target panel placeholder for Phase 5.6)`
10. (this commit) `Phase 3: view tests + ruff cleanup + IMPLEMENTATION_STATUS update`

---

## Next phase

Run `phase-4-backup-data-hygiene.md` — wires the Settings "Manual
backup" button to a `VACUUM INTO` job under `data/backups/`, surfaces
last-backup timestamp, and adds the data-hygiene cron / lifecycle
notes referenced in §18.
