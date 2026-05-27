# Project rules — X Growth Dashboard

Project-local standing rules that override conflicting global defaults. The user's global `~/.Codex/AGENTS.md` still applies; this file adds project-specific constraints.

---

## What this project is

**Single-user, local-only Python tool.** Runs on Daniel's machine only — not distributed, not multi-tenant. No Stripe, no auth, no cloud sync. The "production-minded" practices in `spec.md` (immutable snapshots, `VACUUM INTO` backups, schema discipline) exist to protect Daniel's data and learning, not to prepare for distribution.

**Two presentation surfaces** (see `spec.md` §31, 2026-05-24 native-desktop conversion):

- **Streamlit** (`streamlit run app/main.py`) — the original dev surface; stays runnable throughout Phase 11.
- **Native macOS `.app`** (Phase 11) — Tauri v2 shell + React/TS frontend + the *same* Python backend run as a FastAPI loopback sidecar; movable to `/Applications`.

**Native packaging for Daniel's own machine is explicitly in scope (Phase 11) and is NOT distribution.** "Native" describes the shell, not the audience. What stays out of scope and must still be refused (point back to §1 / §7.1 / §31): the **App Store, multi-user, cloud sync, telemetry, auto-update, or any "ship it to other people" path.**

---

## Authoritative documents

- `spec.md` at the repo root is **authoritative**. If implementation drifts from spec, the spec is correct by default — update the implementation, not the spec.
- If the spec itself is wrong, **update `spec.md` first** in a separate change before writing implementation code. Never let implementation diverge silently.
- Section references in implementation code, commit messages, and discussion should use the spec's `§N` numbering.

---

## Tooling

- **Package manager:** `uv` only. Use `uv add`, `uv add --dev`, `uv sync`, `uv lock`, `uv run`. Never `pip install` directly. Never edit `pyproject.toml` dep lists by hand when `uv add` would do the job.
- **Python:** ≥ 3.11. `uv` provisions and manages the interpreter.
- **Shell examples:** native macOS Terminal syntax. No iTerm-specific features in docs or scripts.
- **DB CLI:** `sqlite-utils` is the inspection helper. The app itself uses `st.connection`.

---

## Directory conventions

- `app/main.py` is the Streamlit entry point — routing and `st.session_state` bootstrap only.
- **Streamlit pages live in `app/pages/`.** New views go there, not in `app/`.
- `app/agent/` is the Growth Agent module (§28). Anthropic API client, tools, session management, confirmation flow, lint pass, recovery routine.
- `app/x_client.py` is the X API OAuth wrapper (publishing only, Phase 5.5+).
- `migrations/` holds raw SQL files applied in lexicographic order.
- `scripts/` holds operational one-shots (backup, export, etc.). Not the daily data path.
- `data/` is user-private and `.gitignore`'d — DB, exports, backups. **Phase 11:** user-writable data also resolves to `~/Library/Application Support/XGrowthDashboard/` for the native app (path resolver: `XGROWTH_DATA_DIR` env → App Support → legacy `./data`); see §31.5.
- `tests/` mirrors `app/` layout where it helps.
- `app/service/` (Phase 11, §31.3) — FastAPI loopback sidecar: a **pure adapter** that wraps the existing backend (agent, forms, exports, jobs, backup, db) over HTTP/SSE for the native app. No business logic lives here.
- `desktop/` (Phase 11, §31) — Tauri v2 shell (Rust) + Vite/React + TypeScript frontend. Recreates the `theme.py` design system 1:1; charts via Plotly.js.

---

## Streamlit side-effects discipline

The user's global rule against React's `useEffect` translates to Streamlit as follows:

- **Never use callback patterns that silently re-run on every rerun cycle.** Streamlit reruns the script top-to-bottom on every interaction — code that "happens to run at the right time" is a bug.
- **Use `st.session_state` flags explicitly.** Initialize once with `if "key" not in st.session_state: st.session_state.key = default`. Mutate via explicit handlers (`on_click`, form `submit`), not inside the render flow.
- **Derive, don't sync.** Compute display values from `st.session_state` and DB reads each rerun. Don't write "if A changed, also update B" effects.
- **For real-once-only setup** (schema bootstrap, settings seed), gate behind an idempotent check (`CREATE TABLE IF NOT EXISTS`, settings-row upsert).

---

## UI work

