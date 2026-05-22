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
