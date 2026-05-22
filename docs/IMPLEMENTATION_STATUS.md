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

`tests/test_agent.py` — 16 Session-1 invariants:

- Tool-registry partitioning (3 tests).
- IWH counter outside agent context (1).
- Six-check confirmation chain (6).
- Atomic publish — validation failure leaves token unconsumed, success
  path stages manual_clipboard, double-publish rejected (3).
- Raw-token redaction — happy path + error path (2).
- Orphan-post detection (1).

`tests/test_agent_session2.py` — 32 Session-2 behaviors:

- Dark-pattern lint offline mode (5).
- Monthly cost ceiling (4).
- IWH `decide_save_or_revise` policy gate (6).
- Prompt drift check + voice/tool injection (5).
- Voice samples CRUD (3).
- Export carve-outs (7).
- Conversation/message persistence helper (1).

Schema test list updated to include `005_agent_core.sql` +
`006_publish_columns.sql`.

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
- [x] `uv run pytest tests/test_agent.py -v` — 16/16 green.
- [x] `uv run pytest -q` — 190/190 green.
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

Commits on `main` for Phase 5.5 (in order):

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