- **Use the `/frontend-design` skill before building or revising any UI.** Pages under `app/pages/`, components under `app/components/`, and the `app/main.py` shell all count. Invoke the skill once at the start of UI work and commit to the aesthetic direction it returns; don't ship Streamlit defaults.
- The dashboard's aesthetic identity is the **dark "instrument-panel" theme** owned by `app/components/theme.py`: deep-ink background, warm bone text, Fraunces display serif, IBM Plex Sans body, JetBrains Mono for every number. Every page calls `apply_theme()` first thing after `st.title`. Don't introduce new color tokens or fonts in page files — extend `theme.py` and reuse.
- §14.7 fixes the MVP theme as dark-only. Don't add a light variant until the spec says so.
- **Phase 11 native UI (`desktop/`):** `/frontend-design` still applies before any view work. The design system is recreated **1:1** from `theme.py` as CSS tokens + React components (§31.4) — same `PALETTE`, same fonts (bundled, not Google-fetched), same component helpers, charts via Plotly.js fed the Python figure JSON. Dark-only still holds. Don't fork the aesthetic — mirror it; treat visible drift against the Streamlit views as a bug (§31.7).

---

## Scope discipline

- **Comprehensive scope is the default.** MVP scope is exactly what §19 of `spec.md` enumerates — do not silently strip features. If §19 says nine views, ship nine views. (The app shipped **18 views** through Phase 6; Phase 11's native port covers all 18 — see §31.7. §19's "nine" is the MVP-era count.)
- A "minimum" suggestion is only valid if §19 explicitly defers the feature to V1.1+.
- When unsure, re-read §19 and §25 before scoping down.

---

## Workflow before non-trivial change

1. Search the existing codebase for prior art (function, view, pattern).
2. Re-read the relevant `spec.md` section.
3. Look at existing tests for the area.
4. Then implement.

Documentation lookup happens **before** "let me try a thing" — when working with Streamlit, Anthropic SDK, or SQLite specifics, look up the docs first.

---

## Branch and worktree workflow

- Work directly on `main` in this checkout. Do **not** create or switch to git worktrees or feature branches unless Daniel explicitly asks for one.
- Before starting implementation or review fixes, verify the checkout with `git status --short --branch`. If it is not on `main`, stop and ask how to proceed.
- When a task or review-fix is complete and verification passes, stage only the essential files for that work, verify `git diff --staged`, commit with the appropriate project convention, and `git push origin main` without waiting for a separate prompt.
- Preserve unrelated local changes. If unrelated changes block a clean commit or push, explain the conflict and ask Daniel before touching them.
- For review/address workflows, keep the existing per-finding discipline: commit and push each completed fix independently.

---

## Commits

