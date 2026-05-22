# Project rules — X Growth Dashboard

Project-local standing rules that override conflicting global defaults. The user's global `~/.claude/CLAUDE.md` still applies; this file adds project-specific constraints.

---

## What this project is

**Single-user, local-only Python/Streamlit tool.** Runs as `streamlit run app/main.py` on Daniel's machine. Not packaged, not distributed, not multi-tenant. No Stripe, no auth, no cloud sync. The "production-minded" practices in `spec.md` (immutable snapshots, `VACUUM INTO` backups, schema discipline) exist to protect Daniel's data and learning, not to prepare for distribution.

If a suggestion implies distribution, packaging for the App Store, multi-user support, or cloud sync — refuse and point back to §7.1 of `spec.md`.

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
- `data/` is user-private and `.gitignore`'d — DB, exports, backups.
- `tests/` mirrors `app/` layout where it helps.

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

---

## Scope discipline

- **Comprehensive scope is the default.** MVP scope is exactly what §19 of `spec.md` enumerates — do not silently strip features. If §19 says nine views, ship nine views.
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

This project does **not** use Linear or GitHub Issues. It tracks fixes locally via the Claude Code Task tools (`TaskCreate` / `TaskUpdate` / `TaskList`). The `/address` skill (and any skill that "files a Linear parent + sub-issues") must adapt as follows on this repo:

- **In lieu of a Linear project:** file a *parent* local task ("Address /review-N findings for <area>") and one *sub-task* per finding via `TaskCreate`. Sub-tasks must reference the finding's severity (🔴/🟡/🔵) and the offending `file:line` from the review report.
- **Per-fix workflow:** for each sub-task, mark `in_progress` → make the code change → run the verification command(s) below for the affected area → `git commit` with a Conventional Commits subject that references the sub-task ID (e.g. `fix(scripts): #14 — restore_db preserves WAL/SHM sidecars`) → `git push origin main` → mark `completed`.
- **Parent close-out:** once every sub-task is `completed`, mark the parent `completed`.
- **Verification commands by area:**
  - Python source (`app/`, `scripts/`, `tests/`): `uv run pytest -q` AND `uv run ruff check`.
  - UI changes (`app/pages/`, `app/components/`): the two above PLUS a Streamlit boot smoke (`uv run streamlit run app/main.py --server.headless true` and check no exception in the logs) when feasible.
  - Migrations (`migrations/*.sql`): `uv run pytest tests/test_schema.py -q` AND a manual `uv run python -m scripts.init_db` against a fresh tmp DB.
  - Docs only (`docs/`, `README.md`, `CLAUDE.md`): no test gate required; spelling/link sanity check is enough.
- **Git remote:** `origin` points at `https://github.com/scalinity/XGrowthDashboard` (public). Every fix commit is pushed individually so the public history reflects per-finding granularity.

The point of the per-sub-task push is the same point Linear would serve: an external observer can audit which finding produced which commit without trawling for `[PRE-EXISTING]` prefixes or commit-body footnotes.

---

## What this file is not

This file is project-specific operational rules. Architecture decisions and product reasoning live in `spec.md`. Day-to-day status lives in `docs/index.html` (interactive). `docs/IMPLEMENTATION_STATUS.md` is frozen at Phase 5.8 — don't append to it. Don't duplicate any of this here.
