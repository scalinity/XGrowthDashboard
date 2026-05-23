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
