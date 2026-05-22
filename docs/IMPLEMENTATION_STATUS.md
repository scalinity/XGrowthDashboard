# Implementation status — X Growth Dashboard

| Field          | Value                                  |
| -------------- | -------------------------------------- |
| Project        | X Growth Dashboard                     |
| Current phase  | 5.5 — Growth Agent                     |
| Spec version   | 2026-05-21 (see `spec.md` §0 revision notes) |
| Next phase     | `phase-5.6-reply-target-discovery.md`  |

---

## Completed in this phase

### Migrations

- `migrations/005_agent_core.sql` — six agent-domain tables
  (`agent_conversations`, `agent_messages`, `agent_tool_calls` with
  `redacted_arguments` flag, `agent_target_accounts`, `voice_samples`,
  `agent_drafts` with `iwh_attempt_index DEFAULT 1`).
- `migrations/006_publish_columns.sql` — ALTER `posts` with
  `agent_draft_id`, `published_via_agent_message_id`, `published_to_x_at`,
  `publish_method`, `publish_last_error`, `publish_attempt_count`; CREATE
  TABLE `publish_confirmation_tokens` with `token_hash UNIQUE`,
  `draft_text_hash_at_issue`, `expires_at_utc`, `consumed_at_utc`.
- `migrations/007_post_classifications_unique.sql` — (added during
  /address W12) CREATE UNIQUE INDEX on `post_classifications(post_id)`
  with an idempotent DELETE-then-INDEX dedupe to collapse any existing
  duplicates. Lets `_save_draft_post` use `INSERT ... ON CONFLICT
  (post_id) DO UPDATE` so retries no longer accumulate classification
  rows. Existing `test_views.py` cases that simulated reclassification
  via a second INSERT were updated to UPDATE — same assertion semantic.
- `migrations/008_agent_tool_usage_view.sql` — (added during /address
  S12) CREATE VIEW `v_agent_tool_usage` rolling up `agent_tool_calls`:
  per-tool counts (success / partial / error), first/last call
  timestamps, total cost USD, avg duration ms. The Settings panel can
  surface "tool not called in N days" candidates for pruning in a
  follow-up iteration.

### `app/agent/` module