- Conventional Commits format (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`).
- Phase-boundary commits use `"Phase N: <one-line summary>"` per `spec.md` §25.
- Verify `git diff --staged` before committing. Never blindly `git add .`.
- The real `.env` (with `ANTHROPIC_API_KEY`) lives at the repo root and is `.gitignore`'d. `.env.example` is the only env file ever committed.

---

## Implementation status doc

Day-to-day implementation status lives in **`docs/index.html`** — an interactive dark "instrument-panel" dashboard styled to match `app/components/theme.py`. The legacy `docs/IMPLEMENTATION_STATUS.md` is frozen at the end of Phase 5.8 and kept only as a historical record. **Don't append to the .md.**

When a phase ships, append a new phase block to `docs/index.html`:

1. Open `docs/index.html` and find the HTML-comment template at the bottom of `<main>` (search for `TEMPLATE FOR THE NEXT PHASE`). Copy the entire `<section class="phase" id="phase-X-X" …>` block out of the comment.
2. Paste it before the `<!-- TEMPLATE … -->` comment and fill in:
   - `id="phase-N-N"`, `data-phase-id="N.N"`, `data-phase-title="…"`.
   - The four counter tiles (migrations / tests passing / acceptance gates / fix commits).
   - One `<details class="subsection" data-section-type="…">` per subsection. Allowed `data-section-type` values: `completed | gates | limitations | lessons | remediation | ambiguity | next`. The filter chips and per-section keyline colors are wired to these — anything else won't render correctly.
3. Add a matching `<li>` to `<ul id="phase-list">` in the sidebar (copy a prior entry, swap the anchor and label).

Verification: `open docs/index.html` on macOS. Confirm the new phase appears in the sidebar nav, the four counter tiles render with mono numerals, every filter chip (Completed / Gates / Limits / Lessons / Remediation) correctly shows/hides its sections, and the search input matches against the new content. Deep links work: `docs/index.html#phase-N-N-gates` should scroll-to-and-expand the right `<details>` block. Per the verification matrix below this is a docs-only change — no `pytest`/`ruff` gate required, but Conventional Commits subject still applies (e.g. `docs(status): Phase N.N — <one-line summary>`).

---

## Issue tracking and review-fix workflow

This project does **not** use Linear or GitHub Issues. It tracks fixes locally via the Codex Task tools (`TaskCreate` / `TaskUpdate` / `TaskList`). The `/address` skill (and any skill that "files a Linear parent + sub-issues") must adapt as follows on this repo:

- **In lieu of a Linear project:** file a *parent* local task ("Address /review-N findings for <area>") and one *sub-task* per finding via `TaskCreate`. Sub-tasks must reference the finding's severity (🔴/🟡/🔵) and the offending `file:line` from the review report.
- **Per-fix workflow:** for each sub-task, mark `in_progress` → make the code change → run the verification command(s) below for the affected area → `git commit` with a Conventional Commits subject that references the sub-task ID (e.g. `fix(scripts): #14 — restore_db preserves WAL/SHM sidecars`) → `git push origin main` → mark `completed`.
- **Parent close-out:** once every sub-task is `completed`, mark the parent `completed`.
- **Verification commands by area:**
  - Python source (`app/`, `scripts/`, `tests/`): `uv run pytest -q` AND `uv run ruff check`.
  - UI changes (`app/pages/`, `app/components/`): the two above PLUS a Streamlit boot smoke (`uv run streamlit run app/main.py --server.headless true` and check no exception in the logs) when feasible.
  - Migrations (`migrations/*.sql`): `uv run pytest tests/test_schema.py -q` AND a manual `uv run python -m scripts.init_db` against a fresh tmp DB.
  - Docs only (`docs/`, `README.md`, `AGENTS.md`): no test gate required; spelling/link sanity check is enough.
- **Git remote:** `origin` points at `https://github.com/scalinity/XGrowthDashboard` (public). Every fix commit is pushed individually so the public history reflects per-finding granularity.

The point of the per-sub-task push is the same point Linear would serve: an external observer can audit which finding produced which commit without trawling for `[PRE-EXISTING]` prefixes or commit-body footnotes.

---

## Cursor Cloud specific instructions

### Environment bootstrap

- `uv` must be on `PATH`. It is installed to `~/.local/bin` via `curl -LsSf https://astral.sh/uv/install.sh | sh`. The update script handles this idempotently.
- After `uv sync`, the `.venv/` is ready. All commands go through `uv run`.

### Running the Streamlit app

```bash
uv run streamlit run app/main.py --server.headless true --server.port 8501
```

The `--server.headless true` flag is required in Cloud Agent VMs (no TTY for the "Email:" prompt). The database auto-initializes on first access if migrations have been applied.

### Database initialization

```bash
mkdir -p data && uv run python -m scripts.init_db
```

Creates `data/dashboard.db` and applies all migrations + seeds settings/milestones. Idempotent — safe to re-run.

### Verification commands

| Area | Commands |
|------|----------|
| Lint | `uv run ruff check` |
| Tests | `uv run pytest -q` |
| Streamlit boot smoke | `uv run streamlit run app/main.py --server.headless true` (check no exception in first 5s) |

### Known test failures (pre-existing)

15 tests in `test_agent.py`, `test_grok_integration.py`, and `test_x_api_writes.py` fail due to X API fixture cassette mismatches (xurl subprocess mock). These are pre-existing on `main` and unrelated to environment setup. Core functionality (1239 tests) passes.

### API keys

No API keys are required for basic development. The app starts and the full test suite (minus X/Grok integration) runs without any `.env` secrets. For AI agent features, set `ANTHROPIC_API_KEY` in `.env` at repo root.

---

## What this file is not

This file is project-specific operational rules. Architecture decisions and product reasoning live in `spec.md`. Day-to-day status lives in `docs/index.html` (interactive). `docs/IMPLEMENTATION_STATUS.md` is frozen at Phase 5.8 — don't append to it. Don't duplicate any of this here.
