# Architecture

`spec.md` at the repo root is the authoritative source for all architecture and product decisions. This file exists only as a pointer.

For architecture, read:

- **§7.1 Decision** — SQLite + Streamlit + manual entry is the MVP.
- **§7.2 Architecture comparison** — why not spreadsheet / Next.js / Tauri / Electron.
- **§8 System overview** — data flow diagram.
- **§10 Database schema** — every table, constraint, and index.
- **§28 Growth Agent** — Anthropic-powered draft/reply/publish flow with confirmation-gated posting.
- **§29 Reply Target Queue** — first-class reply distribution surface.

If anything in this file ever conflicts with `spec.md`, the spec wins.

---

## Export allowlist contract (Phase 5+)

This contract is load-bearing for Phase 5.5 (Growth Agent) and Phase 5.6
(Reply Target Discovery). Read this section before editing
`app/exports/allowlists.py`.

### Single source of truth

`app/exports/allowlists.py` is the canonical surface for the CSV exporter
column shape. Adding a column to a table does NOT auto-include it in
exports — the allowlist decides. Three lists per table:

- `default_columns` — always included; order is the CSV column order.
- `opt_in_columns` — included only when the caller passes
  `include_opt_in=True`. Header still emits these *after*
  `default_columns` (so opt-in columns ride along at the end, by design).
- `excluded_columns` — documentary; never exported under any flag. A
  collision between this list and either inclusion list is a fail-fast
  `ValueError` from `columns_for_export`.

### How Phase 5.5 extends it

Per `spec.md` §16 (7) and §18 rule 18, the publish-flow migration adds
these to the `posts` table — each goes into one specific list:

| Column                              | List               | Rationale                                                                  |
|-------------------------------------|--------------------|----------------------------------------------------------------------------|
| `agent_draft_id`                    | `default_columns`  | Non-sensitive FK; useful for offline analysis.                             |
| `published_to_x_at`                 | `default_columns`  | Non-sensitive timestamp; matches §16 (7) verbatim allowlist.               |
| `publish_method`                    | `default_columns`  | Enum (`manual` / `api`); non-sensitive.                                    |
| `publish_attempt_count`             | `default_columns`  | Counter; non-sensitive.                                                    |
| `in_reply_to_reply_target_id` (5.6) | `default_columns`  | FK into reply_targets; non-sensitive.                                      |
| `reply_intent` (5.6)                | `default_columns`  | Enum; non-sensitive.                                                       |
| `published_via_agent_message_id`    | `opt_in_columns`   | Joins to `agent_messages` (chat content). §18 rule 18.                     |
| `publish_last_error`                | `excluded_columns` | May carry API error bodies / credential-adjacent strings. §16 (7).         |

The Phase 5.5 / 5.6 prompts must:

1. Append the column to the appropriate list in `POSTS_ALLOWLIST`. The
   file body has `# PHASE 5.5 INSERT HERE` / `# PHASE 5.6 INSERT HERE`
   markers at the exact lines.
2. Add the migration that creates the column with the documented default
   (`publish_attempt_count = 0`, others NULL per `spec.md` §25 Phase 5.5).
3. Extend `tests/test_exports.py` so the schema-existence test still
   passes AND so the opt-in flag actually flips a column on/off for at
   least one column.

### How the counterfactual gate works

`app/exports/markdown_weekly.py::export_weekly_report` reads the
`weekly_reviews` row for the requested ISO week and raises
`CounterfactualMissingError` if `counterfactual_note` is NULL or whitespace-
only. The gate is at the export layer, **not** behind the
`counterfactual_required` settings toggle — that toggle affects only the
form-save path. Export always requires the note because the export is the
artifact a future-Daniel reads back as a causal claim.

### What never goes in any export

- Anything matching `*_token`, `*_key`, `*_secret`, `*_password`,
  `*_credential` (and plural variants) in column name — replaced with
  `[REDACTED]` by the JSON exporter.
- `Authorization` / `X-API-Key` / `Cookie` headers inside
  `raw_api_responses.response_json` and `request_params_json` blobs —
  replaced with `[REDACTED]` by the JSON exporter's recursive walker.
- `stir_testers` (PII) and `stir_conversion_events.qualitative_feedback`
  unless `--include-stir-pii` is explicitly passed.
- `publish_confirmation_tokens` (Phase 5.5) — not in `ALLOWLISTS` at all,
  ever. Tokens are ephemeral security material with no value outside the
  live runtime.

### Audit trail

Every export run inserts a row into `data_exports` (created by
migration `004_data_exports.sql`). The Settings page renders the last
20 rows as a console-log manifest.
