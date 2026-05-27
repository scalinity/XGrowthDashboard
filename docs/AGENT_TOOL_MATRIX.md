# Agent tool matrix (§28.4)

Authoritative tool registry: `app/agent/tools.py` (`AGENT_TOOLS`). Publish tools live in
`app/agent/_internal_tools.py` and are **not** model-visible.

| Tool | Owner | Capability | Gates | Status | Refusal / degraded | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| `query_dashboard_state` | `tools.py` | Read dashboard slices | — | real | — | `tests/test_agent.py` |
| `get_recent_posts` | `tools.py` | Read posts | — | real | missing post filters → empty set | agent tests |
| `get_lane_performance` | `tools.py` | Lane metrics | confidence labels | real | — | agent tests |
| `get_open_hypotheses` | `tools.py` | Experiments | — | real | — | agent tests |
| `get_lane_gaps` | `tools.py` | Coverage gaps | — | real | — | agent tests |
| `analyze_post` | `tools.py` | Single-post analysis | — | real | unknown `post_id` → error dict | agent tests |
| `summarize_winners` | `tools.py` | Winner rollup | sample size | real | insufficient data → empty | agent tests |
| `find_reply_targets` | `tools.py` | Discovery | data_collection_mode=api | real / refused | manual mode refuses X reads | `tests/test_agent_autonomy.py` |
| `score_reply_candidates` | `tools.py` | Score reply targets | lint + niche | real | invalid args → errors[] | service agent tests |
| `extract_lesson` | `tools.py` | Lesson drafting | — | **stub (partial)** | returns context only; `lesson_text` null | — |
| `draft_weekly_review_section` | `review_drafting.py` | Weekly review prose | Anthropic key | **real** | missing key → degraded + manual_fallback | `tests/test_review_drafting.py` |
| `save_draft_post` | `tools.py` | Write draft | niche + lint | real | gate failures → refused | agent tests |
| `save_draft_reply` | `tools.py` | Write reply draft | niche + lint + intent | real | gate failures → refused | agent tests |
| `revise_draft` | `tools.py` | Edit draft | ownership | real | wrong draft → error | agent tests |
| `get_content_type_gaps` | `tools.py` | Content mix | — | real | — | agent tests |
| `score_replier_pool` | `tools.py` | Replier scoring | — | real | — | agent tests |
| `get_velocity_projection` | `tools.py` | Velocity | noise floor | real | — | agent tests |
| `record_reply_target` | `tools.py` | Persist target | — | real | duplicate / invalid → error | agent tests |
| `process_brain_dump` | `tools.py` | Brain dump | Anthropic | real | missing key → degraded | agent tests |
| `analyze_account` | `tools.py` | Account research | — | real | — | agent tests |
| `audit_profile` | `tools.py` | Profile audit | — | real | — | agent tests |
| `draft_monthly_review_section` | `review_drafting.py` | Monthly review prose | Anthropic key | **real** | missing key → degraded | `tests/test_review_drafting.py` |
| `transform_inspiration` | `tools.py` | Inspiration → dict | — | real | — | agent tests |
| `score_inspiration_plagiarism_risk` | `tools.py` | Plagiarism score | Voyage optional | real / degraded | missing embeddings → heuristic | agent tests |
| `analyze_campaign_progress` | `tools.py` | Campaign read | — | real | — | agent tests |
| `outline_blog` | `tools.py` | Blog outline | Anthropic | real | missing key → degraded | agent tests |
| `draft_blog` | `tools.py` | Blog draft | Anthropic | real | missing key → degraded | agent tests |
| `suggest_blog_edits` | `tools.py` | Blog edits | Anthropic | real | missing key → degraded | agent tests |
| `generate_blog_seo_metadata` | `tools.py` | SEO metadata | Anthropic | real | missing key → degraded | agent tests |
| `repurpose_blog_to_x` | `tools.py` | Blog → X | Anthropic | real | missing key → degraded | agent tests |
| `repurpose_x_to_blog_idea` | `tools.py` | X → blog idea | Anthropic | real | missing key → degraded | agent tests |
| `fetch_x_post` | `tools.py` | X read | api mode + xurl | real / refused | manual mode or missing xurl | agent tests |
| `run_local_bash` | `tools.py` | Local shell | autonomy gate | real / refused | disallowed commands → refused | agent tests |
| `query_x_api` | `tools.py` | X API read | api mode + xurl | real / refused | manual mode → refused | agent tests |

## Internal (not in AGENT_TOOLS)

| Tool | Owner | Notes |
| --- | --- | --- |
| `publish_post_to_x` | `_internal_tools.py` | Human confirmation + token only; never exposed to the model |

## Permission summary (`GET /agent/mode`)

- `read_dashboard`: always on
- `read_x_api`: only when `data_collection_mode=api`
- `write_drafts`: on (subject to niche/lint gates inside handlers)
- `publish`: **always false** from chat
- `secrets`: **always false** from chat

Regenerate this table when adding tools; keep in sync with `AGENT_TOOLS` and `tests/test_agent.py`.