- `audit.py` — single-source `log_tool_call()` with raw-token redaction
  driven by `PUBLISH_TOOL_NAMES` (§28.2 rule #11).
- `confirmation.py` — `mint_confirmation_token()` + `validate_and_consume_
  token()` with the six-check chain and typed exceptions per failure
  path (§28.2 rule #10).
- `publish.py` — `publish_post_atomic()` runs validation, MVP manual-
  clipboard branch (builds X intent URL, no API call), publish-state
  writes, audit logging. Validation failures leave the token unconsumed
  (§28.10 step 6); post-validation failures consume the token and mark
  the row `publish_method='failed'` (§28.4 atomicity rule).
- `_internal_tools.py` — `INTERNAL_TOOLS` list with `publish_post_to_x`
  and `publish_reply_to_x`. Imported NOWHERE except by the click-handler
  and the startup assertion.
- `tools.py` — `AGENT_TOOLS` list with 15 entries: the merged §28.4 +
  §25 catalog. Each carries an Anthropic input_schema + Python handler.
- `recovery.py` — `detect_orphans()` + `mark_orphan_posted/failed`.
- `cost.py` — `estimate_cost()`, `month_to_date_spend_usd()`,
  `check_ceiling_or_raise()` with versioned rate table.
- `lint.py` — `lint_draft()` invokes Haiku (or falls back to an offline
  substring matcher when `LINT_OFFLINE=1` or the API is unavailable).
- `session.py` — `parse_iwh_self_score()` + `decide_save_or_revise()`
  policy gate. IWH counter never enters the agent's reachable state.
- `voice.py` — `get_active_voice_samples()`, `add_voice_sample()`,
  `deactivate_voice_sample()`, `touch_last_used_at()`.
- `prompt_builder.py` — `build_system_prompt()` splices spec §28.2
  rules verbatim into the Section 3 placeholder, top-N voice samples
  into Section 5, AGENT_TOOLS catalog into Section 7. Drift check
  `verify_rule_count_matches_spec()` asserts 13/13 rules.
- `client.py` — `AgentClient.send_message_sync()` + `dispatch_tool_call()`.
  `_call_model` is overridable for tests; SDK call uses the assembled
  system prompt + `AGENT_TOOLS` spec only.
  - **/address C5: IWH+lint gate now wired into the dispatcher** —
    `dispatch_tool_call` gains `assistant_text` + `current_attempt_index`
    kwargs. For any tool in `SAVE_DRAFT_TOOLS` (`save_draft_post`,
    `save_draft_reply`), the dispatcher runs
    `session.decide_save_or_revise` BEFORE invoking the handler.
    `action='refuse'` returns `{status='error', error='refused by IWH
    gate'}` with audit row `notes='iwh-gate refused'`. `action='revise'`
    returns `{status='revise_required', rationale, next_attempt_index}`
    with audit row `notes='iwh-gate revise'`. `action='save'` falls
    through to the existing handler invocation. `send_message_sync`
    seeds `current_attempt_index` from
    `MAX(iwh_attempt_index)+1` over the conversation's non-rejected
    drafts. Before C5 the gate was dead code in production — only
    tests exercised it; the model could call `save_draft_post`
    unconditionally and the entire §28.2 rule #12+#13 discipline
    was bypassed at runtime.

### `config/agent_system_prompt.md`

8-section template per §28.3 with three placeholders the prompt builder
substitutes at runtime. The template carries the engagement-psychology
guidance verbatim (Section 4) and the output-format rules (Section 8).

### Startup invariant

`app/main.py` runs `_assert_publish_tools_unreachable()` once per
session: asserts `AGENT_TOOLS.name` ∩ `INTERNAL_TOOLS.name` = ∅.

### UI surfaces (theme: `app/components/theme.py`)

Five new theme helpers — all using existing PALETTE tokens, no new
fonts: `tool_call_block`, `iwh_meter`, `cost_meter`,
`token_ttl_countdown`, `console_log_row`.

- `app/pages/9_Agent_Chat.py` — §14.8 chat surface. Sidebar = cost
  meter (always) → IWH meter (when active draft) → past sessions list.
  Inline draft actions: publish / save / discard. Publish modal mints
  a token, renders the post text in Fraunces 1.3rem, runs the TTL
  countdown at 1Hz (the only animated element in the app), and
  invokes `_internal_tools.publish_post_to_x` synchronously. Crash-
  recovery orphan banner at top.
- `app/pages/7_Settings.py` — Growth Agent panel appended: cost meter,
  IWH policy form, voice samples CRUD, curated `agent_target_accounts`
  CRUD, orphan-post recovery list.
- `app/pages/1_Today.py`, `2_Next_Rep.py`, `4_Content_Performance.py`,
  `6_Weekly_Review.py` — "Ask the agent" button rows append to existing
  layouts. Each sets `st.session_state.agent_context_seed` and switches
  to the chat page.

### Export allowlist extensions

`app/exports/allowlists.py` extended per §16 (7) / (8):

- `POSTS_ALLOWLIST.default_columns` gains `agent_draft_id`,
  `published_to_x_at`, `publish_method`, `publish_attempt_count`.
- `POSTS_ALLOWLIST.opt_in_columns` gains
  `published_via_agent_message_id`.
- `POSTS_ALLOWLIST.excluded_columns` gains `publish_last_error` —
  NEVER exported under any flag.
- Six new allowlists for the agent-domain tables, with
  `agent_tool_calls.arguments_json` / `result_json` / `error_message`
  in `excluded_columns` for defense in depth.

### Settings seeds

7 new settings rows: `agent_default_model`,
`agent_monthly_cost_cap_usd`, `agent_voice_sample_count`,
`iwh_self_score_minimum`, `iwh_max_revision_attempts`,
`agent_dark_pattern_lint_enabled`,
`x_posting_confirmation_token_ttl_seconds`.

### Tests

`tests/test_agent.py` — Session-1 invariants + /address regressions:

- Tool-registry partitioning (3 tests).
- IWH counter outside agent context (1).
- Six-check confirmation chain (6).
- Atomic publish — validation failure leaves token unconsumed, success
  path stages manual_clipboard, double-publish rejected (3).
- Raw-token redaction — happy path + error path (2).
- Orphan-post detection (1).
- **(/address C2)** `test_revised_drafts_are_publishable`
- **(/address C3)** `test_every_agent_tool_handler_executes_against_fresh_db`
- **(/address C5)** `test_dispatch_tool_call_refuses_save_draft_with_low_iwh`
- **(/address C5)** `test_dispatch_tool_call_blocks_engagement_bait_via_lint`
- **(/address W1)** `test_detect_orphans_excludes_fresh_manual_clipboard_publishes`

`tests/test_agent_session2.py` — Session-2 behaviors + /address regressions:

- Dark-pattern lint offline mode (5).
- Monthly cost ceiling (4).
- IWH `decide_save_or_revise` policy gate (6).
- Prompt drift check + voice/tool injection (5).
- Voice samples CRUD (3).
- Export carve-outs (7).
- Conversation/message persistence helper (1).
- **(/address W3)** `test_tool_call_cost_usd_NOT_counted_in_mtd`

Schema test list updated to include all four Phase-5.5-era migrations:
`005_agent_core.sql`, `006_publish_columns.sql`,
`007_post_classifications_unique.sql`, `008_agent_tool_usage_view.sql`.

---

## /address remediation (post-/review-2)

A `/review-2` pass over the Phase 5.5 changeset surfaced 5 🔴 / 26 🟡 /
12 🔵 findings. All 43 were addressed across 15 fix commits on `main`,
each pushed individually for per-finding audit traceability. The new
test count rose from 158 (end of original Phase 5.5) to **196**
(after remediation) — 38 new tests, most adding regressions for the
Critical issues C1–C5.

Key behavioral changes landed during /address (beyond C5 above):

- **`db.transaction(conn)` context manager** (`app/db.py`) — BEGIN
  IMMEDIATE / COMMIT / ROLLBACK wrapper. `publish_post_atomic`,
  `_save_draft_post`, `_save_draft_reply`, `_revise_draft` all run
  inside it now. The autocommit-mode connection meant the "atomic
  publish transaction" was not actually atomic before C1.
- **`_revise_draft` mints a `posts` row** so revised drafts are
  publishable (C2). Before this fix the IWH revision flow produced
  drafts that could not reach X — every attempt past v1 raised
  "Internal: agent_drafts row has no linked posts row" from the
  publish modal.
- **`get_open_hypotheses` + 3 sibling read tools** were column-name-
  mismatched against the actual views/tables and would crash sqlite3
  on first invocation (C3). The new
  `test_every_agent_tool_handler_executes_against_fresh_db`
  regression test runs every `AGENT_TOOLS` handler against a fresh-
  migration DB on every test sweep and is the structural fix.
- **Publish modal no longer busy-loops** (`time.sleep(1); st.rerun()`
  removed, C4). The TTL countdown is rendered once on modal open;
  server-side six-check rejects expired tokens cleanly on click. The
  prior loop blocked the Streamlit server thread for ~60 s per modal.
- **Raw confirmation tokens never cross `st.session_state`** (W4) —
  the mint moved from `_open_publish_modal` into the
  confirm-and-publish click handler itself. The raw UUID lives in
  exactly one local variable, in one synchronous call to
  `publish_post_to_x`, then `del`'d. Spec §28.10 contract held.
- **Manual-clipboard 30-min grace window** (W1) on
  `recovery.detect_orphans` so freshly-confirmed drafts don't show
  as orphans until the existing Mark-posted form has had time to
  reconcile them. Stops the chat banner + Settings panel from
  desensitizing the user on every successful publish.
- **MTD cost double-count footgun closed** (W3) by single-sourcing
  `month_to_date_spend_usd` from `agent_messages.input/output_tokens
  × rate_snapshot` only — `agent_tool_calls.cost_usd` is now an
  audit-only stamp, never summed into the monthly cap math.
- **Audit value-pattern guard** (W19) — `audit.py` now scans every
  string argument for the 32-hex UUID shape and raises
  `RawTokenLeakError` if found, even if it arrived under an unexpected
  argument name. Defense in depth alongside the name-keyed redaction.
- **Spec-rule splice is now structural** (W7 + W22) — the rule
  extractor raises on zero rules (silent splice failure is no longer
  possible) and the drift check parses BEGIN/END sentinel comments
  around the spliced block rather than rendered markdown shapes.

A "Lessons" subsection appears at the end of this file with the
non-obvious findings the /address pass uncovered.

---

## Acceptance gates satisfied (§25 Phase 5.5)

- [x] Startup assertion passes — `test_publish_tools_not_in_agent_
      registry` + `_assert_publish_tools_unreachable()` runs at bootstrap.
- [x] Prompt-injection test — `test_prompt_injected_iwh_score_does_
      not_override_orchestrator`.
- [x] Dark-pattern lint catches "5 secrets parents don't know — number
      3 will surprise you!" — `test_engagement_bait_number_will_
      surprise_is_flagged`.
- [x] Atomic publish: simulated failure — `test_validation_failure_
      leaves_token_unconsumed_and_marks_attempt`.
- [x] Crash recovery — `test_detect_orphan_posts`.
- [x] Raw-token redaction — `test_raw_token_redacted_from_arguments_json`
      + variant on error path.
- [x] Double-publish rejected — `test_double_publish_rejected_by_
      check_f`.
- [x] CSV export carve-out — `TestExportCarveOuts` (7 tests).
- [x] Cost ceiling — `test_over_ceiling_raises`.
- [x] Six-check chain — `TestSixCheckConfirmationChain` (6 tests).
- [x] Daniel can chat in `9_Agent_Chat.py` — page boots with 0
      exceptions in AppTest.
- [x] Daniel can publish via manual-clipboard — publish modal mints
      token → atomic publish → callout with intent URL.
- [x] `uv run pytest tests/test_agent.py -v` — 21/21 green
      (16 invariants + 5 /address regressions).
- [x] `uv run pytest -q` — **196/196 green** post-/address (was 190
      pre-remediation; the count rose with the new C2/C3/C5/W1/W3
      regression tests).
- [x] `uv run ruff check` — clean.

---

## Known limitations

- **`publish_post_to_x` MVP is manual-clipboard only.** V1.2 replaces
  the manual branch with a direct `POST /2/tweets` call under the same
  six-check + atomic-transaction contract. `x_client.py` exists but
  carries only the manual-clipboard helper.
- **Tool #9 `score_reply_candidates` and tool #15 `record_reply_target`
  are stubs** until Phase 5.6 lands the dedicated `reply_targets` table
  per §29.6.
- **Voice-sample auto-classification from posted-tweet history is V1.5+.**
  Daniel manually adds samples in Settings.
- **Streaming responses are deferred.** `send_message_sync` returns the
  full turn at once; the architecture already separates the SDK call
  from the UI render so a streaming upgrade is a future iteration on
  the existing surface.
- **X API read access (V1.1).** Crash recovery is manual at MVP; V1.1
  will use `GET /2/users/:id/tweets?since_id=` to auto-reconcile.

---

## Phase boundary

Commits on `main` — 11 implementation commits, 1 status doc, then 15
remediation commits from /address. In order:

**Implementation:**

1. `feat(migrations): #2 #3 — agent core tables + publish surface (005, 006)`
2. `feat(agent): #6 #9 — confirmation token chain + raw-token redaction`
3. `feat(agent): #4 #7 — atomic publish + INTERNAL_TOOLS registry`
4. `feat(agent): #5 #8 — AGENT_TOOLS registry + orphan-post recovery`
5. `feat(agent): #10 — startup assertion: publish tools cannot leak into AGENT_TOOLS`
6. `test(agent): #11 — Session-1 invariants (16 tests)`
7. `feat(agent): #13-#19 — Session-2 backend (prompt, cost, lint, session, voice, client)`
8. `feat(exports): #20 — Phase 5.5 carve-outs for posts + agent tables`
9. `test(agent): #24 — Session-2 behaviors`
10. `feat(views): #21 #22 #23 — agent chat page, settings panel, integration buttons`
11. `docs(status): Phase 5.5 Growth Agent complete`

**/address remediation (post-/review-2):**

12. `fix(agent): C1 — atomic transaction wrapper around publish + save/revise`
13. `fix(agent): C2 — _revise_draft mints a posts row so revisions are publishable`
14. `fix(agent): C3 — fix column mismatches in read tools (get_open_hypotheses + 3 sibling tools)`
15. `fix(views): C4 — drop publish-modal busy-loop that blocked the Streamlit thread`
16. `fix(agent): C5 — wire IWH+lint gate into dispatch_tool_call`
17. `fix(agent): W1 + S2 — orphan grace window + LIMIT 50`
18. `fix(settings): W2 — remove agent_dark_pattern_lint_enabled toggle`
19. `fix(agent): W3 — MTD spend single-sourced from agent_messages`
20. `fix(views): W4 — raw confirmation token never crosses st.session_state`
21. `fix(agent): W5/W6/W7/W8 — orphan reset + tool-result content + spec extraction + lane-gaps date range`
22. `fix(agent): lint clean-up — drop f-prefix on non-templated strings (W7 follow-up)`
23. `fix(migrations): W12 — UNIQUE(post_id) on post_classifications + ON CONFLICT UPSERT`
24. `fix(agent): W13/W14/W15/W16/W17 — lint catch + tool inputs + IWH multi-tag + lane separator`
25. `fix(agent): W18-W26 — allowlist + audit guard + cost msg + drift + ts parser + module imports + publish merge + stub status`
26. `fix(agent): S1/S3/S5/S6/S7/S8/S9/S10/S11/S12 — suggestion sweep`

---

## Lessons (from the /address pass)

1. **An "every-handler smoke test" is cheap insurance.** /review-2
   caught one column-mismatch bug (`get_open_hypotheses`); the
   regression test added during C3 exposed three more in sibling read
   tools that neither agent flagged. Most of the agent's read surface
   would have crashed sqlite3 on first invocation. The new
   `test_every_agent_tool_handler_executes_against_fresh_db` runs every
   `AGENT_TOOLS` handler with minimal kwargs against a fresh-migration
   DB and is now a permanent guard against this class of bug.
2. **The autocommit→atomic bug masked a worse second bug.** Wrapping
   `publish_post_atomic` in a transaction required initializing
   `consumed = None` before the try block — without that defensive
   binding, the post-validation `except Exception` would `UnboundLocal
   Error` on `consumed.token_id`. CA1 raised the symptom speculatively
   in W23 without a concrete trigger; the trigger turned out to be real
   at the same site for a different reason.
3. **C5 (IWH gate not wired) is the most consequential single fix.**
   The entire epistemic discipline that Phase 5.5 exists to enforce
   was dead code in production — the model could call `save_draft_post`
   unconditionally and the test suite passed because tests called the
   orchestrator directly. Lesson: integration tests must exercise the
   production path end-to-end (`dispatch_tool_call` → handler), not
   just call the gate function in isolation.
4. **Memory observation 7893 paid off across phases.** A 2026-05-21
   note flagged "Failing Tests Insert Second post_classifications Row
   to Simulate Reclassification — Must Use UPDATE Instead." When W12's
   UNIQUE migration broke two `test_views.py` tests, the memory entry
   explained exactly what to do.
5. **AppTest smoke is necessary but not sufficient for interactive
   bugs.** The publish-modal busy-loop (C4) passed AppTest before the
   fix because the modal isn't open during cold boot. Interactive
   behavior requires interactive testing (manual or Playwright);
   AppTest is an import-and-render gate only.

---

## Next phase

Run `phase-5.6-reply-target-discovery.md` — promotes `score_reply_
candidates` and `record_reply_target` from stubs to the four-dimension
scoring + deterministic recommended_action resolver per §29. Adds the
`reply_targets` table, the §29.7 Reply Target Queue view, and wires
`posts.in_reply_to_reply_target_id` into the manual-mode "Mark posted"
click-handler.

Phase 5.5 must NOT be extended in the same session — Daniel reviews
the agent surface (system prompt, IWH gate behavior, publish modal
UX) before the reply-target subsystem is layered on top.
