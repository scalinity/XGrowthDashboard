# X Growth Dashboard

Local-first "weight-loss dashboard for X growth." Single-user personal tool. Not distributed.

See `spec.md` for the full product spec and `docs/IMPLEMENTATION_STATUS.md` for current phase.

## Run

```bash
uv sync
uv run streamlit run app/main.py
```

Then open <http://localhost:8501>.

## Test

```bash
uv run pytest -q
```

## Stack

- Python ≥ 3.11, managed by [`uv`](https://docs.astral.sh/uv/).
- [Streamlit](https://streamlit.io/) for the local web UI.
- [SQLite](https://sqlite.org/) for storage (added in Phase 1).
- [Anthropic SDK](https://docs.anthropic.com/) for the Growth Agent (wired in Phase 5.5).
- [Voyage AI](https://docs.voyageai.com/) embeddings for the repetition guard (Phase 5.8). OpenAI `text-embedding-3-small` is the documented alternative.

## Drafting Intelligence Pack (Phase 5.8)

Five informational layers stacked on top of the Growth Agent (`spec.md` §28.11–§28.15). All five are additive and never gate Publish; they just make the agent's reasoning legible.

- **Pre-publish scorer** — deterministic 9-dimension chip (`weak | viable | strong`) above every draft.
- **Generated voice profile** — Haiku synthesis of how Daniel actually writes; spliced into the system prompt alongside hand-picked voice samples.
- **Repetition guard** — embedding-cosine scan of new drafts against shipped posts; yellow banner on near-duplicates.
- **Confidence labels** — `<confidence>` tags on every analytical claim, persisted on `agent_drafts.confidence_label` / `agent_messages.confidence_label`. Untagged claims drop humility by one (rule #13).
- **Approval payload hash UX** — modal banner when Daniel edits the draft after opening; click-handler invalidates prior tokens before minting.

### One-time setup

1. Add `VOYAGE_API_KEY` to `.env` (see `.env.example`). Without it the repetition guard returns NULL at save time and drafts proceed — but the banner never fires.
2. Run the resumable backfill once you have shipped posts to embed: `uv run python scripts/embed_posts.py`.
3. From the running app: **Settings → Growth Agent → Voice profile → "Regenerate from posts"**. Requires ≥10 shipped posts in the lookback window (default 90 days).

## Niche & Content-Type Calibration Pack (Phase 5.9)

Six additive features that give the Growth Agent two new identity anchors and three new lenses (`spec.md` §28.16–§28.21). None changes existing contracts; all stack on top of IWH (§28.2 #13), dark-pattern lint (§28.2 #12), and the repetition guard (§28.13).

- **Structured niche definition (§28.16, rule #15)** — two settings rows (`niche_problem`, `niche_person`) spliced into Section 1 of the system prompt as the load-bearing line *"You help {niche_person} solve {niche_problem}."* When either field is empty, the orchestrator **refuses every `save_draft_*` call** in `app/agent/session.py::niche_gate` — a prompt-injected "skip the niche check" cannot bypass it. The Settings panel also exposes a read-only "Test against bio" Haiku critique.
- **V/G/P/P content type axis (§28.17)** — every post and agent draft carries `content_type` ∈ `{value, growth, personality, proof}`, orthogonal to pillar (topic). The orchestrator rejects `'unspecified'` on agent drafts. A new `v_content_type_performance` view slices outcomes by purpose with the same graduated-confidence ladder as `v_lane_performance`; the **Today** page surfaces an under-represented-type recommendation; **Content Performance** gets a dedicated V/G/P/P table.
- **Reply-quality lint (§28.18)** — second small-model lint pass on every reply draft, gated by `reply_quality_lint_enabled`. Catches forced / AI-tasting / selfishly self-promoting replies. Failure counts as a failed IWH revision (same enforcement path as the dark-pattern lint).
- **Follower-velocity projection (§28.19)** — `v_follower_velocity` view + `get_velocity_projection` tool + the **Progress** velocity panel with a date-target widget. **All projection columns suppress to NULL when `|delta_7d| < velocity_projection_noise_floor_followers`** (default 10) — never display a precise date when the input is noise.
- **Replier-pool candidate discovery (§28.20)** — third reply-target discovery path. Paste a thread URL + replier handles/excerpts into the **Reply Target Queue**; each replier is scored against the §29.3 4-dim model plus a new `thread_context_fit_score` measuring overlap with `niche_person`. Rows land with `source = 'replier_under_thread'`.
- **Personality lore registry (§28.21)** — Daniel-curated `personality_lore` table of recurring jokes and motifs spliced into Section 5 of the system prompt after voice samples. **The agent has no write access** (startup invariant scans `AGENT_TOOLS` for any reference to the table). When a `content_type='personality'` draft saves, the orchestrator fuzzy-scans the text against active lore and increments `invocation_count`; an "over-relied on" banner fires when count exceeds the threshold *and* the bit was used recently.

### One-time setup

Open **Settings → Growth Agent → Niche definition** and fill in both fields — the agent is in "low-power mode" (drafting refused) until both are saved. Optionally seed a few rows in the **Personality lore** panel and adjust thresholds in `settings` if the defaults don't fit.

## Strategic Analysis Pack (Phase 5.10)

Four CreatorOS-derived workflows ported into XGrowth's discipline (`spec.md` §28.22–§28.25). Each closes a workflow gap that previously sent Daniel out to another tool. Brings the total MVP view count from 9 to 11.

- **Brain Dump (§14.9 + §28.22)** — *capture-first* surface, distinct from §14.8 Agent Chat. Daniel pastes raw thinking; the agent processes it into clarifying questions + ≤5 structured candidate drafts. `raw_text` is **immutable after insert** — refinement creates a new dump, never edits an old one. Promotion to `agent_drafts` is an explicit per-candidate click that runs the full Phase 5.8 pipeline downstream (IWH preflight, dark-pattern lint, content-type validation, pre-publish scorer, repetition guard). Lives at `app/pages/11_Brain_Dump.py`.
- **Coach (§14.10 + §28.23)** — second conversational surface with **citation-allowlist discipline**. Every analytical claim must be grounded in a real DB row via inline `〔record_type id_or_filter〕` citations; invalid citations are stripped with a strip-count surfaced under the message. When `coach_refuse_without_evidence = true` (default), uncited analytical messages are replaced with a canonical refusal — *cite or refuse, no speculation as advice*. The Coach is advice-only: its tool registry excludes every write tool, enforced by a boot-time invariant in `app/main.py`. Lives at `app/pages/12_Coach.py`.
- **Account Researcher (§28.24)** — strategic analysis of a target X account: posting patterns, positioning, reply-strategy entry points, niche alignment (0-3 overlap score). Manual-paste workflow for MVP (V1.1+ adds X API auto-pull). Schema permits multiple reports per handle as a point-in-time snapshot; consecutive reports for the same handle render as a side-by-side compare-to-previous diff. Bidirectional link to `reply_targets` via "Generate reply target from this research." Lives at `app/pages/13_Account_Researcher.py`.
- **Profile Audit (§28.25)** — periodic comprehensive review of bio + pinned post + recent posts + active voice profile + niche definition, read as a *unified surface*. Returns a load-bearing `top_three_actions` field — the audit is only useful if it produces concrete next steps. Append-only history; cadence reminder banner at 90 days; **never auto-runs**. Panel lives in **Settings → Growth Agent → Profile audit**.

### One-time setup (Phase 5.10 extras)

The four workflows ship enabled out of the box. Two settings worth knowing:

- `coach_refuse_without_evidence` (default `true`) controls the Coach refusal gate. Toggle from the Coach view header or **Settings → Growth Agent → Profile audit**.
- `profile_audit_cadence_reminder_days` (default `90`) sets when the Settings panel's yellow "fresh audit" reminder fires. Audits never auto-run regardless.

## Growth Layer + Quality-of-Life Pack (Phase 5.11)

Five features that close the remaining consolidation gap from CreatorOS (`spec.md` §28.26–§28.30). Three new top-level views (§14.11 Content Calendar, §14.12 Campaigns, §14.13 Inspiration Library), one extension to §14.6 Weekly Review (Weekly / Monthly cadence toggle), six new tables, one new computed view (`v_campaign_progress`). After Phase 5.11 the only remaining CreatorOS capability is blogs — deferred to Phase 6 with an explicit scope rewrite.

- **Campaigns (§14.12 + §28.26)** — multi-week themed pushes. Each campaign carries a hypothesis + date range + items + **dual-stream success criteria** (≥1 distribution metric AND ≥1 validation metric, enforced at the application layer; single-stream campaigns are rejected). State machine: `planning → active → completed | abandoned`. Completion requires per-criterion actuals + a lesson + a counterfactual_note — same epistemic discipline as weekly reviews. **No auto-completion on `end_date` pass** — the view shows "ended N days ago, complete now or extend?" and waits for Daniel. Agent tool `#21 analyze_campaign_progress` is read-only; the agent never writes campaign state. Lives at `app/pages/15_Campaigns.py`.
- **Monthly AI reviews (§14.6 + §28.27)** — cadence companion to weekly. New Weekly / Monthly radio at the top of §14.6 switches between `weekly_reviews` and `monthly_reviews` writes while sharing the page shell. Auto-fill adds `strongest_content_type` / `weakest_content_type` from the V/G/P/P axis (§28.17) and `campaigns_completed_json` (campaigns whose `completed_at_utc` falls in the iso_month window). Same export-blocker rules as weekly — `counterfactual_note` required, `confidence_label = 'speculation'` blocks export until acknowledged. Agent tool `#22 draft_monthly_review_section` mirrors the weekly tool plus a new `campaigns_retro` section.
- **Content Calendar (§14.11 + §28.28)** — visual planning grid that aggregates four provenances into AM/PM cells: shipped posts, future-dated manual drafts, agent drafts with no linked post, and planned campaign items. AM/PM cutoff configurable via `calendar_am_cutoff_hour` (default noon). Week / two-weeks / month toggle. Filters compose with AND (pillar / content_type / campaign). The "+ schedule slot" inline form is campaign-scoped (writes to `campaign_items`) OR ad-hoc (writes a draft `posts` row). **The calendar shows schedules; it does not publish** — §28.10's two-step confirmation still gates the publish moment. Lives at `app/pages/14_Content_Calendar.py`.
- **Inspiration Library + plagiarism guard (§14.13 + §28.29)** — capture-then-remix workflow for external X content. Daniel pastes posts he liked (no scraping) and runs **7 transform modes** against them: `structure`, `hook_pattern`, `counterpoint`, `original_version`, `voice_profile_version`, `expand`, `compress`. Each transform produces text + a plagiarism risk read. The load-bearing rule: `final_risk = max(ai_reported, deterministic)` on the `low < medium < high` ordering — **the AI cannot underreport**. Deterministic floor uses Jaccard token similarity + longest contiguous shared n-gram, with thresholds tunable via settings (`inspiration_plagiarism_*_threshold`). High-risk transforms **disable the "Send to drafts" button** until Daniel writes an override reason and clicks acknowledge — the override is audit-logged. Lives at `app/pages/16_Inspiration_Library.py`.
- **Comprehensive audit logs (§28.30)** — append-only canonical record of every state-changing event. Eight categories: `auth`, `x_op`, `publish`, `settings`, `export`, `data`, `admin`, `migration`. Distinct from `agent_tool_calls` (which logs every read-only call too). Write-through wired into `set_setting` (with old/new value diff), `record_export`, `backup_database`, all four `publish_post_atomic` branches, and every Phase 5.11 create/transition path. **The agent has NO read or write access to the table** — startup test asserts the AGENT_TOOLS registry contains no reference to `audit_logs`. Pruning via `scripts/prune_audit_log.py` self-audits. Settings → Audit log viewer panel with category + limit filters.

### One-time setup (Phase 5.11 extras)

Run `uv run python -m scripts.init_db` once to apply migration 015 and seed the new settings rows. After that, the views surface in the sidebar — Content Calendar at `pages/14_Content_Calendar.py`, Campaigns at `pages/15_Campaigns.py`, Inspiration Library at `pages/16_Inspiration_Library.py`. The Weekly Review page (`pages/6_Weekly_Review.py`) gains a Weekly / Monthly cadence radio at the top.

Settings worth knowing:

- `audit_log_retention_days` (default `365`) — `scripts/prune_audit_log.py` deletes rows older than this; set to `0` to disable pruning entirely.
- `inspiration_plagiarism_jaccard_high_threshold` (default `0.65`) and the three companion thresholds — tune the plagiarism guard via Settings, **never patch the constants in `app/agent/inspiration.py`**.
- `calendar_am_cutoff_hour` (default `12`) — hour below which a slot reads as AM.
- `monthly_review_auto_draft_enabled` (default `false`) — leave OFF until you want the monthly review banner to surface automatically at the start of each month.

## Long-form Blogs (Phase 6)

Closes the final consolidation gap from CreatorOS (`spec.md` §28.31–§28.34). Adds long-form blog authoring as a first-class production surface — `blogs` / `blog_versions` / `blog_exports` / `blog_to_post_links` tables, one new view (`v_blog_pipeline`), two new top-level views (§14.14 Blogs index, §14.15 Blog Editor), six new agent tools (#25–#30). **Total view count: 16. Total agent tool registry: 30.**

**Critical scope reminder — read this first:** **The app NEVER publishes blogs externally.** Phase 6 produces blogs *locally*, exports them to disk as files, and Daniel publishes externally on his blog platform by hand. There is no Substack publish API, no Ghost API, no WordPress REST integration, no Medium SDK, no RSS generation, no auto-publish on a schedule. **The single-user-local thesis of §7.1 is unchanged.** If you find yourself wanting to integrate a publishing platform, refuse and re-read §0 paragraph 5 + §7.1 + §1's Phase 6 expansion paragraph.

- **Schema + state machine (§28.31)** — `app/agent/blogs.py`. Eight-state lifecycle: `idea → outlining → drafting → editing → ready → exported → published_externally → archived`. Legal transitions enforced in `transition_status` (SQLite CHECKs can only constrain column shape; transitions live in code). Versioning is append-only with no-op detection — saves where body hash AND outline AND title AND status all match the current version skip the version row. Reverting to an older version creates **forward-moving history** (new row carrying the older body; the target row's `is_current_for_blog` is NOT flipped back) so "undo" is auditable. SEO metadata writes directly to `blogs.seo_*` columns without creating a version row (sidecar, not content).
- **Blog drafting agent tools (§28.32)** — `app/agent/blog_drafting.py` + four `config/blog_*_prompt.md` templates. **Tool #25 `outline_blog`**, **#26 `draft_blog`**, **#27 `suggest_blog_edits`**, **#28 `generate_blog_seo_metadata`**. All four read the **unified identity stack** — the same niche definition, voice profile, voice samples, and personality lore that feed X drafting — so the agent has a single coherent identity. The identity context is rendered into the *user* message rather than the system prompt, so a voice-profile regen in Settings reflects on the next agent call without any process restart. `suggest_blog_edits` returns structured per-paragraph suggestions for Accept / Reject / Modify — **NEVER auto-applies**. All four emit `<confidence>` tags per §28.14; the orchestrator parses + persists the dominant label on `blog_versions.confidence_label_at_version` so a speculation-labeled draft surfaces as a yellow chip in the editor.
- **Blog Editor view (§14.15)** — `app/pages/18_Blog_Editor.py`. Three-panel layout: outline (left, editable Markdown), body (center, editable Markdown), agent + identity readout + version history + linked posts (right). Status selector enforces legal transitions (illegal transitions impossible to select). Footer actions: Save / Discard / Export ▾ / Repurpose to X ▾. The identity readout is **live-bound** to fresh DB reads each rerun — voice-profile regen in Settings reflects on the next rerun without caching.
- **Blogs index view (§14.14)** — `app/pages/17_Blogs.py`. Status counters strip, multi-select status filter, four-key sort (last_edited / stale_longest / length_gap / pillar). Yellow keyline on rows whose `days_in_current_status` exceeds `blog_stale_status_warning_days` (default 21). Inline "+ new blog" form (no modals).
- **Blog exports (§28.33)** — `app/agent/blog_exports.py`. Four format renderers: **Markdown** (optional YAML frontmatter), **HTML** (inline minimal Markdown→HTML, no new dependency), **JSON** (body_markdown + body_html + structured metadata), **MDX** (`export const meta = {...}` frontmatter). **Atomic write-then-record contract** — file is written via tempfile + `os.replace` in the target's parent directory (same-FS atomic rename); DB row + audit log + optional `ready → exported` transition land in a single transaction. On DB-side failure the file is preserved on disk (it represents real work) and the editor surfaces a "file written but export record failed" reconciliation banner. `content_sha256` is the audit anchor for detecting later disk-side tampering. Re-export overwrites the file but inserts a new row (append-only history).
- **X ↔ blog repurposing (§28.34)** — `app/agent/blog_repurposing.py` + four prompt templates. **Tool #29 `repurpose_blog_to_x`** with three modes (`thread_from_sections`, `single_post_summary`, `teaser_with_link`) and **#30 `repurpose_x_to_blog_idea`** for the reverse direction. The §28.29 deterministic plagiarism floor runs against every blog→X output — Jaccard + longest-shared-n-gram, AI cannot underreport. `high` overlap **blocks** the drafts-pipeline insert until Daniel checks an override box (audit-logged). Linkage in `blog_to_post_links` lands at SHIP time for blog→X (drafts may be discarded) and immediately at idea creation for X→blog.

### One-time setup (Phase 6 extras)

Run `uv run python -m scripts.init_db` once to apply migration 016 and seed the new settings rows. The two new views surface as `pages/17_Blogs.py` (Blogs index) and `pages/18_Blog_Editor.py` (Blog Editor). Open the Blogs index to create your first blog; the Editor opens automatically.

Settings worth knowing:

- `blog_stale_status_warning_days` (default `21`) — rows whose `days_in_current_status` exceeds this surface a yellow stale-state keyline in the Blogs index.
- `blog_default_target_length_words` (default `1500`) — fallback target length for new blogs when Daniel doesn't specify one. Informational; never a hard gate.
- `blog_export_default_directory` (default `data/blog_exports/`) — prefill for the §14.15 Export dialog's target-path picker. Relative to repo root unless absolute.
- `blog_repurposing_plagiarism_check_enabled` (default `true`) — when true, blog→X repurposing outputs run through the §28.29 deterministic plagiarism floor against the source blog body. **Disable only for testing.** Leaving the guard on is the point of having it.
- `blog_agent_max_draft_iterations` (default `3`) — informational ceiling on consecutive `draft_blog` calls within a single editing session. UI surfaces a soft warning at this count; not enforced in code.

## X API reads (Phase 7)

Phase 7 wires the X API as the *read* path (`spec.md` §17 Phase 7, §25 Phase 7, §29.1 Phase 7 block). Account snapshots, recent-post imports, hourly metrics refresh, and reply-target metrics refresh now have a programmatic option. Manual entry remains the always-available fallback — every API path has a separately-tested manual-equivalent path that survives `data_collection_mode = 'manual'`.

**What ships:**

- **`app/x_client.py`** — shared subprocess wrapper around the `xurl` CLI. The dashboard never holds OAuth tokens; xurl stores them under `~/.xurl/` and attaches the bearer header on every invocation. Typed exceptions (`XApiRateLimited` / `XApiNotFound` / `XApiUnavailable`) so callers can branch on failure mode. Every call logs to `raw_api_responses` for audit + the Settings "Recent X API failures" panel. See `docs/X_API_SETUP.md` for install + `xurl auth login` scope rationale.
- **Four scheduled jobs (`scripts/`, `app/jobs/`)** — `collect_account_snapshot.py` (daily 09:00 ET), `import_recent_posts.py` (daily 09:05 + one-shot `--backfill` with audit-row idempotency gate), `post_metrics_refresh.py` (hourly, staleness-tier priority queue), `reply_target_metrics_refresh.py` (hourly, 404 → `status='target_deleted'`, 429 keeps `last_checked_at_utc` stable). Each writes a `scheduled_job` audit-log row at run-end with row counts + rate-limit hits + runtime.
- **launchd plists under `launchd/`** — **NOT auto-loaded.** Daniel runs `launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.<job>.plist` per job after consent. `docs/SCHEDULED_JOBS.md` has the runbook (prerequisites, exact invocations, failure-mode matrix, symmetric unload).
- **Six-dimension resolver (§29.3)** — `velocity_score(snapshots)` + `timing_score(post_age_minutes, follower_count)` + `apply_velocity_timing_modifiers` compose with the base 4-dim resolver. High velocity bumps `engagement_surface_score` +1 (cap 3); low timing downgrades `recommended_action` by one tier. Base ladder runs FIRST, modifiers AFTER. The pure-function 4⁶ = **4,096-combo test** in `tests/test_reply_target_resolver.py` enumerates every cell against an independent oracle.
- **Thread-classifier lint (§29.10) — NEW** — `thread_classifier_lint` in `app/agent/lint.py`. **Distinct from** the Phase 5.9 §28.18 `reply_quality_lint` (draft-side; untouched). The new lint categorizes the *target post's thread quality* before drafting: `ragebait` OR `hijacking_required_to_mention_stir` → `lint_blocked=true`; `meme_with_no_serious_reply_path` + `low_quality_reply_thread` are signals (each subtracts 1 from `reply_opportunity_score`). Gated by `reply_target_lint_enabled` setting.
- **Force-draft override UI (§29.7)** — when `lint_blocked=1`, the Reply Target Queue dims the row and disables "Draft reply"; a "Force-draft (overrides lint)" affordance prompts for a **mandatory** reason. Submit writes `reply_targets.force_drafted=1 + force_drafted_reason`, logs an `audit_logs` row (`category='data'`, `event_type='lint_force_drafted'`), then hands off to Agent Chat.
- **Q1 programmatic auto-pulls (§28.20 / §28.24 / §28.25)** — `score_replier_pool(auto_scan=True)` calls `/2/tweets/search/recent?query=conversation_id:<id>`; `_analyze_account_to_dict(auto_pull=True)` chains the user + recent-tweets endpoints; `_audit_profile_to_dict(auto_pull_bio=True)` pulls Daniel's bio. All three: manual paste is the always-available fallback (auto_* = False); X API failure returns `status='failed'` with a rationale so the caller can prompt for paste.
- **Settings panel** — `app/pages/7_Settings.py` gains a "Data sources & X API health" section above the Audit log: mode echo, per-job last-refresh timestamps, "Recent X API failures (last 7 days)" reading `raw_api_responses` filtered by `status_code >= 400`.

### One-time setup (Phase 7)

1. `brew install xurl`, then `xurl auth login` with scopes `tweet.read users.read offline.access`. See `docs/X_API_SETUP.md`.
2. `uv run python -m scripts.init_db` — applies migration 018; `data_collection_mode` flips to `'api'` and the five new settings rows seed.
3. `mkdir -p data/logs && uv run python -m scripts.import_recent_posts --backfill` — one-shot post-history import. Re-runs are idempotent (audit gate).
4. Per job you want active: `cp launchd/com.scalinity.xgrowth.<job>.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.<job>.plist`. None of the four plists are loaded automatically.

Settings worth knowing:

- `data_collection_mode` (default `'api'` post-Phase-7) — `'manual'` disables scheduled jobs (jobs no-op cleanly). Manual paths always remain available regardless.
- `reply_target_metrics_refresh_interval_minutes` / `post_metrics_refresh_interval_minutes` (default `60`) — paired with `StartInterval` in the matching launchd plist. Change both in lockstep.
- `combined_ai_monthly_cost_ceiling_usd` (default `30.0`) — supersedes the historical $25 Anthropic-only ceiling. Phase 9 Grok spend accumulates into the same row.
- `x_api_rate_limit_window_minutes` (default `15`) / `x_api_recent_failures_visible_days` (default `7`) — Settings panel lookback bounds.
- `reply_target_lint_enabled` (default `true`) — flip to `false` to skip the §29.10 thread-classifier lint (cost-saving toggle; not recommended for normal use).

## X API writes (Phase 8)

Phase 8 wires the *write* path — `POST /2/tweets` direct from the Growth Agent's publish flow (`spec.md` §25 Phase 8, §28.10 Phase 5.5 → Phase 8 transition, §29.1 Phase 8 block). Replaces the Phase-5.5-stubbed manual-clipboard-only branch with a real X API branch alongside the existing manual branch. `publish_via_api_enabled` (default `true`) gates the per-publish choice. The same six-check + atomic-transaction wrapper from §28.10 runs in both branches — only the X API call inside it differs. Manual fallback remains a Settings-selectable path forever (§29.1).

**What ships:**

- **`publish_post_to_x_via_api(text, in_reply_to_x_post_id)` in `app/x_client.py`** — shells out to `xurl --request POST /2/tweets` with bounded retry on 5xx per `x_posting_publish_retry_attempts_per_token`. Typed exception hierarchy: `XApiColdReplyError` (403; X considered it a real attempt → token consumed), `XApiServerError` (5xx after retries → token consumed per rule #10(f)), `XApiTimeoutError` (token consumed; never retried because X may have already processed the request and a retry would double-post). 429 is re-raised so the publish wrapper leaves the confirmation token UN-consumed.
- **`check_write_rate_capacity(conn)` sliding-window enforcement** — counts `posts.published_to_x_at` rows in the last 15min + 24h windows against `x_write_rate_limit_per_15min` / `_per_24h`. Refusal happens BEFORE the X API call so a saturated window doesn't burn the token. Manual-clipboard publishes count too — Daniel is rate-limited per X account, not per branch.
- **`publish.publish_post_atomic` branches on `publish_via_api_enabled`** — TRUE: capacity check → API call → on 200 sets `posts.publish_method='agent_confirmed'` + `x_post_id` from response + `publish_confirmation_tokens.consumed_by_x_post_id`. FALSE: unchanged Phase 5.5 manual-clipboard branch. Six new except branches map the §22 + §29.11 token-consumed matrix verbatim. The §28.10 six-check chain, raw-token redaction, IWH check, dark-pattern lint preflight, and audit logging are **untouched**.
- **§28.10 step 8 crash recovery via `recovery.reconcile_orphans_via_x_api()`** — when an orphan exists (e.g. timeout mid-transaction), the boot scan calls `GET /2/users/me/tweets?since_id=MAX(posts.x_post_id)` and matches by `sha256(text)` against the X timeline. Matches auto-reconcile via the existing `mark_orphan_posted` helper + set `publish_method='agent_confirmed'`; unmatched orphans fall back to the existing manual-reconcile UI exactly as Phase 5.5.
- **vcr.py-shaped YAML cassettes under `tests/fixtures/x_api/`** — seven cassettes (success post + success reply + 429 + 403 cold-reply + 500 + timeout sentinel + recent-tweets-match) loaded by a subprocess-aware patcher at `tests/_xurl_fixture.py`. Custom loader because xurl is a CLI binary and vcr.py only sees Python HTTP — the YAML shape is vcr.py-compatible so a future transport migration plays back the same files.
- **`scripts/rerecord_x_api_fixtures.py` + `docs/X_API_FIXTURES.md`** — interactive procedure that posts real tweets via xurl and **auto-deletes them via `DELETE /2/tweets/{id}` before exit**; failed delete is a script-level failure that surfaces the orphan ID. Only success-path cassettes re-record automatically; error-path cassettes are hand-maintained because X doesn't surface 403 / 429 / 500 on demand.
- **Settings UI Publishing subsection** — `publish_via_api_enabled` toggle (default ON) in Settings → Growth Agent → Publishing with a flag-amber "MANUAL-CLIPBOARD MODE · X API WRITES DISABLED" keyline banner when OFF. `x_write_rate_limit_per_15min` + `x_write_rate_limit_per_24h` editable inline.

### One-time setup (Phase 8)

1. Re-run `xurl auth login` and paste `tweet.read tweet.write users.read offline.access` when prompted (note the added `tweet.write`). xurl updates `~/.xurl/` in place. See `docs/X_API_SETUP.md` §8.
2. `uv run python -m scripts.init_db` — applies migration 019; the three Phase 8 settings rows seed via `INSERT OR IGNORE`.
3. **Verify the augmented scope:** `xurl /2/users/me` then a low-risk write smoke (`xurl --request POST /2/tweets --data '{"text":"phase 8 smoke"}'`) — delete the result via `xurl --request DELETE /2/tweets/<id>`. If the POST returns 403 with "subset of X API V2 endpoints", your X developer app project is on the wrong tier.

Settings worth knowing:

- `publish_via_api_enabled` (default `true`) — TRUE: §28.10 publish flow takes the real `POST /2/tweets` branch. FALSE: takes the manual-clipboard fallback branch. Toggle live in Settings → Growth Agent → Publishing; manual fallback never goes away.
- `x_write_rate_limit_per_15min` (default `50`) / `x_write_rate_limit_per_24h` (default `1000`) — sliding-window caps on X API writes. Defaults match §25 Phase 8 verbatim; tune as your X API tier allows.
- The cold-reply 403 UX message ("X API refused this reply. Engage with this author's posts first, or use the manual fallback.") is automatic — no setting controls it. The token consumes on a 403 because X considered it a real attempt; the manual fallback is always available as the next step.

### Fixture re-record (testing)

The Phase 8 test suite uses canned cassettes; CI never makes a real X API call. To re-record (when X API contracts change):

```bash
uv run python -m scripts.rerecord_x_api_fixtures           # interactive
uv run python -m scripts.rerecord_x_api_fixtures --no-prompt  # scripted
uv run python -m scripts.rerecord_x_api_fixtures --dry-run   # print plan only
```

See `docs/X_API_FIXTURES.md` for the cassette format, the timeout sentinel pattern, and the sandbox-cleanup safety rules.
