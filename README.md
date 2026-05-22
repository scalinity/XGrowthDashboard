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
