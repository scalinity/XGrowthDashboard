# Product Spec: Local X Growth Dashboard

## 0. Working title

**X Growth Dashboard**
A local-first "weight-loss dashboard for X growth" that helps Daniel track daily reps, follower trend, content performance, and Stir validation without collapsing everything into one misleading vanity score.

> **Revision note (2026-05-21):** This spec was audited and revised to fix structural tensions in the original. Key changes: the 500k follower number was demoted from operational anchor to long-arc reminder, the Stir validation stream now has equal goal-ladder depth to distribution, the content taxonomy was collapsed to a minimum-viable set with a documented v2 expansion path, manual data entry replaced API-first collection as the MVP default, and a new "Next Rep" view was added between Today and Progress. See §30 for the full changelog.
>
> **Scope clarification (same day):** This is a **personal local tool**. Single user (Daniel), single machine, never distributed. It is not destined for the App Store, not packaged for download, not shared with collaborators. Sections originally hedging toward desktop distribution, multi-user support, or cloud sync have been removed. The "production-minded" practices that remain — immutable snapshots, `VACUUM INTO` backups, schema discipline, raw response preservation — stay because they protect Daniel's data and learning, not because they prepare for distribution.
>
> **Growth Agent addition (same day):** A Claude-powered Growth Agent has been added as a first-class component of the app. It drafts posts and replies, analyzes content, surfaces reply targets, and posts to X with explicit two-step confirmation. The agent is integrated throughout the existing views (Today, Next Rep, Content Performance, Weekly Review) and has a dedicated chat view at §14.8. The full agent specification — system prompt, tool functions, data model, confirmation flow, failure modes — is in §28. The existing Changelog has been renumbered to §29.
>
> **Reply Target Discovery addition (same day):** Replies are now treated as a first-class distribution surface, not generic daily reps. A reply is a small post inserted into someone else's attention pool; the comment-section audience is the real reader, not the target author. A new §29 codifies the workflow: candidate discovery, four-dimension scoring, deterministic recommended action, a dedicated Reply Target Queue view, integration with §28.4 agent tools #6 and #7, and a V1.1+ thread-classifier lint pass mirroring the §28.2 dark-pattern lint. The Changelog has been renumbered to §30; see item 68 for the full delta.

---

## 1. Product thesis

The dashboard is not a social media "analytics dashboard" in the generic sense. It is a **behavior + trend + validation system** — a personal local tool, single user, never distributed.

The core job:

> Make it obvious whether Daniel is doing the daily distribution reps, whether those reps are moving X growth over time, and whether any of that growth is converting into real Stir validation.

The most important product decision: **separate distribution growth from product validation, and give them equal structural weight in the goal hierarchy.**

A follower from the AI/builder world can help reach and social proof. A working parent/home cook who downloads Stir, scans their kitchen, gets usable dinner options, and tries Cook Mode is stronger product-validation signal. The dashboard should never compress those into a single "success score" — and the goal hierarchy should not anchor on one stream while leaving the other open-ended.

---

## 2. Current starting state

### Account

| Field                                  |             Value |
| -------------------------------------- | ----------------: |
| X handle                               |   `@dannyscalant` |
| Baseline followers before serious push |                61 |
| Current followers after first push     |                64 |
| Following                              |               351 |
| Total posts                            |                57 |
| Listed count                           |                 0 |
| Likes count                            |               214 |
| Media count                            |                 3 |
| Current milestone                      |     100 followers |
| Operational ceiling                    |   5,000 followers |
| Long-arc reminder                      | 500,000 followers |

**Why two numbers for the long-term picture:**

The original spec anchored on 500,000 as the long-term goal, then acknowledged it was "too large for daily interpretation" and tried to mitigate via rolling windows. That created a hidden contradiction: the dashboard's job is to separate distribution from validation, but the goal hierarchy gave the distribution stream a 10-rung ladder while the validation stream got one rung ("first 5 downloads"). The dashboard would subtly pressure toward the only stream with structure.

The fix: split the long-term number into an **operational ceiling** (the follower count beyond which growth stops mattering for Stir validation — somewhere in the 2k-5k range; we use 5k as a conservative cap) and a **long-arc reminder** (500k, kept visible as identity/ambition but never used for operational decisions). Daily and weekly views show the operational ladder. The 500k number appears only in the long-arc footer of the Progress view, with no progress bar attached.

### Current progress

| Goal                                         | Current |      Remaining | Notes                                       |
| -------------------------------------------- | ------: | -------------: | ------------------------------------------- |
| 61 → 100 followers                           |      64 |             36 | +3 from baseline                            |
| Operational ceiling (5k)                     |      64 |          4,936 | Replaces 500k for daily interpretation      |
| First 5 Stir downloads this week             | unknown | track manually | Distribution and validation tracked apart   |
| First working-parent/home-cook tester from X | unknown |       1 needed | Stronger validation milestone than downloads |

### Current bio

> Building Stir — AI that turns "what's for dinner?" into 3 cookable options in 60s.
>
> Pre-Master's @ UF AI in Biomedicine.
> Long arc: neuro-oncology AI.

---

## 3. External assumptions verified

The X API can expose user public metrics such as `followers_count`, `following_count`, `tweet_count`, and `listed_count`, so account snapshots can be API-backed when credentials and pricing allow it. ([X Developer Platform][1])

The X API post payload supports public post metrics including repost/retweet count, reply count, like count, quote count, impression count, and bookmark count. ([X Developer Platform][1])

Some richer metrics, such as `user_profile_clicks`, `url_link_clicks`, and total `engagements`, appear under non-public metrics and require user-context authentication. The app should label those as unavailable unless explicitly returned by authenticated API calls. ([X Developer Platform][1])

The X API supports reading posts, publishing content, managing users, and analytics/engagement data at a platform level; X API v2 is the recommended current version in the official docs. ([X Developer Platform][2])

The create-post endpoint is `POST /2/tweets`; this can create posts for the authenticated user, but the dashboard should not make posting automation part of the MVP unless Daniel explicitly chooses to add it. ([X Developer Platform][3])

The X developer guidelines allow standard read/search use within limits, but automated replies are only allowed when the user engaged first, and non-API scraping/browser automation is explicitly prohibited. Therefore, manual cold-reply tracking is a first-class requirement, not a workaround. ([X Developer Platform][4])

`xurl` is an official X API CLI with OAuth handling and can be used to prototype authenticated API calls without manually signing requests. This makes it a good upgrade path once the manual loop has proven the dashboard's value. ([X Developer Platform][5])

X API pricing is pay-per-usage and endpoint-dependent, and current tier minimums make API-first collection a poor fit for an MVP tracking a single account at low post volume. **Verify current X API pricing before committing to any API-based collection path** — pricing has changed multiple times and the tier structure is non-obvious. ([X Developer Platform][6])

SQLite is a strong fit for the local database because it is serverless, zero-configuration, transactional, and stores a complete database in a single disk file. ([SQLite][7])

Streamlit can run a local web app from a normal Python script, opening it in the browser with `streamlit run`; it also supports SQL connections and cached read-only queries via `st.connection`. ([Streamlit Docs][8])

Tauri is attractive for a later packaged desktop app because it uses web UI with a Rust-backed native shell and system webview, but it adds build/setup complexity that does not help the first-week distribution loop. ([Tauri][9])

Electron is a mature cross-platform desktop option for JavaScript/HTML/CSS apps, but it ships Chromium and Node.js in the binary, which is heavier than needed for this single-user dashboard MVP. ([Electron][10])

Next.js is a strong full-stack React option and can run locally at `localhost:3000`, but it adds more frontend/backend surface area than the MVP needs. ([Next.js][11])

---

## 4. Product goals

### Primary goals

1. **Daily weigh-in**

   * Capture follower count and account metrics at the same time each day.
   * Show change since yesterday, since baseline, and distance to next milestone.
   * Keep the tone calm: trend, not panic.

2. **Rep adherence**

   * Track whether daily posting/reply targets were completed.
   * Show consistency before showing conclusions.
   * Treat high-quality reply sessions like workouts.

3. **Content learning**

   * Track posts/replies by pillar, audience, CTA, and hypothesis.
   * Show which content lanes appear to create reach, saves/bookmarks, replies, or conversions.
   * Avoid ranking a content lane until sample size and time coverage justify it.

4. **Dual milestone progress**

   * **Distribution ladder**: 61 → 100 → 250 → 500 → 1k → 2.5k → 5k (operational ceiling).
   * **Validation ladder**: equal depth — 7 rungs from "first download" to "first 5 Cook Mode completions in a week."
   * 500k remains visible as long-arc reminder, never as an operational target.

5. **Stir validation**

   * Track site visits, downloads, signups, kitchen scans, "3 plausible dinners" events, Cook Mode usage, and qualitative feedback.
   * Validation ladder is structurally equal to distribution ladder, not a side metric.

6. **Weekly interpretation**

   * Produce a weekly markdown postmortem:

     * what moved,
     * what got stuck,
     * best/worst post,
     * strongest/weakest pillar,
     * follower delta,
     * Stir validation,
     * next experiment,
     * explicit acknowledgment that growth has counterfactual baseline that cannot be measured here.

---

## 5. Non-goals

The MVP should **not** do the following:

1. **Do not auto-reply or auto-post without per-action confirmation.**

   * The Growth Agent can publish posts and replies to X, but ONLY after Daniel clicks an explicit confirmation for each individual post.
   * No batch publishing. No "approve all." Each post requires a separate confirmation click.
   * Auto-replying into third-party conversations is still prohibited — the agent surfaces reply candidates and drafts replies, but each publish is confirmed individually.
   * Track manual replies in `posts` table same as before.
   * Optionally help plan reply sessions.

2. **Do not claim causal attribution from noisy data.**

   * A post followed by follower growth is not proof that the post caused it.
   * The dashboard can say "associated with," not "caused by," unless attribution is explicit.
   * The weekly review explicitly acknowledges a counterfactual baseline (platform drift, cohort effect, day-of-week) cannot be measured by this tool.

3. **Do not build a generic social scheduler first.**

   * Posting automation is not the bottleneck.
   * The bottleneck is consistent reps + learning which lanes convert.

4. **Do not collapse Stir validation into follower growth.**

   * +20 AI-builder followers and 1 working-parent tester are different kinds of wins.

5. **Do not chase UI polish before instrumentation.**

   * A clean Streamlit dashboard is enough for MVP.
   * Tauri/Electron packaging can wait.

6. **Do not scrape X.**

   * Use official API/xurl/manual entry.
   * Manual entry is the MVP default, not a fallback.

7. **Do not overspecify taxonomy before data justifies it.**

   * Start with 3 pillars, 2 audiences, 2 CTAs.
   * Expand only when post volume in a category supports discrimination.

---

## 6. Product metaphor

### Weight-loss / fitness dashboard mapping

| Fitness dashboard concept | X Growth Dashboard equivalent                       |
| ------------------------- | --------------------------------------------------- |
| Scale weight              | Daily follower count                                |
| Goal weight (vanity)      | 500k followers — visible reminder, never operational |
| Operational goal weight   | Operational ceiling, currently 5k                    |
| Current target weight     | Next milestone, currently 100                       |
| Calorie/protein adherence | Daily post/reply rep completion                     |
| Workouts                  | High-quality reply sessions                         |
| Step count                | Replies shipped / posts shipped                     |
| Progress photos           | Screenshots, examples, qualitative signal           |
| Body measurements         | Profile visits, link clicks, downloads, ICP testers |
| Trend line                | 7-day / 30-day follower trend                       |
| Weekly check-in           | Growth postmortem                                   |
| Diet experiment           | Content-lane experiment                             |
| Water retention/noise     | Noisy daily follower movement                       |

### Design principle

The UI should make daily follower count visible, but not emotionally dominant.
The dominant question should be:

> Did I do the reps, and what did the market teach me?

---

## 7. Recommended architecture

## 7.1 Decision

**Recommended MVP:**

* **SQLite** for local-first storage.
* **Streamlit + `st.connection`** for the local dashboard UI with built-in query caching. Connection setup MUST execute `PRAGMA foreign_keys = ON` on every connection — SQLite disables FK enforcement by default, and the spec relies on `ON DELETE SET NULL` for `posts.published_via_agent_message_id` and `agent_messages.resulted_in_published_post_id` (§10.2). Without this PRAGMA, FK behaviors documented in the schema silently no-op.
* **Manual entry as the default data collection path** — daily snapshot form is pinned to the top of the Today view.
* **`xurl` as the V1.1 upgrade path** once the manual loop has proven the dashboard's value.
* **Direct X API client deferred to V1.2** when cost justifies it.
* **Python scripts** for batch operations (export, weekly report generation) but not as the primary daily data path.
* **`cron`/`launchd` only when a real API integration exists** — there is nothing to schedule when collection is manual.
* **CSV export** for portability.
* **Markdown weekly report export** for Obsidian.
* **`VACUUM INTO` for backups** (safe while DB is open; `cp` of an open SQLite file is unsafe).

### Why this is the better path

The conventional path is two-step wrong: people either build a polished Next.js/Tauri/Electron desktop app first (months of yak-shaving) or they reach immediately for the X API (real cost floors, OAuth complexity, rate limits) for a use case that doesn't justify either.

The learning loop is the product. The fastest useful system is:

```text
daily manual snapshot (30 seconds)
→ manual post/reply logging during the day
→ trend interpretation in the dashboard
→ weekly experiment decision
```

Streamlit + SQLite + manual entry gets that working with the least surface area. Once the dashboard proves useful for 2–4 weeks and the data load justifies it, layer in `xurl` for snapshot automation. The data model already supports both — manual and API rows live in the same tables with explicit `source` and `data_quality` columns.

### Why not a spreadsheet?

The honest alternative-to-build isn't Next.js; it's a Google Sheet + Looker Studio. That would be operational in 2 hours instead of a week. But the spec's most valuable property — enforced separation of distribution and validation, immutable snapshots with corrections, sample-size warnings baked into the SQL views, structured weekly review export — is exactly the kind of discipline that disappears in a spreadsheet. The schema is part of the product. Building it in SQLite is worth the extra time precisely because the constraints are load-bearing.

---

## 7.2 Architecture comparison

| Option                          | Pros                                                                                                           | Cons                                                                                | Fit                                 |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------- |
| **SQLite + Python + Streamlit (manual-first)** | Fastest MVP, zero auth/cost, schema enforces product discipline, easy CSV/Markdown export, natural upgrade path to API | Less polished than custom React; Streamlit UX has limits                            | **Best fit for personal local tool** |
| Google Sheet + Looker Studio    | 2-hour setup, zero engineering                                                                                  | Schema discipline disappears; no immutable snapshots, no enforced sample-size logic | Acceptable if you don't care about the discipline |
| Next.js + SQLite                | Better frontend, strong TS/React ergonomics                                                                    | More boilerplate, API routes/server actions/ORM choices, slower MVP                 | Only if Streamlit UX feels too limiting after months of use |

Tauri, Electron, FastAPI+React, and other desktop-packaging or production-shaped options were considered and dropped — this tool runs as `streamlit run` on Daniel's machine and that's the entire deployment story.

### Recommendation

Build the MVP as:

```text
x_growth_dashboard/
  data/
    x_growth.sqlite
    backups/                 # VACUUM INTO targets, dated
    raw_api/                 # empty until V1.1
    exports/
    weekly_reports/
  scripts/
    backup_db.py             # VACUUM INTO with date suffix
    export_weekly_report.py
    collect_account_snapshot.py   # stub until V1.1
    collect_recent_posts.py       # stub until V1.1
    refresh_post_metrics.py       # stub until V1.1
  app/
    db.py                    # st.connection wrapper + schema bootstrap
    streamlit_app.py
    views/
      today.py
      next_rep.py            # NEW — see §14.2
      progress.py
      content.py
      funnel.py
      weekly_review.py
      settings.py
  migrations/
    001_initial.sql
    002_taxonomy_v1.sql      # placeholder for v2 expansion later
  config/
    settings.toml
    content_pillars.yaml     # v1 minimum, with v2 documented inline
    utm_templates.yaml
  .env
  README.md
```

The first implementation should run with:

```text
streamlit run app/streamlit_app.py
```

Target local URL:

```text
http://localhost:8501
```

Database access pattern (specify this so it doesn't drift):

```python
# app/db.py
import streamlit as st

conn = st.connection("x_growth", type="sql", url="sqlite:///data/x_growth.sqlite")
# Views and read queries: conn.query("SELECT ...", ttl=60)
# Writes: with conn.session as s: s.execute(...); s.commit()
```

Backups (run via cron or manually):

```bash
sqlite3 data/x_growth.sqlite "VACUUM INTO 'data/backups/x_growth_$(date +%Y%m%d_%H%M%S).sqlite'"
```

---

## 8. System overview

```text
                         ┌─────────────────────────┐
                         │  Manual entry forms     │
                         │  (MVP default path)     │
                         │  • daily snapshot       │
                         │  • posts / replies      │
                         │  • Stir events          │
                         └───────────┬─────────────┘
                                     │
                         ┌───────────┴─────────────┐
                         │  xurl / X API (V1.1+)   │
                         │  same tables, source=api │
                         └───────────┬─────────────┘
                                     │
                                     v
┌──────────────────┐       ┌────────────────────┐       ┌────────────────────┐
│ Corrections      │ ----> │ SQLite local DB     │ ----> │ Streamlit dashboard │
│ audit-trail only │       │ immutable snapshots │       │ localhost:8501      │
└──────────────────┘       │ raw responses       │       │                    │
                           └─────────┬──────────┘       └─────────┬──────────┘
                                     │                              │
                                     v                              v
                         ┌────────────────────┐        ┌─────────────────────┐
                         │ CSV / JSON export   │        │ Weekly MD export     │
                         │ backup / analysis   │        │ Obsidian postmortem  │
                         └────────────────────┘        └─────────────────────┘
```

---

## 9. User stories

### Daily operator stories

1. As Daniel, I want to open a local dashboard each morning and see my current X follower count, yesterday's delta, baseline delta, and distance to 100 followers.
2. As Daniel, I want the app to remind me that daily follower movement is noisy so I do not overreact to +1 or -1.
3. As Daniel, I want a daily checklist showing whether I completed my minimum reps.
4. As Daniel, I want to log manual replies because many high-value replies happen directly on X, not through the API.
5. As Daniel, I want to record why I posted something so I can learn from the result instead of just seeing likes.
6. As Daniel, I want the dashboard to tell me which content lane is most under-sampled so I know where to focus today's reps. **(Next Rep view, §14.2)**

### Content learning stories

7. As Daniel, I want to tag each post/reply by pillar, audience, CTA, and hypothesis.
8. As Daniel, I want to filter posts by lane.
9. As Daniel, I want to see which content lanes get impressions, replies, bookmarks, and profile/link clicks — with uncertainty made visible (CI or IQR), not hidden behind point estimates.
10. As Daniel, I want the dashboard to refuse to rank lanes when sample size doesn't support discrimination, while still showing the underlying data.
11. As Daniel, I want to compare standalone posts vs replies because early growth may come more from replies.

### Stir validation stories

12. As Daniel, I want to track whether X activity leads to getstir.app visits.
13. As Daniel, I want to track the first 5 downloads separately from follower growth.
14. As Daniel, I want to identify whether a download came from a likely working parent/home cook or from an AI/builder follower — but only when self-reported, not inferred.
15. As Daniel, I want to track whether a tester scanned a kitchen, got 3 plausible dinners, and used Cook Mode.
16. As Daniel, I want qualitative feedback visible next to quantitative funnel data.
17. As Daniel, I want the validation milestone ladder to feel as structurally important as the follower ladder.

### Weekly review stories

18. As Daniel, I want a weekly review page that asks the same questions every week.
19. As Daniel, I want the app to suggest candidate best/worst posts, but I choose the final interpretation.
20. As Daniel, I want to export the weekly review to Markdown for Obsidian.
21. As Daniel, I want to define next week's experiment and measure it against a simple success criterion.
22. As Daniel, I want to separate "I did not do the reps" from "I did the reps and the strategy did not work."
23. As Daniel, I want the weekly review to acknowledge that growth has a counterfactual baseline (platform drift, cohort) that this tool cannot measure.

### Growth Agent stories

24. As Daniel, I want a Claude-powered agent inside the app that can draft posts and replies for me — not to replace my voice, but to compress the slowest step in the distribution loop. **(Growth Agent, §28)**
25. As Daniel, I want every draft the agent produces to clear three bars — intelligence, wisdom, humility — so the work that ships under my name is something I'd still be proud of in a year.
26. As Daniel, I want the agent to know my niche (Stir, working-parent ICP, build-in-public, neuro-oncology long arc) so I don't have to re-explain it every session.
27. As Daniel, I want the agent to surface which posts I should reply to based on under-sampled lanes and target accounts I care about. **(Agent tool: `find_reply_targets`)**
28. As Daniel, I want the agent to be reachable from inline buttons in Today, Next Rep, Content Performance, and Weekly Review — not just a separate chat. **(§28.7 integration points summary)**
29. As Daniel, I want a dedicated Agent Chat view for open-ended strategy conversations that persist across sessions. **(§14.8)**
30. As Daniel, I want every post the agent helps draft to require my explicit two-step confirmation before going live — even when the X API is connected. **(§28.10 publish flow)**
31. As Daniel, I want the agent's tool calls to be visible and inspectable in the chat — I should be able to see what data it pulled and why it suggested what it suggested.
32. As Daniel, I want the agent's behavior optimized using real psychology-of-engagement principles, not generic AI-generated-content patterns that scream "I asked an LLM."

---

## 10. Data model

The database should be local SQLite.

### 10.1 Design rules

1. **Raw snapshots are immutable.**

   * Do not overwrite historical account snapshots or post metric snapshots.
   * Corrections are stored separately.

2. **Stock metrics and flow metrics are separate.**

   * Stock: follower count at a point in time.
   * Flow: posts shipped, replies shipped, downloads during a day/week.

3. **Computed metrics are not stored unless needed for caching.**

   * Deltas, rolling averages, rates, milestone progress should usually be views/calculations.

4. **Every metric should know its source.**

   * `manual`, `xurl`, `api`, `csv_import`, `analytics_import`, `computed`.
   * `manual` is the default for MVP; others are upgrade paths.

5. **Inferred metrics must be labeled.**

   * Example: "follower conversions from this post" is usually inferred unless explicitly attributed.

6. **Sensitive attributes are only stored when self-reported.**

   * No `inferred_*` confidence for working-parent / home-cook classification.
   * If the tester hasn't told you they're a working parent, the column is null.

---

### 10.2 Tables

### `settings`

Stores global app configuration.

| Column       | Type             | Notes                                 |
| ------------ | ---------------- | ------------------------------------- |
| `key`        | text primary key | e.g. `x_handle`, `baseline_followers` |
| `value_json` | text             | JSON-encoded value                    |
| `updated_at` | datetime         | local or UTC                          |
| `note`       | text nullable    | optional                              |

Required initial settings:

```text
x_handle = "dannyscalant"
x_user_id = null                  # populated once known; future-stable identifier
profile_url = "https://x.com/dannyscalant"
baseline_followers = 61
operational_ceiling = 5000        # replaces 500k as operational anchor
long_arc_reminder = 500000        # displayed in long-arc footer only
current_milestone = 100
timezone = "America/New_York"
daily_snapshot_time = "09:00"
daily_post_target = 1
daily_reply_target = 12           # raised from 5; see §10.2 daily_activity defaults
daily_reply_session_target = 1
target_calibration_review_date = baseline + 21 days
weekly_report_export_path = configurable
data_collection_mode = "manual"   # manual | xurl | api

# Growth Agent (see §28)
agent_enabled = true
agent_api_provider = "anthropic"                       # only option for MVP; subprocess path documented in §28
agent_model_high_stakes = "claude-opus-4-7"            # post drafting, reply scoring, complex reasoning
agent_model_iteration = "claude-sonnet-4-6"            # reply drafting, lesson extraction, most iteration
agent_model_quick = "claude-haiku-4-5-20251001"        # weekly review section drafts, quick categorization
agent_system_prompt_path = "config/agent_system_prompt.md"
agent_cost_cap_monthly_usd = 25.00
agent_cost_alert_threshold_pct = 80
agent_default_max_tokens = 2000
agent_voice_sample_count = 5                           # top N active voice samples in system prompt
agent_reply_target_default_expiry_hours = 24           # X conversation freshness is short

# X API posting — compile-time CONSTANTS (NOT editable via settings table)
# These four label the env vars where OAuth credentials live. They are
# indirection labels, not editable runtime values. Implemented as Python
# constants in app/x_client.py.
x_api_consumer_key_env = "X_API_CONSUMER_KEY"      # OAuth 1.0a; in .env
x_api_consumer_secret_env = "X_API_CONSUMER_SECRET"
x_api_access_token_env = "X_API_ACCESS_TOKEN"      # user-context posting
x_api_access_token_secret_env = "X_API_ACCESS_TOKEN_SECRET"

# Security invariant — compile-time constant in app/agent/confirmation.py:
#   CONFIRMATION_REQUIRED = True
# This is NOT stored in the settings table. Storing it in `settings` would
# make the per-action confirmation gate bypassable via a single
# `UPDATE settings SET value_json = 'false' WHERE key = ...'` — defeating
# §28.2 rule #10's "non-bypassable" promise. The publish tools read the
# constant directly from code; there is no DB toggle.

# X API posting — EDITABLE settings (rows in the settings table)
x_posting_enabled = false                                   # opt-in in Settings
x_posting_rate_limit_per_hour = 10                          # safety bound (sliding 3,600s window); 50/day cap binds first after ~5h max-rate
x_posting_rate_limit_per_day = 50                           # ~3-4x steady-state daily target (12 replies + 1 post); safety bound, not productivity gate
x_posting_confirmation_token_ttl_seconds = 60               # short enough to prevent stale confirmations, long enough for thoughtful publish flow
x_posting_publish_retry_attempts_per_token = 2              # bounded internal retry on transient X API errors (5xx, rate-limit) — one confirmation → at most one live X post
x_posting_audit_export_requires_explicit_action = true      # §16 carve-out: agent_messages / agent_tool_calls require their own opt-in export

# IWH (intelligence / wisdom / humility) enforcement
iwh_max_revision_attempts = 3                               # orchestrator (not agent) tracks revisions; refuse after this many failures
iwh_self_score_minimum = 2                                  # per-quality threshold (0-3 scale); below this triggers revision

# X format guidance — centralized so prompt + lint pass agree
x_short_post_target_chars = 200                             # aspirational ceiling for standalone "one strong idea" posts
x_post_max_chars = 280                                      # hard X platform ceiling
```

---

### `account_snapshots`

Immutable daily or ad hoc X account snapshots.

| Column               | Type                | Notes                                               |
| -------------------- | ------------------- | --------------------------------------------------- |
| `id`                 | integer primary key |                                                     |
| `snapshot_date`      | date                | local date                                          |
| `collected_at_utc`   | datetime            | exact collection timestamp                          |
| `x_user_id`          | text nullable       | stable across handle changes; preferred join key   |
| `username`           | text                | `dannyscalant`                                      |
| `profile_url`        | text                |                                                     |
| `followers_count`    | integer             |                                                     |
| `following_count`    | integer             |                                                     |
| `post_count`         | integer             |                                                     |
| `listed_count`       | integer             |                                                     |
| `like_count`         | integer nullable    |                                                     |
| `media_count`        | integer nullable    |                                                     |
| `bio_text`           | text nullable       |                                                     |
| `baseline_followers` | integer             |                                                     |
| `source`             | enum                | `manual`, `xurl`, `api`, `csv_import`               |
| `data_quality`       | enum                | `exact`, `manual`, `estimated`, `partial`, `failed` |
| `raw_response_id`    | integer nullable    |                                                     |
| `created_at`         | datetime            |                                                     |

Indexes:

```text
unique(x_user_id, collected_at_utc) where x_user_id is not null
unique(username, collected_at_utc) where x_user_id is null
index(snapshot_date)
index(x_user_id, snapshot_date)
```

Do **not** make `snapshot_date` unique. Multiple snapshots in one day can happen, but the dashboard should designate one as the canonical daily snapshot.

Note: the unique index prefers `x_user_id` to handle the case where the handle changes. Until `x_user_id` is known, fall back to `username`.

---

### `account_snapshot_corrections`

Manual corrections without mutating the original snapshot.

| Column        | Type                | Notes |
| ------------- | ------------------- | ----- |
| `id`          | integer primary key |       |
| `snapshot_id` | integer foreign key |       |
| `field_name`  | text                |       |
| `old_value`   | text                |       |
| `new_value`   | text                |       |
| `reason`      | text                |       |
| `created_at`  | datetime            |       |

Example:

```text
snapshot_id = 12
field_name = "followers_count"
old_value = "63"
new_value = "64"
reason = "Manual correction from X profile screenshot"
```

---

### `raw_api_responses`

Preserves raw responses for auditability. Empty until V1.1 (xurl/API).

| Column                  | Type                | Notes                                                              |
| ----------------------- | ------------------- | ------------------------------------------------------------------ |
| `id`                    | integer primary key |                                                                    |
| `source`                | enum                | `x_api`, `xurl`, `website_analytics`, `app_store`, `manual_import` |
| `endpoint_or_command`   | text                |                                                                    |
| `request_params_json`   | text nullable       |                                                                    |
| `response_json`         | text                |                                                                    |
| `status_code`           | integer nullable    |                                                                    |
| `collected_at_utc`      | datetime            |                                                                    |
| `request_cost_estimate` | real nullable       |                                                                    |
| `notes`                 | text nullable       |                                                                    |

Privacy rule: never store secrets, bearer tokens, OAuth tokens, or API keys here.

---

### `posts`

One row per known post/reply/quote/thread root.

| Column                       | Type                 | Notes                                                         |
| ---------------------------- | -------------------- | ------------------------------------------------------------- |
| `id`                         | integer primary key  |                                                               |
| `x_post_id`                  | text unique nullable | nullable for manual draft/pending entry                       |
| `created_at_utc`             | datetime nullable    |                                                               |
| `created_date`               | date                 |                                                               |
| `text`                       | text                 |                                                               |
| `url`                        | text nullable        |                                                               |
| `type`                       | enum                 | `standalone`, `reply`, `quote`, `thread_root`, `thread_child` |
| `conversation_id`            | text nullable        |                                                               |
| `in_reply_to_post_id`        | text nullable        |                                                               |
| `in_reply_to_user`           | text nullable        |                                                               |
| `posted_via`                 | enum                 | `manual`, `xurl`, `api`, `imported`, `agent_assisted`, `unknown`                |
| `manual_confirmation_status` | enum                 | `confirmed`, `needs_id`, `needs_metrics`, `draft`             |
| `contains_link`              | boolean              |                                                               |
| `expanded_urls_json`         | text nullable        |                                                               |
| `utm_source`                 | text nullable        |                                                               |
| `utm_medium`                 | text nullable        |                                                               |
| `utm_campaign`               | text nullable        |                                                               |
| `utm_content`                | text nullable        |                                                               |
| `utm_term`                   | text nullable        |                                                               |
| `raw_response_id`            | integer nullable     |                                                               |
| `created_in_app_at`          | datetime             |                                                               |
| `published_to_x_at`          | datetime nullable    | when successfully published to X                              |
| `publish_method`             | enum                 | `agent_confirmed`, null. Set to `agent_confirmed` only when a publish via `publish_post_to_x` or `publish_reply_to_x` succeeds. `null` for all other rows (drafts, imports, manual-mode posts logged after-the-fact). Provenance for non-agent publishes is tracked via `posted_via`. |
| `published_via_agent_message_id` | integer fk nullable | links to `agent_messages.id` when published from agent flow; ON DELETE SET NULL |
| `publish_attempt_count`      | integer default 0    | tracks retries (including the successful attempt); reset is intentional — left at the total. Migration: existing rows backfill to 0. |
| `publish_last_error`         | text nullable        | last publish error if any; cleared (set to NULL) on a subsequent successful publish |
| `agent_draft_id`             | int nullable         | foreign key to `agent_drafts.id` if this post originated from an agent draft |

Indexes:

```text
index(created_date)
index(type)
index(conversation_id)
index(utm_campaign)
```

---

### `post_metric_snapshots`

Metric snapshots over time. One post can have many metric snapshots.

| Column              | Type                | Notes                                     |
| ------------------- | ------------------- | ----------------------------------------- |
| `id`                | integer primary key |                                           |
| `post_id`           | integer foreign key |                                           |
| `x_post_id`         | text                |                                           |
| `collected_at_utc`  | datetime            |                                           |
| `impressions`       | integer nullable    |                                           |
| `likes`             | integer nullable    |                                           |
| `replies`           | integer nullable    |                                           |
| `reposts`           | integer nullable    |                                           |
| `quotes`            | integer nullable    |                                           |
| `bookmarks`         | integer nullable    |                                           |
| `engagements_total` | integer nullable    | exact from API only; never store a sum here |
| `profile_clicks`    | integer nullable    |                                           |
| `url_link_clicks`   | integer nullable    |                                           |
| `source`            | enum                | `manual`, `xurl`, `api`, `csv_import`     |
| `data_quality`     | enum                | `exact`, `manual`, `estimated`, `partial` |
| `raw_response_id`   | integer nullable    |                                           |

Computed from latest snapshot:

```text
engagement_rate
bookmark_rate
reply_rate
profile_click_rate
link_click_rate
```

---

### `post_classifications`

Content metadata and learning notes.

| Column            | Type                | Notes                   |
| ----------------- | ------------------- | ----------------------- |
| `id`              | integer primary key |                         |
| `post_id`         | integer foreign key |                         |
| `pillar`          | text                | enum-with-room-to-grow (see v1 set below) |
| `audience`        | text                | enum-with-room-to-grow |
| `cta`             | text                | enum-with-room-to-grow |
| `quality_score`   | integer nullable    | optional 1–5 subjective |
| `why_posted`      | text nullable       |                         |
| `hypothesis`      | text nullable       |                         |
| `expected_signal` | text nullable       |                         |
| `actual_signal`   | text nullable       |                         |
| `lesson`          | text nullable       |                         |
| `classified_at`   | datetime            |                         |
| `updated_at`      | datetime            |                         |

#### v1 (MVP) taxonomy — minimum viable

The original spec specified 9 × 6 × 8 = 432 possible classifications. At ~30 posts/month that matrix cannot populate densely enough to support discrimination. v1 starts at the minimum that supports the questions you actually need answered:

**Pillars (v1, 3 values):**

```text
stir        — anything centered on Stir, the product, the pain it solves, dogfooding it
build       — founder-from-zero, build-in-public, taste, process, postmortems
self        — anything personal: identity, neuro-oncology long arc, AI/biomed angle
```

**Audiences (v1, 2 values):**

```text
icp         — working parent / home cook, real or self-identified
other       — everyone else (AI builders, founders, broad)
```

**CTAs (v1, 2 values):**

```text
ask         — there is an explicit call to action (link, reply, follow, download)
none        — pure signal, no ask
```

That's 3 × 2 × 2 = 12 cells. Realistic to populate in 4-6 weeks.

#### v2 expansion (documented, NOT implemented yet)

When the data justifies finer resolution — guideline: any v1 pillar has 15+ posts and the within-pillar variance suggests subcategories matter — expand to:

```text
v2 pillars:
  stir_pain
  stir_product_insight
  build_in_public
  taste_process
  postmortem
  ai_biomed_neuro_oncology_long_arc
  direct_ask

v2 audiences:
  working_parent_home_cook_icp
  ai_builders
  founders_builders
  biomed_healthcare
  broad_general

v2 ctas:
  none
  profile
  link
  reply_keyword
  dm
  download_test_ask
```

The schema stores these as `text` rather than rigid enum so v2 is a config change, not a migration. The UI seeds dropdowns from `config/content_pillars.yaml` which starts with v1 values; v2 values get added when ready.

---

### `daily_activity`

Daily reps and behavior tracking.

| Column                             | Type             | Notes |
| ---------------------------------- | ---------------- | ----- |
| `activity_date`                    | date primary key |       |
| `planned_posts`                    | integer          |       |
| `planned_replies`                  | integer          |       |
| `planned_quotes`                   | integer          |       |
| `posts_shipped`                    | integer          |       |
| `replies_shipped`                  | integer          |       |
| `quotes_shipped`                   | integer          |       |
| `high_quality_reply_targets_found` | integer          |       |
| `reply_sessions_completed`         | integer          |       |
| `minimum_reps_completed`           | boolean          |       |
| `time_spent_minutes`               | integer nullable |       |
| `manual_actions_count`             | integer nullable |       |
| `api_actions_count`                | integer nullable |       |
| `avoidance_notes`                  | text nullable    |       |
| `daily_note`                       | text nullable    |       |
| `created_at`                       | datetime         |       |
| `updated_at`                       | datetime         |       |

#### Initial daily targets — explicitly experimental

The original spec defaulted to 5 replies/day. For an account at 64 followers, that is plausibly too low to escape the noise floor — much of the growth literature for sub-1k accounts puts the breakout threshold somewhere in the 10-30 replies/day range, though the evidence is folk-knowledge, not rigorous.

The MVP defaults below treat reply targets as a 21-day calibration experiment. Adherence data after 3 weeks tells you whether to raise (you hit consistently and want more signal), lower (you're missing days because the target is unsustainable), or hold (you hit reliably and growth is moving).

```text
minimum_posts_per_day        = 1
minimum_replies_per_day      = 12     # raised from 5 — see calibration note
minimum_reply_sessions_per_day = 1
calibration_review_date      = baseline + 21 days
```

The Settings view shows a "Calibrate reply target" prompt on the calibration review date. The intent is to make this a deliberate adjustment driven by adherence data, not a quiet drift.

---

### `reply_sessions`

One row per deliberate reply "workout."

| Column                  | Type                | Notes                      |
| ----------------------- | ------------------- | -------------------------- |
| `id`                    | integer primary key |                            |
| `session_date`          | date                |                            |
| `started_at`            | datetime nullable   |                            |
| `duration_minutes`      | integer nullable    |                            |
| `target_lane`           | text                | same as pillar or audience |
| `target_accounts_json`  | text nullable       |                            |
| `targets_found`         | integer             |                            |
| `replies_shipped`       | integer             |                            |
| `best_reply_post_id`    | integer nullable    |                            |
| `session_quality_score` | integer nullable    |                            |
| `notes`                 | text nullable       |                            |

---

### `stir_conversion_events`

Event-level Stir conversion tracking. **Simplified from the original 12-value enum** — at <10 testers, the granular event taxonomy is fiction. `event_category` is the structured field (3-4 values), `event_type` is free text that can be retroactively categorized as patterns emerge.

| Column                        | Type                | Notes                                                 |
| ----------------------------- | ------------------- | ----------------------------------------------------- |
| `id`                          | integer primary key |                                                       |
| `occurred_at_utc`             | datetime            |                                                       |
| `event_date`                  | date                |                                                       |
| `event_category`              | enum                | `acquisition`, `activation`, `usage`, `feedback`      |
| `event_type`                  | text                | free text, e.g. `download`, `kitchen_scan`, `cook_mode_started` — categorize retroactively |
| `source`                      | text nullable       | e.g. `x`, `direct`, `manual`, `unknown`               |
| `medium`                      | text nullable       | e.g. `social`, `profile`, `post`, `reply`             |
| `campaign`                    | text nullable       |                                                       |
| `utm_source`                  | text nullable       |                                                       |
| `utm_medium`                  | text nullable       |                                                       |
| `utm_campaign`                | text nullable       |                                                       |
| `utm_content`                 | text nullable       |                                                       |
| `referring_post_id`           | integer nullable    |                                                       |
| `referring_x_handle`          | text nullable       |                                                       |
| `attribution_method`          | enum                | `self_reported`, `utm`, `referrer_header`, `inferred`, `unknown` |
| `is_likely_icp`               | boolean nullable    | only set when `self_reported` — see privacy rule below |
| `qualitative_feedback`        | text nullable       |                                                       |
| `source_data_quality`         | enum                | `exact`, `manual`, `estimated`, `inferred`, `unknown` |
| `raw_response_id`             | integer nullable    |                                                       |

**Privacy rule:** `is_likely_icp` and any working-parent / home-cook classification can only be set when `attribution_method = self_reported`. There is no `inferred_low` confidence level. If the tester hasn't told you they're a working parent, the column stays null. Better honest gaps than stored guesses about strangers.

**Event category guide:**

| Category       | Meaning                                                         | Example event_types                                |
| -------------- | --------------------------------------------------------------- | -------------------------------------------------- |
| `acquisition`  | They found Stir                                                 | `site_visit`, `profile_visit`, `link_click`, `download` |
| `activation`   | They tried the core action                                      | `signup`, `kitchen_scan`, `three_options_generated` |
| `usage`        | They used the product meaningfully                              | `cook_mode_started`, `cook_mode_completed`, `repeat_session` |
| `feedback`     | They told you something useful                                  | `qualitative_feedback`, `bug_report`, `feature_request` |

App Store attribution caveat: there is no reliable pipe from "X click" to "App Store download" without TestFlight or App Store Connect attribution APIs (both limited). The default attribution method for downloads will be `self_reported` — i.e., you ask testers where they found Stir and trust the answer. UTM tagging works for getstir.app visits but not App Store downloads.

---

### `stir_testers`

Person-level tester records, with privacy constraints.

| Column                        | Type                | Notes                                                                     |
| ----------------------------- | ------------------- | ------------------------------------------------------------------------- |
| `id`                          | integer primary key |                                                                           |
| `alias`                       | text                | no full personal data required                                            |
| `x_handle`                    | text nullable       |                                                                           |
| `contact_ref`                 | text nullable       | optional local-only                                                       |
| `source`                      | text nullable       |                                                                           |
| `first_seen_date`             | date                |                                                                           |
| `is_working_parent_home_cook` | boolean nullable    | self-reported only; null otherwise                                       |
| `icp_notes`                   | text nullable       | what they told you, not what you inferred                                |
| `downloaded_app_at`           | datetime nullable   |                                                                           |
| `scanned_kitchen_at`          | datetime nullable   |                                                                           |
| `got_plausible_dinners_at`    | datetime nullable   |                                                                           |
| `used_cook_mode_at`           | datetime nullable   |                                                                           |
| `feedback_summary`            | text nullable       |                                                                           |
| `status`                      | enum                | `lead`, `downloaded`, `activated`, `cook_mode_used`, `churned`, `unknown` |

---

### `milestones`

Tracks parallel ladders: follower distribution, content, reps, and Stir validation. **The validation ladder is now structurally equal to the distribution ladder.**

| Column                   | Type                | Notes                                               |
| ------------------------ | ------------------- | --------------------------------------------------- |
| `id`                     | integer primary key |                                                     |
| `category`               | enum                | `distribution`, `validation`, `content`, `reps`     |
| `ladder_position`        | integer             | order within category, 1-indexed                    |
| `name`                   | text                |                                                     |
| `start_value`            | integer nullable    |                                                     |
| `target_value`           | integer nullable    |                                                     |
| `current_value_override` | integer nullable    |                                                     |
| `status`                 | enum                | `not_started`, `in_progress`, `achieved`, `skipped` |
| `achieved_at`            | datetime nullable   |                                                     |
| `notes`                  | text nullable       |                                                     |

#### Initial milestones

**Distribution ladder** (replaces the old 100→500k ladder):

```text
distribution[1]: 61 → 100 followers
distribution[2]: 100 → 250
distribution[3]: 250 → 500
distribution[4]: 500 → 1,000
distribution[5]: 1,000 → 2,500
distribution[6]: 2,500 → 5,000 (operational ceiling)
```

Beyond 5,000 the dashboard stops anchoring on follower count as a goal. The 500k number is preserved in `settings.long_arc_reminder` and surfaces only in the Progress view's long-arc footer.

**Validation ladder** (new — equal depth to distribution):

```text
validation[1]: first Stir download attributed to X
validation[2]: first 5 Stir downloads
validation[3]: first working-parent/home-cook tester (self-reported)
validation[4]: first kitchen scan with 3 plausible dinners
validation[5]: first Cook Mode completion
validation[6]: 5 Cook Mode completions in a week
```

**Content ladder:**

```text
content[1]: first post with 1,000 impressions
content[2]: first reply with 100 impressions
content[3]: first post with 10+ bookmarks
```

**Reps ladder:**

```text
reps[1]: first week with daily reply reps completed
reps[2]: first 4 consecutive weeks of rep adherence
```

---

### `weekly_reviews`

Stores weekly postmortems.

| Column                      | Type                | Notes |
| --------------------------- | ------------------- | ----- |
| `id`                        | integer primary key |       |
| `week_start_date`           | date                |       |
| `week_end_date`             | date                |       |
| `followers_start`           | integer nullable    |       |
| `followers_end`             | integer nullable    |       |
| `follower_delta`            | integer nullable    |       |
| `posts_shipped`             | integer             |       |
| `replies_shipped`           | integer             |       |
| `reply_sessions_completed`  | integer             |       |
| `daily_reps_days_completed` | integer             |       |
| `best_post_id`              | integer nullable    |       |
| `worst_post_id`             | integer nullable    |       |
| `strongest_pillar`          | text nullable       |       |
| `weakest_pillar`            | text nullable       |       |
| `downloads`                 | integer             |       |
| `qualified_icp_testers`     | integer             | self-reported only |
| `what_moved`                | text nullable       |       |
| `what_got_stuck`            | text nullable       |       |
| `lesson`                    | text nullable       |       |
| `next_week_experiment`      | text nullable       |       |
| `counterfactual_note`       | text nullable       | freeform acknowledgment of unmeasurable baseline |
| `exported_markdown_path`    | text nullable       |       |
| `created_at`                | datetime            |       |
| `updated_at`                | datetime            |       |

---

### `experiments`

Optional but useful after MVP.

| Column                | Type                | Notes                                          |
| --------------------- | ------------------- | ---------------------------------------------- |
| `id`                  | integer primary key |                                                |
| `name`                | text                |                                                |
| `start_date`          | date                |                                                |
| `end_date`            | date                |                                                |
| `hypothesis`          | text                |                                                |
| `content_lane`        | text nullable       |                                                |
| `target_audience`     | text nullable       |                                                |
| `success_metric`      | text                |                                                |
| `minimum_sample_size` | integer nullable    |                                                |
| `result_summary`      | text nullable       |                                                |
| `status`              | enum                | `planned`, `running`, `completed`, `abandoned` |

Example:

```text
Hypothesis:
Replies under build-pillar posts produce more useful followers than standalone stir-pillar posts.

Success metric:
At least 10 high-quality replies shipped, 2+ new followers, and at least 1 profile visit or Stir click signal.
```

---

### `agent_conversations`

Persistent chat sessions with the Growth Agent (§14.8, §28).

| Column                | Type                | Notes                                                       |
| --------------------- | ------------------- | ----------------------------------------------------------- |
| `id`                  | integer primary key |                                                             |
| `started_at_utc`      | datetime            |                                                             |
| `last_message_at_utc` | datetime            |                                                             |
| `title`               | text                | auto-generated from first user message, user-editable      |
| `context_seed`        | text nullable       | what triggered the conversation, e.g. `today_draft`, `next_rep_lane_gap:stir×icp×ask`, `weekly_review_interpretation:week_2026_05_18` |
| `status`              | enum                | `active`, `archived`                                        |
| `message_count`       | integer             |                                                             |
| `total_input_tokens`  | integer             |                                                             |
| `total_output_tokens` | integer             |                                                             |
| `estimated_cost_usd`  | real                | computed from token counts and per-model rate snapshot      |
| `model_default`       | text                | default model for this conversation; user-overridable per message |
| `created_at`          | datetime            |                                                             |

---

### `agent_messages`

Append-only message history per conversation.

| Column            | Type                | Notes                                                       |
| ----------------- | ------------------- | ----------------------------------------------------------- |
| `id`              | integer primary key |                                                             |
| `conversation_id` | integer foreign key |                                                             |
| `role`            | enum                | `user`, `assistant`, `system`, `tool_result`                |
| `content`         | text                | message body; JSON-serializable for `tool_result`           |
| `tool_calls_json` | text nullable       | structured tool calls from assistant role                   |
| `tool_call_id`    | text nullable       | links a `tool_result` message to its originating `tool_use` |
| `model`           | text nullable       | which model produced this message (null for user/system)   |
| `input_tokens`    | integer nullable    |                                                             |
| `output_tokens`   | integer nullable    |                                                             |
| `rate_snapshot_json` | text nullable    | per-token cost at time of generation, for retroactive cost auditing if pricing changes |
| `resulted_in_published_post_id` | integer fk nullable | links to `posts.id` if this message led to a successful publish; ON DELETE SET NULL |
| `created_at_utc`  | datetime            |                                                             |

---

### `agent_tool_calls`

Audit log of every tool invocation. Each row is one call; the agent may make multiple calls per assistant turn.

| Column           | Type                | Notes                                          |
| ---------------- | ------------------- | ---------------------------------------------- |
| `id`             | integer primary key |                                                |
| `message_id`     | integer foreign key | the assistant message that requested the call  |
| `tool_name`      | text                | e.g. `query_dashboard_state`, `save_draft_post` |
| `arguments_json` | text                | tool arguments at invocation. For publish tools, the raw `confirmation_token` MUST be redacted before insert (replaced by `confirmation_token_id` referencing `publish_confirmation_tokens.id`). The tool dispatcher performs the redaction; do NOT rely on the agent SDK's default argument-logging behavior. See §28.2 rule #11. |
| `redacted_arguments` | boolean default false | true when `arguments_json` was redacted (currently: publish tools). Reviewers checking the audit log can sort/filter on this column. |
| `result_json`    | text nullable       | null if errored before producing result        |
| `status`         | enum                | `success`, `error`, `partial`                  |
| `error_message`  | text nullable       |                                                |
| `duration_ms`    | integer             |                                                |
| `created_at_utc` | datetime            |                                                |

---

### `publish_confirmation_tokens`

Single-use UUIDs that gate the publish tools. Generated by the server-side Streamlit click-handler when Daniel clicks "Publish now" in the confirmation modal; consumed atomically when the publish tool fires. Raw tokens are never stored — only SHA-256 hashes. See §28.10 publish flow for the full lifecycle.

| Column                   | Type                | Notes                                                                                       |
| ------------------------ | ------------------- | ------------------------------------------------------------------------------------------- |
| `id`                     | integer primary key | referenced by `agent_tool_calls.arguments_json` as `confirmation_token_id` after redaction  |
| `token_hash`             | text unique         | SHA-256 hex of the raw UUID; lookup key                                                     |
| `post_id`                | integer fk          | the draft this token authorizes; FK to `posts.id`                                           |
| `draft_text_hash_at_issue` | text              | SHA-256 of the draft text at modal-open time; if `posts.text` changes before the token is consumed, the hash mismatch invalidates the token (§22 edge case "Draft edited after confirmation token generated") |
| `created_at_utc`         | datetime            | when the click-handler generated the token                                                  |
| `expires_at_utc`         | datetime            | `created_at_utc + x_posting_confirmation_token_ttl_seconds`                                |
| `consumed_at_utc`        | datetime nullable   | NULL until consumed; set in the same DB transaction as a successful publish                |
| `consumed_by_x_post_id`  | text nullable       | the resulting `x_post_id` once published; provides a back-reference for audit              |

Indexes:

```text
unique(token_hash)
index(post_id, created_at_utc)
index(expires_at_utc) where consumed_at_utc is null
```

Notes:
- Raw UUIDs live ONLY in the click-handler's local stack frame and the synchronous call to `publish_post_to_x(post_id, confirmation_token)`. They are NOT written to `st.session_state`, NOT exposed via any agent-callable tool, and NOT persisted in plaintext.
- The agent process MUST run with a tool registry that does NOT include any tool that reads this table or `st.session_state`. The token registry is unreachable from the agent loop by construction.
- Token validation = (a) SHA-256 the incoming raw string, (b) look up `token_hash`, (c) verify `expires_at_utc > now() AND consumed_at_utc IS NULL`, (d) verify `draft_text_hash_at_issue` matches current `posts.text` hash, (e) atomically set `consumed_at_utc` in the same transaction as the publish state writes.
- See §22 for the matrix of token rejection paths.

---

### `reply_targets`

Queue of external X posts identified (by agent or Daniel) as worth replying to. Drives reply-session workflow.

| Column                     | Type                | Notes                                                            |
| -------------------------- | ------------------- | ---------------------------------------------------------------- |
| `id`                       | integer primary key |                                                                  |
| `target_post_url`          | text                |                                                                  |
| `target_post_text`         | text                | snapshot — may differ from current X content if edited or deleted |
| `target_user`              | text                |                                                                  |
| `pillar`                   | text                | suggested lane (v1: stir / build / self)                         |
| `audience`                 | text                | v1: icp / other                                                  |
| `agent_reasoning`          | text                | why the agent thinks this is worth replying to                  |
| `agent_priority_score`     | integer             | 1-10, agent-assigned                                             |
| `daniel_priority_override` | integer nullable    | 1-10, set when Daniel manually adjusts                          |
| `status`                   | enum                | `queued`, `replied`, `dismissed`, `expired`                     |
| `expires_at_utc`           | datetime            | default: created_at + `agent_reply_target_default_expiry_hours` |
| `replied_post_id`          | integer nullable    | fk to `posts` once a reply has been logged                       |
| `created_at_utc`           | datetime            |                                                                  |
| `resolved_at_utc`          | datetime nullable   | set when status transitions out of `queued`                      |

Index on `(status, expires_at_utc)` for the "fresh queue" query.

---

### `voice_samples`

Curated examples of Daniel's voice for the agent system prompt. Top N active samples (default 5) are injected into the system prompt to anchor drafting voice.

| Column               | Type                | Notes                                            |
| -------------------- | ------------------- | ------------------------------------------------ |
| `id`                 | integer primary key |                                                  |
| `post_id`            | integer fk nullable | nullable for off-platform samples (e.g., a DM, an Obsidian note) |
| `text`               | text                |                                                  |
| `context_note`       | text nullable       | why this is a good voice reference              |
| `pillar`             | text                | which lane this voice represents                |
| `is_active`          | boolean             | included in current system prompt rotation       |
| `priority`           | integer             | display order in system prompt; lower = earlier |
| `added_at_utc`       | datetime            |                                                  |
| `last_used_at_utc`   | datetime nullable   | when this sample was last in a generation; tracks overuse |

---

### `agent_drafts`

Tracks agent-generated drafts before they become posts (or get rejected). The agent's working memory for what it has proposed and what status each proposal is in.

| Column              | Type             | Notes                                                                     |
| ------------------- | ---------------- | ------------------------------------------------------------------------- |
| `id`                | integer pk       |                                                                           |
| `created_at`        | datetime         |                                                                           |
| `session_id`        | text             | groups drafts from the same chat session                                  |
| `draft_kind`        | enum             | `standalone`, `reply`, `quote`, `thread_root`                             |
| `text`              | text             | the draft itself                                                          |
| `pillar`            | text             | v1 taxonomy (stir / build / self)                                         |
| `audience`          | text             | v1 taxonomy (icp / other)                                                 |
| `cta`               | text             | v1 taxonomy (ask / none)                                                  |
| `hypothesis_id`     | int nullable     | links to `experiments` if drafted to test one                             |
| `target_post_url`   | text nullable    | for replies                                                               |
| `target_post_text`  | text nullable    | snapshot of target at draft time                                          |
| `agent_reasoning`   | text             | why the agent thinks this is a good draft                                 |
| `voice_self_score`  | text             | JSON: `{"intelligence": 0-3, "wisdom": 0-3, "humility": 0-3}` — the agent's own quality scores per the IWH framework (§28.3 Section 2). NOT the enforcement counter — see `iwh_attempt_index` |
| `iwh_attempt_index` | integer default 1 | which revision attempt this draft is (1 = first try). Tracked by the ORCHESTRATOR (`app/agent/session.py`), NOT by the agent itself. The orchestrator increments on every `save_draft` call where any score < `iwh_self_score_minimum`. On attempt `iwh_max_revision_attempts + 1`, the orchestrator refuses to call `save_draft` and emits the refusal back into the conversation. Lives outside the agent's reachable state by design (§28.2 rule #13). |
| `status`            | enum             | `proposed`, `accepted_as_is`, `accepted_with_edits`, `rejected`, `superseded` |
| `final_post_id`     | int nullable     | foreign key to `posts.id` if shipped                                      |
| `user_feedback`     | text nullable    | what Daniel said when revising                                            |
| `revision_of`       | int nullable     | foreign key to prior `agent_drafts.id` if this is a revision              |
| `prepublish_score_id` | int nullable   | foreign key to `prepublish_scores.id`. Populated by the §28.11 scorer at `save_draft_*` time. NULL if the scorer hasn't run yet (e.g., legacy rows from before Phase 5.8). |
| `confidence_label`  | enum nullable    | `fact \| inference \| speculation \| mixed`. Required by §28.2 rule #14 on every analytical claim emitted with a draft. NULL when the draft is creative-only (no analytical claim attached). |
| `similarity_warning_json` | text nullable | JSON. `{"max_cosine": float 0-1, "nearest_post_id": int, "nearest_text_excerpt": str, "label": "near_duplicate"|"close_echo"|"distinct"}`. Written by the §28.13 repetition guard at `save_draft_*` time. NULL if embeddings layer is unavailable. |

Indexes:

```text
index(session_id, created_at)
index(status)
index(final_post_id)
```

---

### `agent_target_accounts`

Curated list of X accounts Daniel wants to engage with. Used by `find_reply_targets` in MVP/manual mode (when X API search isn't available yet) to suggest where to look for recent posts to reply to.

| Column            | Type            | Notes                                              |
| ----------------- | --------------- | -------------------------------------------------- |
| `id`              | integer pk      |                                                    |
| `x_handle`        | text            |                                                    |
| `display_name`    | text nullable   |                                                    |
| `notes`           | text            | why this account, what lane                        |
| `lane`            | text            | pillar × audience tag                              |
| `priority`        | int             | 1 = high priority, higher = lower priority         |
| `last_engaged_at` | datetime nullable | when Daniel last replied to this account         |
| `is_active`       | boolean         | for soft delete                                    |
| `created_at`      | datetime        |                                                    |

Indexes:

```text
index(lane, priority) where is_active = true
unique(x_handle)
```

---

### `voice_profiles`

Generated voice fingerprints derived from Daniel's own past posts. Distinct from `voice_samples` (which are hand-picked raw exemplars). The profile is a compact JSON summary that is spliced into the system prompt alongside the raw samples, so the agent gets both verbatim references AND a structural read of Daniel's actual writing patterns. See §28.12.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `generated_at_utc` | datetime | when this snapshot was generated |
| `is_active` | boolean | exactly one row may have `is_active = true` (enforced by partial unique index); activation is atomic — deactivate-then-insert in a single transaction |
| `source_post_window_days` | integer | how many days of `posts` were sampled to build this snapshot (default `voice_profile_window_days = 90`) |
| `source_post_count` | integer | how many posts actually fed the synthesis (after filtering by `posts.x_post_id IS NOT NULL` and `posts.text IS NOT NULL`) |
| `profile_json` | text | JSON. Schema: `{"hook_patterns": [str], "cadence": {"avg_chars": int, "avg_sentences": float, "one_idea_per_line_rate": 0-1}, "vocabulary_signatures": [str], "tone_markers": [str], "stop_phrases": [str], "self_description": str (1-2 sentences in first person)}`. `self_description` is what gets spliced into the system prompt; the rest is structural data the agent can be asked to reflect on. |
| `model_used` | text | e.g. `claude-haiku-4-5-20251001` — recorded so future re-generations can be compared with the same model |
| `tokens_used` | integer | cost-tracking |
| `superseded_by_profile_id` | int nullable | back-reference once a newer profile takes over `is_active` |

Indexes:

```text
unique(is_active) where is_active = true
index(generated_at_utc desc)
```

Notes:
- Voice samples (`voice_samples`) and voice profile (`voice_profiles`) are complementary, NOT alternatives. Voice samples are raw post text Daniel picked. The profile is a structural read of his actual writing. The system prompt carries both.
- Regeneration is manual-button only (Settings → Growth Agent → "Regenerate voice profile from last N days"). Never automatic on a cron — Daniel decides when his voice has shifted enough to warrant a refresh.
- A profile with `source_post_count < voice_profile_min_source_posts` (default 10) is rejected at write time; the regenerate handler surfaces "not enough posts to build a profile" instead of saving a thin one.

---

### `post_embeddings`

Embedding vectors keyed to `posts.id`. Powers the §28.13 repetition guard. SQLite has no native vector type; vectors are stored as `BLOB` (raw float32 little-endian) and similarity is computed in Python with numpy cosine. For MVP single-user volume (Daniel's lifetime post count is in the low thousands) a brute-force in-memory cosine scan is fast enough; a vector index can be added in V1.X if scan time crosses 200ms.

| Column | Type | Notes |
| --- | --- | --- |
| `post_id` | integer pk | FK to `posts.id` `ON DELETE CASCADE` |
| `embedding_blob` | blob | raw float32 little-endian, length `embedding_dim * 4` bytes |
| `embedding_dim` | integer | dimensionality (e.g. 1024). Stored per-row so a model change doesn't silently corrupt similarity math — readers MUST verify `embedding_dim` matches the current `embedding_model_dim` setting before comparing. |
| `model_name` | text | e.g. `voyage-3-lite` or `text-embedding-3-small`. Required. |
| `model_version` | text | e.g. `2024-09-01`. Optional but recommended; allows fleet upgrades. |
| `created_at_utc` | datetime | |
| `source_text_hash` | text | `sha256(posts.text)` at embedding time. If `posts.text` is later edited, the hash mismatch invalidates the cached embedding and forces re-embed. |

Indexes:

```text
index(model_name, model_version)
index(created_at_utc)
```

Notes:
- Embedding model and dimensionality are NOT centralized in `settings` (see §28.13 for the rationale — the embedding layer is a swappable adapter, not a tuneable). Migration to a new model is an explicit re-embed-all operation, gated by a Settings → Maintenance button, not a silent settings change.
- If the embedding provider is unavailable (network down, rate-limited, API key missing), the §28.13 guard returns `similarity_warning_json = NULL` and the agent draft proceeds. The repetition guard is a soft check that informs, never a hard gate.
- A daily VACUUM cleanup deletes rows where the parent `posts.id` no longer exists (cascade handles this, but the VACUUM keeps SQLite's page tree compact).

---

### `prepublish_scores`

Per-draft heuristic scores produced by the §28.11 pre-publish scorer. One row per `agent_drafts.id` that has been scored. Scores are deterministic functions of the draft text + a small set of context inputs (target post text for replies, lane, recent post window); the scorer is NOT an LLM call by default (see §28.11 for the deterministic-first rule).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `agent_draft_id` | integer fk | `agent_drafts.id` `ON DELETE CASCADE` |
| `scored_at_utc` | datetime | |
| `clarity_score` | integer | 0-3 |
| `hook_strength_score` | integer | 0-3 |
| `specificity_score` | integer | 0-3 |
| `length_fit_score` | integer | 0-3 |
| `format_fit_score` | integer | 0-3 |
| `topic_fit_score` | integer | 0-3 |
| `reply_substance_score` | integer nullable | 0-3, only populated when `draft_kind = reply` |
| `cta_strength_score` | integer nullable | 0-3, NULL when `cta = none` |
| `voice_fit_score` | integer | 0-3 — agreement between the draft and the active `voice_profiles.profile_json` |
| `composite_label` | enum | `weak \| viable \| strong` — derived from the scores per §28.11 §"Composite label derivation". This label is the ONLY thing the UI shows by default; individual scores are revealed on click. No numeric composite. |
| `warnings_json` | text nullable | JSON array of one-line strings: e.g. `["hook is generic", "ends without a clear ask, but cta=none, so okay"]`. The scorer's plain-language read. |
| `scorer_version` | text | semver of the scorer (e.g. `prepublish-scorer/0.1.0`); incremented when the algorithm changes so historical rows are interpretable |
| `tokens_used` | integer | 0 for the deterministic path; non-zero only if §28.11's optional LLM augmentation is enabled |

Indexes:

```text
unique(agent_draft_id)
index(scored_at_utc)
index(composite_label)
```

Notes:
- The scorer is a pure function of (draft text, draft metadata, active voice profile). Re-running it on the same inputs yields the same output — drift between runs is a bug.
- The scorer never blocks a draft. It informs. The IWH framework + dark-pattern lint (§28.2 rules #12 #13) are the hard gates; this is a "before you click Publish, here's a read" panel.
- `composite_label` derivation table (recomputed on every row write so it's never out of date with the scores):
  - `strong` if `count(scores >= 2) >= 6` and `count(scores == 3) >= 2` and no score is 0.
  - `weak` if `count(scores == 0) >= 1` or `count(scores >= 2) <= 3`.
  - `viable` otherwise.

---

## 11. Computed views

### `v_account_daily`

Canonical daily account state.

Rules:

1. Pick the snapshot closest to configured daily snapshot time.
2. Apply corrections if present.
3. Compute deltas.

Fields:

```text
snapshot_date
followers_count
following_count
post_count
listed_count
like_count
media_count
bio_text
delta_vs_yesterday
delta_vs_baseline
delta_7d
delta_30d
velocity_7d_per_day        # display only when |delta_7d| >= 10; otherwise show "noise"
velocity_30d_per_day
distance_to_current_milestone
current_milestone_progress_pct
distance_to_operational_ceiling   # replaces distance_to_500k as the operational metric
distance_to_long_arc              # 500k, shown only in long-arc footer
```

---

### `v_post_latest_metrics`

Latest metrics per post.

Fields:

```text
post_id
x_post_id
created_at_utc
text
type
pillar
audience
cta
impressions
likes
replies
reposts
quotes
bookmarks
engagement_rate
bookmark_rate
reply_rate
profile_clicks
url_link_clicks
link_click_rate
latest_metrics_collected_at
data_quality
```

---

### `v_daily_reps`

Daily rep adherence.

Fields:

```text
activity_date
posts_shipped
replies_shipped
quotes_shipped
reply_sessions_completed
minimum_reps_completed
planned_posts
planned_replies
post_target_met
reply_target_met
session_target_met
time_spent_minutes
```

---

### `v_funnel_daily`

Daily X → Stir funnel.

Fields:

```text
event_date
x_impressions_estimate
profile_visits
link_clicks
getstir_visits
downloads
waitlist_signups
kitchen_scans
three_options_generated
cook_mode_started
qualified_icp_testers          # self-reported only
working_parent_home_cook_testers  # self-reported only
```

Important: `x_impressions_estimate` should be labeled as an estimate unless all included impressions are exact post-level API metrics.

---

### `v_lane_performance`

**New view — replaces the original "rank lanes if n≥5" rule.** Shows per-lane medians with IQR and sample size so uncertainty is visible at all sample sizes, not hidden behind point estimates with a binary threshold.

Fields:

```text
pillar
audience
cta
post_count
days_covered
median_impressions
iqr_impressions_low      # 25th percentile
iqr_impressions_high     # 75th percentile
median_engagement_rate
iqr_engagement_rate_low
iqr_engagement_rate_high
total_bookmarks
total_replies
stir_signal_count        # count of stir_conversion_events plausibly attributable
confidence_label         # see logic below
```

Confidence label logic:

```text
if post_count < 5 OR days_covered < 3:
    confidence_label = "insufficient sample"
elif post_count < 15:
    confidence_label = "low — show scatter, do not rank"
elif post_count >= 15 AND days_covered >= 7:
    confidence_label = "moderate"
elif post_count >= 30 AND days_covered >= 14:
    confidence_label = "stronger"
else:
    confidence_label = "moderate"
```

The UI shows medians at all sample sizes but pairs them with IQR bars and the confidence label. Lanes are only sortable/rankable at "moderate" or above.

---

## 12. Metric definitions

### Account metrics

| Metric                            | Definition                                                                   |
| --------------------------------- | ---------------------------------------------------------------------------- |
| `followers_count`                 | Current follower count from daily snapshot                                   |
| `delta_vs_yesterday`              | Today's followers minus previous canonical daily snapshot                    |
| `delta_vs_baseline`               | Today's followers minus `baseline_followers`                                 |
| `delta_7d`                        | Today's followers minus followers from 7 calendar days prior, if available   |
| `delta_30d`                       | Today's followers minus followers from 30 calendar days prior, if available  |
| `velocity_7d_per_day`             | `delta_7d / 7`, display only when `abs(delta_7d) >= 10`                     |
| `velocity_30d_per_day`            | `delta_30d / 30`                                                             |
| `distance_to_current_milestone`   | `current_milestone - followers_count`                                        |
| `milestone_progress_pct`          | `(followers_count - milestone_start) / (milestone_target - milestone_start)` |
| `distance_to_operational_ceiling` | `operational_ceiling - followers_count`                                      |

For current milestone:

```text
milestone_start = 61
milestone_target = 100
followers_count = 64

progress = (64 - 61) / (100 - 61)
progress = 3 / 39
progress ≈ 7.7%
```

**Why `velocity_7d_per_day` is conditional:** at low absolute deltas, dividing by 7 produces precise-looking noise. `delta_7d = 3` → `velocity = 0.43 followers/day` reads as a real growth rate but is statistically indistinguishable from zero. Suppressing the number below `|delta_7d| >= 10` is honest about what the data can support.

---

### Content metrics

| Metric                       | Definition                                            |
| ---------------------------- | ----------------------------------------------------- |
| `impressions`                | X impression count if API/manual metric exists        |
| `likes`                      | Like count                                            |
| `replies`                    | Reply count                                           |
| `reposts`                    | Retweets/reposts                                      |
| `quotes`                     | Quote count                                           |
| `bookmarks`                  | Bookmark count                                        |
| `engagements_total`          | From API only when available                          |
| `engagements_total_approx`   | Computed sum, **always labeled "approx" in UI**       |
| `engagement_rate`            | `engagements_total / impressions` (uses approx if exact unavailable) |
| `bookmark_rate`              | `bookmarks / impressions`                             |
| `reply_rate`                 | `replies / impressions`                               |
| `profile_click_rate`         | `profile_clicks / impressions`, if available          |
| `link_click_rate`            | `url_link_clicks / impressions`, if available         |

Computed fallback:

```text
engagements_total_approx =
  likes + replies + reposts + quotes + bookmarks
```

Note: this sum is not the same as X's official `engagements` metric (which has its own definition and may include other interactions). When the API returns `engagements_total` directly, use it; when it doesn't, use the approx and label it as such in the UI.

If `impressions` is null or zero, rates should display as:

```text
N/A
```

not `0`.

---

### Activity metrics

| Metric                     | Definition                                        |
| -------------------------- | ------------------------------------------------- |
| `posts_per_day`            | Count of standalone/thread posts shipped that day |
| `replies_per_day`          | Count of reply-type posts shipped/logged that day |
| `quotes_per_day`           | Count of quote-type posts shipped/logged that day |
| `reply_sessions_completed` | Intentional reply work blocks completed           |
| `minimum_reps_completed`   | True only if daily configured minimums are met    |
| `adherence_7d`             | Days in last 7 where minimum reps completed / 7   |
| `adherence_30d`            | Days in last 30 where minimum reps completed / 30 |

---

### Stir validation metrics

| Metric                             | Definition                                                                |
| ---------------------------------- | ------------------------------------------------------------------------- |
| `getstir_visits`                   | Visits from analytics/manual import                                       |
| `x_attributed_visits`              | Visits with X/referrer/UTM evidence                                       |
| `downloads`                        | App downloads, manual/App Store/analytics import                          |
| `x_attributed_downloads`           | Downloads where attribution_method = `self_reported` and source contains "x" |
| `qualified_icp_testers`            | Testers with `is_working_parent_home_cook = true` (self-reported only)    |
| `activation_event`                 | Kitchen scan completed, 3 options generated, or Cook Mode started         |
| `first_5_downloads_progress`       | `min(downloads, 5) / 5`                                                   |
| `first_5_x_attributed_progress`    | `min(x_attributed_downloads, 5) / 5`                                      |

---

## 13. Accuracy rules

### Hard rules

1. **Same-time daily account snapshot**

   * Default: 9:00 AM America/New_York.
   * MVP: this is a manual ritual, not a scheduled job.
   * If missed, allow manual snapshot at any time but label it noncanonical.

2. **Immutable raw snapshots**

   * Never overwrite raw account/post metric snapshots.
   * Store corrections in correction tables.

3. **Separate stock and flow**

   * Follower count is stock.
   * Posts/replies/downloads are flow.

4. **Compute deltas from raw snapshots**

   * Do not manually store deltas unless cached with provenance.

5. **Use rolling windows**

   * Show 7-day and 30-day trends.
   * The one-day follower delta is visible but visually de-emphasized.

6. **Suppress noise-level velocity**

   * `velocity_7d_per_day` only displays when `|delta_7d| >= 10`.
   * Below threshold the UI shows "trend not yet measurable."

7. **Label estimates and inferences**

   * Example: "follower conversion inferred" must not appear as exact attribution.
   * `engagements_total_approx` always carries the "approx" label.

8. **UTM preservation**

   * Extract and store UTM parameters for every link.

9. **Raw response preservation**

   * Preserve raw API responses where feasible. (Empty until V1.1.)

10. **Manual correction support**

    * Manual corrections must leave audit trail.

11. **Self-report-only sensitive attributes**

    * ICP classification, working-parent status, home-cook status: only stored when self-reported.
    * No inference, no `inferred_low` confidence level.

---

### Interpretation rules

The UI should enforce these constraints:

| Situation                                       | UI behavior                                    |
| ----------------------------------------------- | ---------------------------------------------- |
| Follower change is ±1 day-over-day              | Show "noise; wait for 7-day trend"             |
| `|delta_7d| < 10`                               | Suppress velocity number; show "trend not yet measurable" |
| Content lane has post_count < 5 or days < 3     | Show "insufficient sample"                     |
| Content lane has 5-14 posts                     | Show data as scatter only, no median ranking   |
| Content lane has 15+ posts and 7+ days          | Show median with IQR; ranking allowed          |
| A post has 0 impressions or missing impressions | Do not compute engagement rate                 |
| `engagements_total` field is computed (not API) | Label "approx" prominently                     |
| A follower appears after a post                 | Do not attribute unless explicit               |
| AI-builder follower gained                      | Count under distribution, not validation       |
| ICP tester (self-reported) downloads Stir       | Count under validation                         |
| Downloads but no kitchen scan                   | Count as acquisition, not activation           |
| Kitchen scan + plausible dinners                | Count as stronger product signal               |
| Cook Mode usage                                 | Count as highest early activation signal       |
| Working-parent status not self-reported         | Leave null; do not infer                       |

---

## 14. Dashboard views

# 14.1 Today / Weigh-In

### Purpose

Daily operating cockpit.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Today: May 21, 2026                                          │
│ Snapshot status: needs manual entry (or: collected 9:02 AM)  │
└─────────────────────────────────────────────────────────────┘

[Manual snapshot form — pinned to top until today's snapshot exists]
followers: ___  following: ___  posts: ___  listed: ___
[save daily snapshot]

┌─────────────┬──────────────┬──────────────┬─────────────────┐
│ Followers   │ Δ yesterday  │ Δ baseline   │ To 100          │
│ 64          │ +?           │ +3           │ 36 remaining    │
└─────────────┴──────────────┴──────────────┴─────────────────┘

Milestone progress: 61 → 100 (distribution[1])
[████░░░░░░░░░░░░░░░░] 7.7%

Validation ladder: not yet started
Next: first Stir download attributed to X (validation[1])

Trend warning:
Daily follower count is noisy. Judge the week, not the morning.
7-day velocity: trend not yet measurable (delta_7d = +3)

Daily reps:
[ ] 1 standalone/build-in-public post
[ ] 12 high-quality replies     ← raised from 5; experimental, review on day 21
[ ] 1 reply session
[ ] Log Stir conversion signal

Today's plan:
- Planned post:
- Planned reply targets:
- Planned CTA:
- Stir ask:
```

### Required components

1. **Manual snapshot form (pinned)**

   * Appears at top until today's canonical snapshot is recorded.
   * 4-5 fields, takes 30 seconds.
   * Once today's snapshot exists, collapses to a small "edit" link.

2. **Follower weigh-in card**

   * Current followers
   * Delta vs yesterday
   * Delta vs baseline
   * Distance to next milestone
   * Milestone progress

3. **Validation ladder status**

   * Current validation milestone and progress.
   * Equal visual weight to distribution ladder.

4. **Trend warning**

   * Always show when viewing daily delta.
   * Stronger warning if fewer than 7 snapshots exist.
   * Velocity suppressed below threshold.

5. **Daily reps checklist**

   * Posts target
   * Replies target (raised, experimental)
   * Reply session target
   * Manual logging target
   * Stir signal check

6. **Today's planned work**

   * Planned post text/idea
   * Planned reply lanes
   * Planned direct ask
   * Avoidance note

7. **Stir conversion status**

   * Visits today
   * Downloads today
   * ICP testers today (self-reported)
   * First 5 downloads progress

8. **Growth Agent quick-actions** — see §28 for full agent spec

   * Button: "Draft today's post" — opens Agent Chat with seed context: current under-sampled lane, current open hypothesis.
   * Button: "Start reply session" — opens Agent Chat in `find_reply_targets` mode, prefilled with the under-sampled lane.
   * Button: "Just the lane gaps" — opens Next Rep view (§14.2) without invoking the agent.

### Acceptance criteria

* User can complete the daily check-in (snapshot + plan) in under 3 minutes.
* The manual snapshot form is the default path; no API required for MVP.
* If daily reps are incomplete, the UI makes that obvious without moralizing.
* The page does not imply follower delta is the same as success.
* The validation ladder is structurally and visually equal to the distribution ladder.

---

# 14.2 Next Rep — NEW

### Purpose

Close the loop between measurement and the daily generative act. The dashboard knows which content lanes are under-sampled and which open hypotheses need more data; this view surfaces that as a "what should I post next" prompt.

Without this view, the dashboard is purely retrospective — you do reps on intuition, then look at the dashboard afterward to see what happened. With it, the dashboard nudges rep selection so each post fills a data gap or tests an open hypothesis.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Next Rep                                                     │
│ What should I post / reply about?                            │
└─────────────────────────────────────────────────────────────┘

This week's lane coverage:
  stir × icp × ask:    0 posts  ← biggest data gap
  stir × icp × none:   1 post
  build × other × none: 4 posts
  self × other × none:  2 posts

Suggestion: Stir lane is under-sampled this week. Posting one
stir × icp post would meaningfully reduce uncertainty in your
strongest hypothesis area.

Open hypotheses needing data:
  H1: "Replies under build-in-public posts produce more useful
       followers than standalone stir posts."
       → needs: 5 more build-pillar replies, 0 / 5 shipped this week.

  H2: "Self-pillar posts about neuro-oncology long arc attract
       higher-quality ICP than stir-pillar posts."
       → needs: 1 self-pillar post + 1 download attribution survey.
       → 0 / 1 self-pillar posts this week.

Recent target accounts (replies you might engage with):
  [list of accounts you've recently engaged with successfully,
   filtered by lane you're under-sampling]

Drafts in queue:
  [list of `manual_confirmation_status = draft` posts]

[ + new draft ]   [ start reply session targeting stir × icp ]
```

### Required components

1. **Lane coverage scoreboard**

   * Shows post counts by pillar × audience × CTA for the current week.
   * Highlights the biggest gap.
   * Pulls from `v_lane_performance` with a 7-day filter.

2. **Open hypothesis tracker**

   * Pulls from `experiments` table where `status = 'running'`.
   * Shows what each hypothesis needs to reach its `minimum_sample_size`.
   * Click-through to log a post that contributes to a hypothesis.

3. **Reply target suggestions** — windowed onto §29 Reply Target Queue

   * Top 3–5 rows from `reply_targets WHERE status = 'candidate' ORDER BY recommended_action_score DESC, last_checked_at_utc DESC`, filtered to candidates whose `pillar` matches the under-sampled lane.
   * Each row shows the four MVP scores (Relevance / Engagement surface / Saturation / Reply opportunity) and the deterministic `recommended_action_label` from §29.3.
   * "See full queue →" link opens the dedicated Reply Target Queue (§29.7).
   * If `reply_targets` is empty, the panel surfaces "no candidates yet — add one from the Queue" with a deep link, and falls back to showing the legacy `agent_target_accounts`-based account suggestions as account leads (accounts, not posts).
   * Active accounts from `agent_target_accounts` matching the under-sampled lane are shown as a secondary list under the post-level candidates.

4. **Draft queue**

   * Posts with `manual_confirmation_status = draft` (both manual and `posted_via = agent_assisted`).
   * Agent-generated drafts visually distinguished from manual drafts.
   * One-click "ship" → opens X with the text in your clipboard (or just shows the text to paste).

5. **Agent integration buttons**

   * For each lane gap shown: button "Have the agent draft for this lane" → opens Agent Chat with `draft_post(pillar, audience, cta)` pre-invoked.
   * For each open hypothesis: button "Draft a post that tests this hypothesis" → opens Agent Chat with `hypothesis_id` passed in.
   * For each suggested target account: button "Draft a reply to a recent post by this account" → opens Agent Chat with `draft_reply` workflow.

### Acceptance criteria

* View is reachable from Today view in one click.
* If no experiments are running, the view degrades gracefully to "no open hypotheses — consider starting one in Weekly Review."
* Lane gaps are computed live from the last 7 days, not stored.
* Suggestions never tell you exactly what to write — they tell you what category needs data.

### Why this view earns its place

The original spec had no surface for the daily generative question. Measurement views are necessary but not sufficient. The Next Rep view closes the feedback loop: weekly review identifies hypotheses → Next Rep surfaces them daily → posts shipped fill the matrix → next weekly review can actually conclude something.

---

# 14.3 Progress

### Purpose

Longer-term trend view.

### Sections

1. **Distribution trend**

   * Line chart: follower count by date.
   * Overlay 7-day rolling trend.
   * Overlay 30-day rolling trend after enough data exists.
   * Milestone bands/markers (distribution ladder).
   * Operational ceiling (5k) shown as horizontal line.

2. **Distribution ladder progress**

   * 61 → 100
   * 100 → 250
   * 250 → 500
   * 500 → 1k
   * 1k → 2.5k
   * 2.5k → 5k (operational ceiling)

3. **Validation ladder progress** — equal weight to distribution ladder

   * first download
   * first 5 downloads
   * first self-reported working-parent tester
   * first kitchen scan + plausible dinners
   * first Cook Mode completion
   * 5 Cook Mode completions in a week

4. **Consistency calendar**

   * Calendar heatmap:

     * green/full = daily minimum complete
     * partial = some reps
     * blank = no data
   * Do not use red for failure; missing data and incomplete reps should be visually distinct.

5. **Cumulative activity**

   * Cumulative posts (by pillar)
   * Cumulative replies (by pillar)
   * Cumulative reply sessions
   * Cumulative direct asks
   * Cumulative Stir asks

6. **Behavior vs outcome**

   * Dual chart:

     * bars = replies/posts shipped
     * line = follower count
   * Goal: show whether behavior happened before judging outcome.

7. **Long-arc footer** — small, unobtrusive

   * "500,000 — long arc, not operational. Current operational ceiling: 5,000."
   * No progress bar attached.
   * Reminder of where this is all eventually going, with no daily pressure.

### Example charts

```text
Follower count over time
Y-axis: followers
X-axis: date
Lines:
- raw daily followers
- 7-day smoothed followers
- 30-day smoothed followers
Markers:
- 100, 250, 500, 1k, 2.5k milestones
- 5k operational ceiling (horizontal line)
```

```text
Rep adherence heatmap
Rows: week
Columns: Mon–Sun
Cell value: minimum reps completed / partial / missing
```

---

# 14.4 Content Performance

### Purpose

Find what content lanes are working without overfitting.

### Filters

* Date range
* Type:

  * standalone
  * reply
  * quote
  * thread
* Pillar (v1: stir / build / self)
* Audience (v1: icp / other)
* CTA (v1: ask / none)
* Contains link
* Manual/API
* UTM campaign
* Minimum impressions
* Minimum sample size

### Main table columns

| Column          | Notes                         |
| --------------- | ----------------------------- |
| Date            | created date                  |
| Type            | standalone/reply/quote/thread |
| Text preview    | first 120 chars               |
| Pillar          | selected tag                  |
| Audience        | selected tag                  |
| CTA             | selected tag                  |
| Impressions     | latest                        |
| Likes           | latest                        |
| Replies         | latest                        |
| Reposts         | latest                        |
| Quotes          | latest                        |
| Bookmarks       | latest                        |
| Engagement rate | latest (label "approx" when computed) |
| Bookmark rate   | latest                        |
| Link clicks     | if available                  |
| Lesson          | short note                    |
| URL             | open post                     |

### Summary cards

1. **Top post by impressions** (any sample size)
2. **Top post by engagement rate** (only if impressions exceed threshold)
3. **Top bookmark-rate post**
4. **Best reply**
5. **Most useful Stir signal**
6. **Underperforming post worth learning from**

### Agent integration

For any post in the main table or summary cards, an "Ask agent" button opens the Agent Chat with `analyze_post(post_id)` pre-invoked. The agent returns structured analysis — what worked, what didn't, what to learn, whether the post helped any open hypothesis.

### Lane analysis — replaces the old "rank if n≥5" rule

For each content pillar, show the row from `v_lane_performance`:

| Pillar | Posts/replies | Days covered | Median impressions (IQR) | Median engagement rate (IQR) | Bookmarks | Replies | Stir signals | Confidence |
| ------ | ------------- | ------------ | ------------------------ | ---------------------------- | --------- | ------- | ------------ | ---------- |
| stir   | 3             | 2            | 312 (80–2,400)           | 0.04 (0.01–0.09)             | 4         | 1       | 0            | insufficient sample |
| build  | 8             | 5            | 245 (110–620)            | 0.06 (0.03–0.11)             | 12        | 6       | 2            | low — scatter only |
| self   | 2             | 1            | —                        | —                            | 1         | 0       | 0            | insufficient sample |

**Interpretation rules:**

* Lanes are sortable/rankable only at "moderate" or "stronger" confidence.
* Below that, the UI shows the data but suppresses ordinal ranking.
* IQR is shown alongside median so spread is visible. A lane with median 245 and IQR 110-620 is not the same as median 245 with IQR 230-260, even though the medians match.
* Total bookmarks / replies / Stir signals are shown without sample-size warnings because they're sums, not estimates.

### Anti-overfitting rule (updated)

The old rule was a binary threshold: "Don't rank lanes unless ≥5 posts and ≥3 days." That hides data below threshold and creates false confidence above it.

The new rule is graduated:

```text
post_count < 5  OR days_covered < 3:   "insufficient sample"  — show scatter, no medians
post_count 5-14:                       "low — show scatter, do not rank"  — medians + IQR but no ordering
post_count 15+ AND days 7+:            "moderate" — ranking allowed
post_count 30+ AND days 14+:           "stronger" — ranking with confidence
```

Plus: never rank when one post in a lane has >50% of the lane's total impressions. That's outlier-dominated and the median is fiction.

---

# 14.5 Funnel

### Purpose

Track whether X growth converts into Stir validation.

### Layout

```text
Distribution signal
┌────────────────────────────────────────────────────┐
│ X impressions                                       │
│ Profile visits, if available                       │
│ Link clicks, if available                          │
│ Follower growth                                    │
└────────────────────────────────────────────────────┘

Validation signal
┌────────────────────────────────────────────────────┐
│ getstir.app visits                                 │
│ Downloads                                          │
│ Kitchen scans                                      │
│ 3 plausible dinners generated                      │
│ Cook Mode usage                                    │
│ Working-parent/home-cook testers (self-reported)  │
└────────────────────────────────────────────────────┘
```

### Funnel stages

| Stage                 | Source                                    | Accuracy              |
| --------------------- | ----------------------------------------- | --------------------- |
| X impressions         | X post metrics                            | exact if API returned |
| Profile visits        | non-public X metrics if available         | exact/partial         |
| Link clicks           | X non-public metrics or website analytics | exact/partial         |
| getstir.app visits    | analytics/manual import (UTM)             | exact/partial         |
| Downloads             | App Store/manual                          | manual/self-reported  |
| X-attributed downloads| Self-reported by tester                   | self-reported only    |
| Qualified ICP testers | Self-reported by tester                   | self-reported only    |
| Kitchen scan          | app event/manual                          | exact/manual          |
| 3 plausible dinners   | app event/manual                          | exact/manual          |
| Cook Mode             | app event/manual                          | exact/manual          |
| Qualitative feedback  | manual                                    | manual                |

### App Store attribution — explicit caveat

There is no reliable automatic attribution pipe from X click → App Store download. The two real options are:

1. **Self-reporting:** ask testers "where did you find Stir?" in the app onboarding or follow-up. Trust the answer. This is the MVP default.
2. **TestFlight / App Store Connect APIs:** limited, post-MVP.

UTM tagging works fine for getstir.app visits but does not survive the jump to the App Store. The Funnel view should make this asymmetry visible — "site visits: 47 (UTM-attributed)" and "downloads: 3 (self-reported source)" are different epistemic categories.

### First 5 downloads tracker

```text
First 5 Stir downloads
[██░░░] 2 / 5

Download 1:
- Date:
- Source (self-reported):
- X-attributed? yes/no/unknown
- Working parent (self-reported)? yes/no/unknown
- Used app? yes/no
- Notes:

Download 2:
...
```

### Required distinction

The page must show two separate scorecards:

#### Distribution signal

* Followers gained
* Reply impressions
* Standalone post impressions
* Profile interest
* Builder/founder engagement

#### Validation signal

* Working-parent/home-cook download (self-reported)
* Kitchen scan
* 3 plausible dinners
* Cook Mode usage
* Qualitative "I would use this" feedback
* Repeated use

Do **not** combine these into one success number.

---

# 14.6 Weekly Review

### Purpose

Turn raw activity into learning.

### Weekly review questions

1. What moved?
2. What got stuck?
3. Did I do the daily reps?
4. What was the best post/reply?
5. What was the worst post/reply?
6. Which content pillar looked strongest?
7. Which content pillar looked weakest?
8. What follower movement happened?
9. What Stir validation happened?
10. Did I get closer to first 5 downloads?
11. Did I reach any working-parent/home-cook testers (self-reported)?
12. What is next week's experiment?
13. What couldn't this tool measure? (counterfactual prompt)

### Auto-filled fields

* Week start/end
* Followers start/end
* Follower delta
* Posts shipped
* Replies shipped
* Reply sessions completed
* Daily reps completed count
* Top posts by impressions
* Top posts by bookmark rate
* Top replies
* Downloads/signups
* ICP testers (self-reported)
* Candidate strongest/weakest pillar (with confidence label)

### User-filled fields

* Interpretation
* What surprised me
* Avoidance notes
* Lesson
* Next week experiment
* Commitments
* **Counterfactual note** — explicit acknowledgment of unmeasurable baseline (platform drift, day-of-week, cohort)

### Agent integration

* Button: "Help me draft next week's experiment hypothesis" — opens Agent Chat with last week's data, top posts, and current open hypotheses pre-loaded. Agent proposes 2-3 candidate hypotheses with sample-size targets and falsification criteria.
* Button: "Help me write the counterfactual note" — opens Agent Chat with the week's data and a prompt asking the agent to list what this week's growth could be explained by *other than* Daniel's actions (platform drift, seasonality, cohort effects, prior posts compounding). Daniel still writes the final note.
* Button: "Suggest the strongest/weakest pillar pick" — agent reviews `v_lane_performance` at moderate-or-above confidence and offers reasoning. Daniel still makes the final call.

### Acceptance criteria

* User can generate Markdown export.
* Export includes enough raw numbers to audit the interpretation.
* Export distinguishes distribution signal from validation signal.
* Export says when sample size is too small.
* Export includes the counterfactual note so future-Daniel doesn't read past-Daniel's interpretation as causal claim.

---

# 14.7 Settings

### Fields

1. **Account**

   * X handle
   * X user ID (stable identifier; populated once known)
   * Profile URL
   * Baseline follower count
   * Baseline date
   * Bio text snapshot

2. **Goals**

   * Operational ceiling (default: 5,000)
   * Long-arc reminder (default: 500,000, display-only)
   * Current distribution milestone
   * Current validation milestone
   * Distribution ladder configuration
   * Validation ladder configuration

3. **Daily reps**

   * Posts/day target
   * Replies/day target (default: 12 — experimental, review on day 21)
   * Reply sessions/day target
   * Calibration review date (auto-set to baseline + 21 days)
   * Weekly review day
   * Snapshot time

4. **Content taxonomy**

   * Pillars (v1 set seeded; editable)
   * Audiences (v1 set seeded; editable)
   * CTAs (v1 set seeded; editable)
   * v2 expansion guidance shown inline

5. **UTM templates**

   * Default source: `x`
   * Default medium options:

     * `profile`
     * `post`
     * `reply`
     * `dm`
   * Campaign examples:

     * `first_5_downloads`
     * `founder_zero_push`
     * `stir_launch_week`

6. **Data sources**

   * X API mode:

     * **manual (default for MVP)**
     * xurl (V1.1)
     * direct API (V1.2)
   * Website analytics:

     * disabled
     * manual
     * CSV
     * API later
   * App downloads:

     * **self-reported (default)**
     * CSV
     * App Store Connect later

7. **Export paths**

   * CSV export folder
   * Markdown weekly reports folder
   * Raw JSON archive folder

8. **Backups**

   * Backup directory (default: `data/backups/`)
   * Backup method: `VACUUM INTO` (safe with open DB)
   * Manual backup button
   * Last backup timestamp

9. **Growth Agent configuration** — see §28 for full spec

   * Anthropic API key status (configured / not set) — actual key in `.env`
   * Model (default `claude-opus-4-7`, configurable)
   * System prompt file path (default `config/agent_system_prompt.md`)
   * Max tokens per response (default 4096)
   * Per-session token budget (default 50,000)
   * Daily cost ceiling in USD (default $5.00)
   * Tokens used today / this week / this month
   * Estimated cost today / this week / this month

---

# 14.8 Agent Chat — NEW

### Purpose

Dedicated conversational interface for strategic distribution work. Use cases: "what's missing this week," "draft three openings for a post about X," "find me five accounts I should be engaging with in the build pillar," "why did this post underperform what I expected?"

Distinct from the inline agent buttons in other views (which invoke specific tools with prefilled context). The Agent Chat is open-ended — you bring a question, the agent uses tools as needed, and the conversation persists.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Agent Chat                                    [+ new session] │
│ Session: Tue May 21 morning planning      Tokens: 4.2k / 50k │
└─────────────────────────────────────────────────────────────┘

[ Sidebar: past sessions list ]    [ Main: conversation ]

User: I want to post something today but I'm not sure what.

Agent: Looking at your last 7 days, your biggest gap is
stir × icp × ask — zero posts. You also have an open
hypothesis (H2) needing one self-pillar post.

[Tool: get_lane_gaps(week_offset=0)]  ▶ click to expand
[Tool: get_open_hypotheses()]          ▶ click to expand

Three options, different angles:

  1. [stir × icp × ask]
     "What I learned watching 4 people stare at their fridges
      this week — one of them did not eat dinner..."
     Aiming for: shared learning (intelligence), restraint on
     the ask (wisdom), 'I learned' not 'I taught' (humility)

  2. [self × icp × none]
     "The neuro-onc surgical AI project started for me at a
      Friday-night fridge stare in 2023. The two arcs aren't
      as separate as they look..."
     Aiming for: identification (intelligence), threads two
     of your pillars (wisdom), no flexing on the long arc
     (humility)

  3. [stir × icp × ask] (short)
     "If you've stared at a fridge for ten minutes waiting
      for a verdict — I'm building something for that. DM
      me if you want the beta link."
     Aiming for: specificity over generality, direct ask
     without performance

Which direction do you want me to develop?

User: do #3 but cut the "DM me" — feels performative.
make the ask softer.

Agent: [generates 3 ending variations]
[Tool: revise_draft(draft_id=12, feedback="cut DM ask")]

  3a. "...I'm building something for that. Link in bio
       if you want to see it."

  3b. "...I'm building something for that. Quietly looking
       for 5 testers — reply if that's you."

  3c. "...I'm building something for that. (No ask. Just
       wanted you to know.)"

User: 3b. let's ship it.

Agent: [opens confirmation flow — see §28.10]
       Final text:
       "If you've stared at a fridge for ten minutes waiting
        for a verdict — I'm building something for that.
        Quietly looking for 5 testers — reply if that's you."
       Pillar: stir × icp × ask
       Voice self-score: I:2 W:3 H:3
       [ Yes, post this ]   [ No, go back ]
```

### Required components

1. **Conversation pane**
   - Uses `st.chat_message` and `st.chat_input` (Streamlit 1.30+).
   - Streaming responses (tokens appear as generated).
   - Scrollable, persists in `agent_messages` per session.

2. **Tool call transparency**
   - Every tool invocation rendered as a collapsible block.
   - Default collapsed; click to expand and see exact input + output.
   - This is non-negotiable. The user must see what data the agent pulled and what it did with it.

3. **Inline draft actions**
   - For any draft mentioned in conversation: `[ Save as draft ]` `[ Confirm + post ]` `[ Discard ]` buttons.
   - Save as draft → `agent_drafts.status = proposed`, also persisted to `posts` with `manual_confirmation_status = draft`.
   - Confirm + post → triggers two-step confirmation flow (§28.10).
   - Discard → `agent_drafts.status = rejected`.

4. **Session sidebar**
   - List of past sessions, most recent first.
   - Click to resume (loads all `agent_messages` for that session_id).
   - Each session has a name (user can rename) and a timestamp.

5. **New session button**
   - Generates fresh `session_id`.
   - Re-applies the system prompt cleanly (no leak from previous session).
   - Past sessions remain accessible in the sidebar.

6. **Token + cost indicator**
   - Top right: cumulative tokens this session and budget remaining.
   - Settings view shows daily/weekly/monthly aggregates.

7. **Edit-in-place for drafts**
   - User can edit any draft text inline before confirming.
   - Edits captured: `agent_drafts.status = accepted_with_edits`, edited text stored.

### Acceptance criteria

- Streaming responses (no waiting for full response before seeing anything).
- Tool calls visible and inspectable.
- Confirmation flow re-displays exact text before posting, every time.
- Sessions persist across browser refreshes and Streamlit reruns.
- New session starts fresh — no leakage from previous session into context.
- Token usage visible per session and aggregated in Settings.
- Direct keyboard shortcut to focus chat input (configurable).

---

## 15. Manual entry workflows

**Manual entry is the MVP default**, not the fallback. Every form below is a first-class workflow.

### 15.1 Daily account snapshot — MVP default path

Pinned to the top of the Today view until today's snapshot exists.

Fields:

```text
date          (auto-filled to today)
time          (auto-filled to now)
followers_count
following_count
post_count
listed_count
like_count    (optional)
media_count   (optional)
bio_text      (optional, prefilled from yesterday)
profile_url   (auto-filled from settings)
screenshot_path (optional)
note          (optional)
```

Rules:

* Store `source = manual`, `data_quality = manual`.
* If a snapshot for today already exists, this form becomes an "edit" link that creates a correction record (not an overwrite).
* Designed to take 30 seconds.

### 15.2 Manual reply logging

Use after Daniel replies directly on X.

Fields:

```text
reply_url
reply_text
created_at approximate
in_reply_to_user
conversation_url optional
pillar
audience
cta
why this target?
hypothesis
expected signal
```

Optional later fields:

```text
impressions
likes
replies
bookmarks
profile clicks
actual signal
lesson
```

Important: manual replies are not second-class. For early distribution, this may be the most important activity table.

---

### 15.3 Content tagging

Use for any post/reply after it is logged.

Required:

```text
pillar       (v1: stir / build / self)
audience     (v1: icp / other)
cta          (v1: ask / none)
why_posted
hypothesis
expected_signal
```

Later:

```text
actual_signal
lesson
```

The dashboard should show untagged posts as a queue:

```text
Needs classification: 4 posts/replies
```

---

### 15.4 Stir conversion event logging

Use when a download/signup/tester/feedback happens.

Fields:

```text
event_category    (acquisition / activation / usage / feedback)
event_type        (free text)
date/time
source            (self-reported by tester if applicable)
attribution_method (self_reported / utm / referrer_header / inferred / unknown)
campaign
referring post/reply if known
is likely ICP? (only settable when attribution_method = self_reported)
working parent? (only settable when attribution_method = self_reported)
what did they do?
feedback
notes
```

Classification examples:

| Event                                                    | Category    | Validation strength          |
| -------------------------------------------------------- | ----------- | ---------------------------- |
| AI builder follows Daniel                                | (not stir)  | distribution only            |
| AI builder downloads Stir but does not use               | acquisition | weak product signal          |
| Working parent (self-reported) downloads Stir            | acquisition | stronger acquisition signal  |
| Working parent (self-reported) scans kitchen             | activation  | strong activation signal     |
| Working parent gets 3 plausible dinners                  | activation  | stronger product signal      |
| Working parent uses Cook Mode                            | usage       | strongest early usage signal |
| Tester gives unprompted "I would use this" feedback      | feedback    | qualitative product signal   |

---

### 15.5 Weekly review workflow

1. Select week.
2. Dashboard auto-fills quantitative summary.
3. User chooses:

   * best post,
   * best reply,
   * worst post,
   * strongest pillar (only from lanes at "moderate" or above confidence),
   * weakest pillar.
4. User writes interpretation.
5. **User writes counterfactual note** — what couldn't this tool measure this week?
6. User defines next experiment.
7. Export Markdown.

Optional agent assistance at steps 4-6: each user-filled field has a "Draft this section" button (§14.6) that calls `draft_weekly_review_section`. Draft is editable before save.

---

## 16. Import/export requirements

### CSV import

Support CSV import for:

1. `account_snapshots`
2. `posts`
3. `post_metric_snapshots`
4. `daily_activity`
5. `stir_conversion_events`
6. `stir_testers`

Each import should:

* Preview rows before committing.
* Validate required columns.
* Detect duplicates.
* Store import metadata.
* Allow rollback for the import batch.

### CSV export

Support exports for:

1. Account snapshots
2. Posts with latest metrics — **column allowlist** (see below); the new publish flow adds columns that are NOT in the default export.
3. Daily activity
4. Stir conversion events
5. Weekly reviews
6. Full database dump summary — excludes the carve-outs in (7).
7. **Excluded from default export (opt-in required):**
   - `posts.publish_last_error` — may contain X API diagnostic strings or credential-adjacent error text.
   - `posts.published_via_agent_message_id` — joins to `agent_messages` reveal agent chat content for the publish.
   - `publish_confirmation_tokens` — entire table is non-exportable. Tokens are ephemeral security material; even hashes are not in the export surface.
   - `agent_messages`, `agent_tool_calls`, `agent_drafts` — agent audit log. Requires "Export agent audit" action (8) below.

**`posts` default-export column allowlist:** `id, x_post_id, created_at_utc, created_date, text, url, type, conversation_id, in_reply_to_post_id, in_reply_to_user, posted_via, manual_confirmation_status, contains_link, expanded_urls_json, utm_source, utm_medium, utm_campaign, utm_content, utm_term, created_in_app_at, agent_draft_id, published_to_x_at, publish_method, publish_attempt_count`. Adding `publish_last_error` / `published_via_agent_message_id` requires the opt-in toggle in Settings → Export.

8. **Export agent audit** — separate action with its own confirmation modal (mirrors the publish flow's confirmation pattern). Surfaces `agent_messages`, `agent_tool_calls`, `agent_drafts` as a single timestamped archive. Daniel must check "I understand this archive contains chat content, tool calls, and self-scores — share only with parties who should see Daniel's drafts" before the export runs. Controlled by `x_posting_audit_export_requires_explicit_action = true` (§10.2 settings; non-zero default).

### JSON export

Support raw JSON archive export for:

```text
raw_api_responses
settings
content taxonomy
milestones (both ladders)
```

### Markdown export

Weekly report export path:

```text
/Users/daniel/Documents/Obsidian Vault/Stir/X Growth Weekly Reviews/
```

Actual path configurable.

---

## 17. Scheduling requirements

### MVP: nothing is scheduled

Manual entry is the default. There is no cron job, no `launchd` plist, no API to poll. The daily ritual is opening the dashboard at 9 AM, entering the snapshot in the pinned form, and reviewing the day's plan.

The original spec mandated scheduled jobs from day one. That assumed API-first collection. With manual-first collection, scheduling is a V1.1 concern.

### V1.1: account snapshot via xurl

When the manual loop has run for 2-4 weeks and the value is proven:

```text
Default: every day at 9:00 AM America/New_York
Behavior:
  1. Fetch account metrics via xurl.
  2. Store raw response.
  3. Insert immutable account snapshot.
  4. Log success/failure.
  5. If failure, the manual form remains the fallback (already the MVP default).
```

### V1.2: recent post collection

```text
Default: every day after account snapshot
Behavior:
  1. Fetch recent posts from Daniel.
  2. Insert new posts if not already known.
  3. Do not overwrite manually added metadata (pillar, audience, hypothesis).
  4. Preserve raw response.
```

### V1.2: post metric refresh

```text
Daily: refresh metrics for posts from last 14 days.
Weekly: refresh metrics for posts from last 90 days.
Monthly: refresh old milestone posts only.
```

Reason: avoid unnecessary API usage and cost.

### Weekly report reminder

Default:

```text
Sunday evening or Monday morning
```

No push notification required. A "Weekly review due" banner in the app is enough. This works in MVP because the user opens the app daily.

---

## 18. Privacy and security notes

1. Store API keys/tokens in `.env`, OS keychain, or xurl config.
2. Never store secrets in SQLite tables.
3. Add these to `.gitignore`:

   ```text
   data/
   .env
   *.sqlite
   *.sqlite-journal
   *.sqlite-wal
   *.sqlite-shm
   raw_api/
   exports/
   backups/
   ```
4. Raw API responses may contain content and identifiers; do not publish them.
5. Tester records should use aliases by default.
6. Avoid storing unnecessary personal data about working parents/home cooks.
7. **Do not infer sensitive attributes.** Working-parent / home-cook / ICP classification is only stored when self-reported. There is no `inferred_low` confidence level.
8. Do not use scraping/browser automation for X data collection.
9. Attribution confidence is limited to:

   * `self_reported` (the tester told you)
   * `utm` (URL parameters)
   * `referrer_header` (HTTP referrer)
   * `inferred` (you guessed, treat as low confidence)
   * `unknown`
10. Add local backup support:

    * Daily `VACUUM INTO` backup to `data/backups/x_growth_YYYYMMDD_HHMMSS.sqlite`.
    * **Never** use `cp` of an open SQLite file — it can corrupt.
    * Optional encrypted external backup later.

11. **Anthropic API key for Growth Agent:**

    * Stored in `.env` as `ANTHROPIC_API_KEY`, never in SQLite.
    * `.gitignore` already covers `.env`.
    * Settings view shows status as "configured" / "not set" — never displays the key itself.
    * Agent refuses to send requests if key is missing or invalid; surfaces error in chat with link to Settings.

12. **Anthropic API request logging:**

    * `agent_messages` stores model, input tokens, output tokens for cost auditability.
    * No request bodies sent to third-party logging services.
    * If a request fails, store the error message but not the failing payload (avoids storing potentially sensitive content twice).

13. **Agent data scope (least privilege):**

    * Agent's read-access tools (`get_open_hypotheses`, `summarize_winners`, `get_lane_gaps`, `analyze_post`) work over `posts`, `post_metric_snapshots`, `post_classifications`, `experiments`, `agent_target_accounts`, `daily_activity`, `v_lane_performance`.
    * Agent does NOT have a tool that reads `stir_testers` (PII) or `stir_conversion_events.qualitative_feedback` (testers' words). Testers' confidences are not the agent's to weaponize.
    * Agent does NOT have a tool that reads `publish_confirmation_tokens` or `st.session_state`. The publish-flow token registry is unreachable from the agent loop by construction (§28.2 rule #10).
    * Agent does NOT have write access to `settings`, `milestones`, or `account_snapshots` — only to its own tables (`agent_drafts`, `agent_messages`) and to `posts` (drafts only, never confirmed posts without two-step confirm).

16. **X API OAuth credentials** (consumer key/secret, access token/secret) stored in `.env` only. Never in DB, never logged, never included in `raw_api_responses` exports. Loaded by `app/x_client.py` (new module) only when a publish call fires.

17. **X API rate limits** enforced client-side before each publish call to avoid hitting X-side limits and triggering account flags. Default: 10 publishes/hour, 50/day. Adjustable in Settings.

18. **Publish-flow export carve-out** (per §16 (7) and §16 (8)):
    * `posts.publish_last_error` and `posts.published_via_agent_message_id` are EXCLUDED from default CSV export. Opt-in toggle in Settings → Export for debugging.
    * `publish_confirmation_tokens` is non-exportable. Tokens (even hashed) are ephemeral security material and have no value outside the live runtime.
    * `agent_messages`, `agent_tool_calls`, `agent_drafts` require the separate "Export agent audit" action with its own confirmation modal — never bundled into the default `posts` export.
    * The default-export `posts` column allowlist is the canonical surface; any new sensitive column added to `posts` in a future revision defaults to EXCLUDED until the spec explicitly promotes it.

19. **`publish_confirmation_tokens` storage hygiene:**
    * Raw UUIDs are NEVER persisted — only `token_hash` (SHA-256). The click-handler holds the raw token in a local stack frame for the duration of the synchronous tool call and then discards it.
    * Tokens are bounded by `x_posting_confirmation_token_ttl_seconds` (60s default). A daily VACUUM removes expired-and-unconsumed rows; consumed rows stay for audit (joined via `consumed_by_x_post_id`).
    * The `agent_tool_calls.arguments_json` for publish tools MUST be redacted to `{"post_id": N, "confirmation_token_id": M}` with `redacted_arguments = true` (§28.2 rule #11).

---

## 19. MVP scope

### Must ship

1. Local SQLite database with `migrations/001_initial.sql`.
2. Streamlit app with **nine** views:

   * Today / Weigh-In (with pinned manual snapshot form)
   * **Next Rep** (new)
   * Progress
   * Content Performance
   * Funnel
   * Weekly Review
   * Settings
   * **Agent Chat** (new — §14.8)
   * **Reply Target Queue** (new — §29.7)
3. Manual account snapshot entry as the default path.
4. Manual post/reply logging.
5. Post tagging (v1 taxonomy: 3 pillars × 2 audiences × 2 CTAs):

   * pillar,
   * audience,
   * CTA,
   * hypothesis,
   * lesson.
6. Daily reps tracker (with raised, experimental targets and 21-day calibration prompt).
7. Stir conversion event tracker (4-category schema, free-text event_type).
8. **Dual milestone ladders** — distribution and validation, equal depth.
9. 7-day and 30-day trend calculations with noise-floor suppression.
10. `v_lane_performance` view with graduated confidence labels.
11. CSV export.
12. Markdown weekly report export (with counterfactual prompt).
13. Correction/annotation support.
14. `VACUUM INTO` backup script.
15. `st.connection`-based database access pattern.
16. **Growth Agent core** (§28):

    * `agent_drafts`, `agent_messages`, `agent_target_accounts` tables.
    * `posts.agent_draft_id` column and `agent_assisted` value for `posted_via`.
    * System prompt loaded from `config/agent_system_prompt.md`.
    * Tool functions: `draft_post`, `draft_reply`, `analyze_post`, `summarize_winners`, `get_open_hypotheses`, `get_lane_gaps`, `save_draft`, `revise_draft`, `submit_post` (manual mode).
    * `find_reply_targets` from `agent_target_accounts` table (curated list, manual mode).
    * Two-step confirmation flow with text re-display before posting.
    * Tool call transparency in Agent Chat.
    * Token + cost tracking per session.
    * Daily cost ceiling enforcement.
17. **Agent integration buttons** in Today, Next Rep, Content Performance, Weekly Review views.

19. **X API posting integration** with OAuth 1.0a user-context auth (`app/x_client.py`).
20. **Publish flow UI** in §14.8 Growth Agent Chat: confirmation modal with character count, exact-text display, single-use confirmation token, rate-limit display.
21. **`publish_post_to_x` and `publish_reply_to_x` tools** (§28.4 tools #10-11) with rate limiting and audit logging.

### Should ship

1. Backup automation (cron or launchd entry for daily `VACUUM INTO`).
2. UTM extraction from links.
3. First 5 downloads tracker.
4. Consistency calendar.
5. Content lane summary with graduated confidence.
6. Next Rep view's open-hypothesis tracker.
7. Agent session naming and search.
8. Agent draft queue visible in Next Rep view alongside manual drafts.

11. **Scheduled publish drafts** — draft now, schedule the publish for later (still requires fresh confirmation at publish time, not at schedule time).

12. **Drafting Intelligence Pack (Phase 5.8 — see §28.11 through §28.15):**

    * Pre-publish heuristic scorer (`prepublish_scores` table + `app/agent/prepublish_scorer.py`) with `composite_label` chips in Today, Next Rep, Agent Chat, and a historical view in Content Performance.
    * Generated voice profile (`voice_profiles` table + `app/agent/voice_profile.py`) with the Settings → Growth Agent regeneration panel and prompt-builder splice.
    * Repetition guard (`post_embeddings` table + `app/agent/repetition_guard.py` + `scripts/embed_posts.py` backfill) with the `similarity_warning_json` banner above the draft text.
    * Confidence labels on agent outputs (`agent_drafts.confidence_label` + `agent_messages.confidence_label` + `app/agent/confidence_patterns.py`) enforced by orchestrator parsing.
    * Approval payload hash — user-visible enforcement (extension to §28.10 modal behavior, no new table).

### Can wait — V1.1+

1. xurl-based account snapshot collection.
2. xurl-based recent post import.
3. xurl-based post metric refresh.
4. Raw response preservation pipeline (table exists, empty until V1.1).
5. Direct OAuth UI inside app.
6. Auto-posting.
7. Website analytics API integration.
8. App Store Connect API integration for Stir download data.
9. Automatic follower classification.
10. Recommendation engine.
11. v2 taxonomy expansion (when data justifies it).
12. **Agent `submit_post` in true API mode** — direct posting via X API (MVP uses clipboard handoff + intent URL).
13. **Agent `find_reply_targets` using X API search** — surfaces real-time posts in lane (MVP uses curated `agent_target_accounts`).
14. **Agent auto-pulls metrics** on agent-shipped posts after a delay (currently same flow as manual posts).

---

## 20. V1 roadmap

After MVP works for 1–2 weeks.

### V1.1 — API collection layer

* xurl-based account snapshot collector.
* Store raw response.
* Switch `data_collection_mode` setting to `xurl`.
* Manual form remains, but stops auto-pinning when API snapshots are succeeding.
* API cost/request tracking.
* Scheduled job status page.
* Better error handling and retry logs.

### V1.2 — Direct API client

* Direct X API Python client (replaces xurl for higher-volume needs).
* Post metric refresh on schedule.
* Raw response browser UI.
* Cost dashboard.

### V1.3 — Better analysis

* Standalone vs reply comparison.
* "Rep adherence vs follower trend" analysis.
* Experiment tracking with explicit hypothesis lifecycle.
* v2 taxonomy migration tooling.

### V1.4 — Better Stir funnel

* Website analytics CSV/API import.
* Download source import.
* UTM campaign dashboard.
* Tester lifecycle view:

  * lead,
  * download,
  * scan,
  * plausible dinners,
  * Cook Mode,
  * feedback.

### V1.5 — Better workflow

* Reply session planner.
* Target account list management.
* Manual reply queue.
* "Needs tagging" inbox auto-populated.
* Weekly review auto-draft.

---

## 21. V2 roadmap

### V2.1 — Experiment engine

* Define experiment:

  * hypothesis,
  * lane,
  * expected signal,
  * sample size,
  * timeframe.
* Compare result to expectation.
* Archive lessons.

### V2.2 — Qualitative signal library

* Screenshots of good replies.
* Screenshots of Stir feedback.
* Before/after bio/pinned-post changes.
* "Progress photo" gallery for distribution.

### V2.3 — Content strategy assistant

* Suggest under-tested lanes (Next Rep view's smarter cousin).
* Flag overfitting.
* Recommend next week's distribution experiment.
* Still require Daniel to write interpretation.

---

## 22. Edge cases

| Edge case                                      | Required behavior                                                     |
| ---------------------------------------------- | --------------------------------------------------------------------- |
| Missed daily snapshot                          | Show missing day; allow manual backfill                               |
| Multiple snapshots same day                    | Pick canonical by closest configured snapshot time                    |
| Manual snapshot, then later API snapshot       | Both stored; canonical chosen by `daily_snapshot_time` proximity      |
| API returns partial metrics                    | Store partial, label unavailable fields                               |
| API fails                                      | Log failure, manual form remains available (already the default)      |
| X handle changes                               | Store stable `x_user_id` when available; keep username history        |
| Post deleted                                   | Keep historical record, mark deleted/unavailable                      |
| Manual reply has no post ID                    | Store with `manual_confirmation_status = needs_id`                    |
| Reply URL entered later                        | Merge with existing manual row                                        |
| Duplicate import                               | Detect by post ID/date/text hash                                      |
| Follower count decreases                       | Show honestly; no special handling except trend warning               |
| Viral outlier in a lane                        | Lane confidence label drops to "outlier-dominated"; median suppressed |
| One post dominates lane (>50% of impressions)  | Flag lane as outlier-dominated; ranking disabled                      |
| Link has no UTM                                | Store `utm_* = null`; do not infer campaign unless manually annotated |
| Shortened URL                                  | Preserve original and expanded URL if available                       |
| Download source unknown                        | Store `attribution_method = unknown`; do not attribute to X           |
| ICP status unclear                             | Leave `is_likely_icp` null; do not infer                              |
| Builder downloads Stir                         | Count as download; not automatically ICP validation                   |
| Working parent gives feedback without download | Count qualitative validation separately                               |
| App Store data delayed                         | Show data latency note                                                |
| Timezone mismatch                              | Normalize UTC internally, display America/New_York                    |
| `delta_7d` < 10 followers                      | Suppress velocity; show "trend not yet measurable"                    |
| Lane has 5-14 posts                            | Show scatter + IQR; suppress ordinal ranking                          |
| Weekly review export with no counterfactual note | Block export; prompt user to fill it in                             |
| Anthropic API key missing or invalid           | Agent view shows "Configure API key in Settings"; no requests sent  |
| Anthropic API timeout / 5xx error              | Show error in chat with retry button; conversation state preserved  |
| Agent generates a draft that fails voice bars  | Agent self-flags and proposes revision; refuses to ship if asked    |
| User tries to confirm without an active draft  | UI button disabled; no confirmation_token issued                    |
| User double-clicks confirm                     | Token is single-use; second click returns "already posted/copied"   |
| Daily Anthropic cost ceiling hit               | New sessions blocked; current session can finish; banner shown      |
| Per-session token budget approaches limit      | Warning at 80%, hard stop at 100% (offer to start new session)      |
| Agent tool call returns DB error               | Surface to agent so it can recover; don't silently swallow          |
| User edits draft after agent generated it      | Edited text is what ships; `accepted_with_edits` recorded           |
| Agent in manual mode tries to call X API       | Refuse; explain mode mismatch                                       |
| Session_id collision (unlikely)                | UUIDs are used; collision treated as error and logged               |
| Streamlit rerun during agent streaming response | Persist partial response to `agent_messages`; on resume, complete   |
| User asks agent to write engagement-bait       | Agent refuses, explains voice bars, offers alternative              |
| Target post for reply has been deleted         | Agent flags it; offers to draft based on cached text or abandon     |
| X API publish fails (auth)                       | Log `publish_last_error`, surface in chat, disable publish until Daniel re-enters credentials |
| X API publish fails (rate limit)                 | Bounded internal retry up to `x_posting_publish_retry_attempts_per_token` (default 2) with exponential backoff; if still failing, surface to Daniel and ROLLBACK (token stays unconsumed if within TTL). External observability: one Daniel click → at most one live X post. |
| X API publish fails (text too long, content blocked) | Surface error in chat; draft stays as draft; Daniel can edit and re-attempt |
| Network failure during publish                   | Three distinct sub-cases: (a) X API succeeded, response not received → mark token unconsumed-but-attempted, set `publish_last_error = 'network: response missing'`, increment `publish_attempt_count`; crash-recovery routine on next app boot calls `GET /2/users/:id/tweets?since_id=<last_known>` and matches by text hash to reconcile (§28.10 publish flow step 8). (b) X API succeeded, DB write threw mid-transaction → ROLLBACK the DB changes BUT do NOT roll back the token consumption flag in memory; surface "publish state unknown — verify on X" banner; reconciliation routine handles next boot. (c) Reconciliation `GET /2/users/:id/tweets` itself failed → keep banner; Daniel can manually mark-as-resolved with the live X URL. |
| Confirmation token expired (>60s)                | Reject publish call; show "Confirmation expired — click Publish again". Token stays in `publish_confirmation_tokens` until daily VACUUM cleans expired-unconsumed rows. |
| Confirmation token reused                        | Reject publish call (rule #10 check (c): `consumed_at_utc` is non-null); log security warning to `agent_tool_calls` with `status = error`, `redacted_arguments = true`; require fresh token via fresh UI click |
| Draft edited after confirmation token generated  | Rule #10 check (d) fails on `sha256(posts.text) != draft_text_hash_at_issue`; reject publish; require fresh confirmation with updated text. The hash mismatch is the enforcement mechanism — the click-handler does not need a separate "watch for edits" loop. |
| `x_posting_enabled = false` in settings          | All publish buttons greyed; agent can still draft normally. Modal-open state is also rejected if the flag transitions to false between modal open and submit (re-check on click). |
| `x_posting_enabled` toggled OFF between modal open and submit | The "Publish now" click handler MUST re-check `x_posting_enabled` after generating the token. If false: do NOT create the `publish_confirmation_tokens` row; surface "Publishing was disabled — re-enable in Settings to continue"; modal closes. |
| Agent attempts to generate its own confirmation token | Rejected by construction: the publish tools are NOT in the agent-facing tool registry (`AGENT_TOOLS`). The agent has no schema slot to mint a token into. Any attempt manifests as the agent emitting a chat message asking Daniel to click Publish, never as a tool call. If somehow a tool call to `publish_post_to_x` is invoked from agent context (regression), the tool dispatcher rejects with `status = error`, `redacted_arguments = true`, and logs a security warning. |
| Draft fails intelligence/wisdom/humility check three times | Orchestrator (NOT the agent) refuses on attempt `iwh_max_revision_attempts + 1`; do not save; emit refusal message to chat; suggest a different angle. The agent cannot game its own count (see §28.2 rule #13). |
| Dark-pattern lint pass flags a draft              | Treated as a failed IWH revision (counts toward `iwh_max_revision_attempts`). Send back to the agent with the lint's one-line reason as feedback; agent must rewrite without the flagged pattern. |
| Publish invoked on already-confirmed row (double-publish attempt) | Rule #10 check (f) fails: `manual_confirmation_status != 'draft'` because the prior successful publish transitioned it to `confirmed`. Reject with explicit "already published — see x_post_id=<existing>" error; do NOT call X API; log `agent_tool_calls` with `status = error`. |
| Draft `post_id` deleted (via "Discard" button) while confirmation modal is open | Click handler re-fetches `posts` row before generating the token. If the row no longer exists (or its `manual_confirmation_status` is no longer `draft`), refuse to generate the token; close modal; surface "Draft no longer available — re-draft to continue". |
| Concurrent path changed `posts.manual_confirmation_status` between modal open and submit | Rule #10 check (f) fails on the atomic re-check inside the publish transaction. Reject; surface "Draft state changed concurrently — re-draft or refresh and try again". |
| Reconciliation banner present at app boot      | App displays a top-of-Today-view banner: "Publish state unknown for post_id={N} — verify on X". Daniel either clicks "Mark resolved (with X URL)" to write the reconciliation row manually, or "Dismiss" to leave the row in limbo and re-attempt the publish-flow on a fresh draft. |
| `publish_confirmation_tokens` table grows unbounded | Daily VACUUM job (§17 scheduling) deletes rows where `consumed_at_utc IS NULL AND expires_at_utc < now() - 24h`. Consumed rows stay (joined via `consumed_by_x_post_id` for audit). |

Reply-target-discovery edge cases are catalogued in their own table at §29.11; see there for target-deletion, lint-override, threshold-misfire, candidate-expiry, and CSV-import behaviors.

---

## 23. Example charts

### 23.1 Follower trend

```text
Chart type: line
X-axis: date
Y-axis: followers

Series:
- raw follower count
- 7-day trend
- 30-day trend

Annotations:
- baseline: 61
- current milestone: 100
- distribution milestones: 100, 250, 500, 1k, 2.5k
- operational ceiling: 5k (horizontal line)
```

### 23.2 Distribution and validation ladder progress

```text
Chart type: paired horizontal progress bars

Distribution ladder:
  61 → 100:        [████░░░░░░░░░░░░░░░░] 7.7%
  100 → 250:       [░░░░░░░░░░░░░░░░░░░░] 0%
  ...

Validation ladder:
  first download:           [░░░░░░░░░░░░░░░░░░░░] 0%
  first 5 downloads:        [░░░░░░░░░░░░░░░░░░░░] 0%
  first ICP tester:         [░░░░░░░░░░░░░░░░░░░░] 0%
  ...
```

Both ladders shown side by side, equal visual weight.

### 23.3 Reps calendar

```text
Chart type: calendar heatmap

Cell states:
- complete
- partial
- missed
- no data
```

### 23.4 Content lane matrix with IQR

```text
Rows: pillar (× audience × cta optionally)
Columns:
- count
- days covered
- median impressions + IQR bar
- median engagement rate + IQR bar
- total bookmarks
- total Stir signals
- confidence label
```

The IQR bar visualizes spread. A lane with median 245 and IQR 110-620 looks visibly noisier than one with median 245 and IQR 230-260, even though the medians match.

### 23.5 Post scatterplot

```text
X-axis: impressions
Y-axis: engagement rate
Point color/group: pillar
Point shape: post type
Tooltip:
- text preview
- CTA
- audience
- lesson
```

This view is the primary tool when sample size is "insufficient" or "low" — show the data, refuse to summarize prematurely.

### 23.6 Funnel waterfall

```text
X impressions
→ profile visits
→ link clicks
→ getstir.app visits        (UTM-attributed)
→ downloads                  (self-reported source)
→ kitchen scans
→ Cook Mode usage
→ ICP testers (self-reported)
```

Each stage shows source quality:

```text
exact / partial / manual / self-reported / inferred / unavailable
```

The transition from UTM-attributed visits to self-reported downloads is the App Store attribution gap — make this asymmetry visible, not hidden.

---

## 24. Example weekly report format

```markdown
# X Growth Weekly Review — Week of 2026-05-18

## 1. Summary

Baseline followers: 61
Start followers: {{followers_start}}
End followers: {{followers_end}}
Follower delta: {{follower_delta}}
Current distribution milestone: 100
Distance to milestone: {{distance_to_100}}
Operational ceiling: 5,000 (distance: {{distance_to_ceiling}})

Current validation milestone: {{validation_milestone_name}}
Validation progress: {{validation_progress}}

Daily reps completed: {{daily_reps_days_completed}} / 7
Posts shipped: {{posts_shipped}}
Replies shipped: {{replies_shipped}} (target: {{daily_reply_target}}/day × 7)
Reply sessions completed: {{reply_sessions_completed}}

Stir downloads: {{downloads}}
X-attributed downloads (self-reported): {{x_attributed_downloads}}
Working-parent/home-cook testers (self-reported): {{working_parent_home_cook_testers}}
Kitchen scans: {{kitchen_scans}}
Cook Mode starts: {{cook_mode_started}}

## 2. What moved?

{{what_moved}}

## 3. What got stuck?

{{what_got_stuck}}

## 4. Rep adherence

- Posts target met: {{post_target_days}} / 7
- Replies target met: {{reply_target_days}} / 7  (target: {{daily_reply_target}}/day)
- Reply session target met: {{session_target_days}} / 7

Interpretation:

{{rep_interpretation}}

## 5. Best post/reply

Post/reply: {{best_post_url}}

Type: {{best_post_type}}
Pillar: {{best_post_pillar}}
Audience: {{best_post_audience}}
CTA: {{best_post_cta}}

Metrics:
- Impressions: {{best_post_impressions}}
- Likes: {{best_post_likes}}
- Replies: {{best_post_replies}}
- Bookmarks: {{best_post_bookmarks}}
- Engagement rate: {{best_post_engagement_rate}} {{approx_label_if_computed}}

Why it worked / might have worked:

{{best_post_lesson}}

## 6. Worst or most disappointing post/reply

Post/reply: {{worst_post_url}}

What I expected:

{{worst_expected_signal}}

What happened:

{{worst_actual_signal}}

Lesson:

{{worst_lesson}}

## 7. Content lanes

Strongest apparent lane (confidence: {{strongest_confidence}}):

{{strongest_pillar}}

Evidence:

{{strongest_pillar_evidence}}

Weakest apparent lane (confidence: {{weakest_confidence}}):

{{weakest_pillar}}

Evidence:

{{weakest_pillar_evidence}}

Sample-size note:

{{sample_size_warning}}

## 8. Distribution vs validation

### Distribution signal

{{distribution_signal}}

### Validation signal

{{validation_signal}}

Important distinction:

{{distinction_note}}

## 9. First 5 downloads tracker

{{first_5_downloads_status}}

Download notes:

{{download_notes}}

## 10. Next week's experiment

Hypothesis:

{{next_week_hypothesis}}

Plan:

{{next_week_plan}}

Minimum reps:

{{next_week_minimum_reps}}

Success criteria:

{{next_week_success_criteria}}

What would falsify this?

{{next_week_falsification}}

## 11. What this tool could not measure

(Required field — counterfactual acknowledgment.)

{{counterfactual_note}}

Examples of things this tool cannot tell you:
- Whether you would have grown anyway from platform drift or cohort effects.
- Whether a follower came from a specific post or from cumulative bio/timeline exposure.
- Whether non-X channels (DMs, friends, Obsidian sharing) drove any download.
- Whether a post that "did poorly" would have done well at a different time.

## 12. One sentence lesson

{{one_sentence_lesson}}
```

---

## 25. Implementation checklist

### Phase 0 — Project setup

* [ ] Create project folder.
* [ ] Create SQLite database via `migrations/001_initial.sql`.
* [ ] Create `app/db.py` with `st.connection` wrapper.
* [ ] Create `.env.example`.
* [ ] Create `.gitignore` with sqlite WAL/SHM patterns.
* [ ] Create seed settings:

  * handle,
  * baseline followers,
  * operational ceiling (5,000),
  * long arc reminder (500,000),
  * distribution milestones (6 rungs),
  * validation milestones (6 rungs),
  * daily rep targets (raised, with calibration date),
  * v1 content pillars/audiences/CTAs,
  * data_collection_mode = "manual".
* [ ] Create local data folders:

  * `data/raw_api`
  * `data/exports`
  * `data/weekly_reports`
  * `data/backups`

### Phase 1 — Core database

* [ ] Implement `settings`.
* [ ] Implement `account_snapshots` with `x_user_id`-preferred unique index.
* [ ] Implement `account_snapshot_corrections`.
* [ ] Implement `raw_api_responses` (empty until V1.1).
* [ ] Implement `posts`.
* [ ] Implement `post_metric_snapshots`.
* [ ] Implement `post_classifications` (v1 taxonomy text columns).
* [ ] Implement `daily_activity`.
* [ ] Implement `reply_sessions`.
* [ ] Implement `stir_conversion_events` with 4-category schema.
* [ ] Implement `stir_testers` (self-report-only sensitive columns).
* [ ] Implement `milestones` with `category` and `ladder_position`.
* [ ] Implement `weekly_reviews` with `counterfactual_note` column.
* [ ] Add indexes.
* [ ] Add computed views: `v_account_daily`, `v_post_latest_metrics`, `v_daily_reps`, `v_funnel_daily`, `v_lane_performance`.

### Phase 2 — Manual workflows (MVP default path)

* [ ] Manual account snapshot form (pinned in Today view).
* [ ] Manual reply/post logging form.
* [ ] Content classification form (v1 taxonomy).
* [ ] Daily reps form.
* [ ] Stir conversion event form (with `attribution_method` field).
* [ ] Correction form.
* [ ] "Needs tagging" queue.
* [ ] "Needs post ID" queue.

### Phase 3 — Dashboard views

* [ ] Today / Weigh-In view (with pinned snapshot form and dual ladder status).
* [ ] **Next Rep view (new).**
* [ ] Progress view (with both ladders and long-arc footer).
* [ ] Content Performance view (with graduated confidence labels and IQR).
* [ ] Funnel view (with App Store attribution gap visible).
* [ ] Weekly Review view (with counterfactual prompt).
* [ ] Settings view.
* [ ] Add trend warnings.
* [ ] Add graduated sample-size warnings.
* [ ] Add distribution-vs-validation split.

### Phase 4 — Backup and data hygiene

* [ ] `scripts/backup_db.py` using `VACUUM INTO`.
* [ ] Settings UI button for manual backup.
* [ ] Last backup timestamp shown in Settings.
* [ ] `.gitignore` covers sqlite + backups + raw_api.

### Phase 5 — Export

* [ ] CSV export for account snapshots.
* [ ] CSV export for posts/latest metrics.
* [ ] CSV export for daily activity.
* [ ] CSV export for Stir events.
* [ ] Markdown weekly report export with counterfactual section.
* [ ] Raw JSON export.
* [ ] Export path configuration.

### Phase 5.5 — Growth Agent (see §28 for full spec)

* [ ] Create `agent_drafts`, `agent_messages`, `agent_target_accounts` tables.
* [ ] Add `agent_draft_id` column and `agent_assisted` enum value to `posts`.
* [ ] Create `config/agent_system_prompt.md` from the prompt in §28.3.
* [ ] Implement `app/agent/` module:

  * [ ] `client.py` — Anthropic API client with streaming support.
  * [ ] `tools.py` — tool function registrations and handlers.
  * [ ] `session.py` — session management, message persistence, system prompt application.
  * [ ] `confirmation.py` — confirmation_token generation and validation, two-step flow.
  * [ ] `cost.py` — token tracking, daily ceiling enforcement.
* [ ] Implement tool functions:

  * [ ] `draft_post(pillar, audience, cta, hypothesis, seed_topic, length_target, variation_count)`
  * [ ] `draft_reply(target_post_url, target_post_text, target_author, intent, link_to_own_post, own_post_url)`
  * [ ] `find_reply_targets(lane, count, recency_hours)` — MVP: from `agent_target_accounts`
  * [ ] `analyze_post(post_id_or_x_post_id)`
  * [ ] `summarize_winners(window_days, lane_filter, confidence_minimum)`
  * [ ] `get_open_hypotheses()`
  * [ ] `get_lane_gaps(week_offset)`
  * [ ] `save_draft(text, pillar, audience, cta, hypothesis, type, target_post_url, agent_reasoning)`
  * [ ] `revise_draft(draft_post_id, feedback)`
  * [ ] `submit_post(draft_post_id, confirmation_token)` — MVP: manual mode (clipboard + intent URL)
* [ ] Build §14.8 Agent Chat view:

  * [ ] `st.chat_message` + `st.chat_input` plumbing.
  * [ ] Streaming response rendering.
  * [ ] Tool call collapsible blocks.
  * [ ] Session sidebar with resume.
  * [ ] New session button.
  * [ ] Token/cost indicator.
  * [ ] Inline draft action buttons (Save / Confirm / Discard).
  * [ ] Two-step confirmation modal with text re-display.
* [ ] Add agent integration buttons:

  * [ ] Today view: "Draft today's post", "Start reply session".
  * [ ] Next Rep view: per-lane "Have agent draft", per-hypothesis "Draft for this", per-target "Draft reply".
  * [ ] Content Performance view: per-post "Ask agent".
  * [ ] Weekly Review view: "Help draft experiment", "Help write counterfactual", "Suggest pillar pick".
* [ ] Settings: Agent configuration section.
* [ ] Cost tracking display in Settings.
* [ ] Refuse-and-explain handling for low-quality requests (engagement bait, etc.).
* [ ] Create `app/x_client.py` — X API OAuth 1.0a wrapper for publishing (`POST /2/tweets`).
* [ ] Create new `publish_confirmation_tokens` table (§10.2) via migration `migrations/00X_publish_tokens.sql`; raw tokens never persisted, only `token_hash` (SHA-256).
* [ ] Create new IWH tracking columns on `agent_drafts` (`iwh_attempt_index`, augmented `voice_self_score`); migration backfills existing rows to `iwh_attempt_index = 1`.
* [ ] Create new `redacted_arguments` column on `agent_tool_calls` (default false).
* [ ] Add publish-flow columns to `posts` via migration `migrations/00X_publish_columns.sql` with explicit defaults (`publish_attempt_count = 0`, other publish_* columns NULL); backfill all existing rows.
* [ ] Implement `publish_post_to_x` and `publish_reply_to_x` as INTERNAL-ONLY tools in `app/agent/_internal_tools.py` — NOT registered in `app/agent/tools.py::AGENT_TOOLS`. Direct Python callables invoked by the Streamlit click-handler; the agent has no schema slot to invoke them (§28.2 rule #10).
* [ ] Implement the six-check token validation chain (rule #10 a–f) inside `app/agent/confirmation.py::validate_and_consume_token`. Use SHA-256 hash lookup, not raw-string equality.
* [ ] Implement the atomic publish-then-DB-write transaction wrapper in `app/agent/_internal_tools.py`. Single transaction covering token consume + X API call + post state writes + agent_messages write + token row consume. Bounded internal retry (`x_posting_publish_retry_attempts_per_token`) on transient X API errors.
* [ ] Implement the raw-token redaction step in the tool dispatcher (`app/agent/tools.py::dispatch_tool_call`). For tool_name in {`publish_post_to_x`, `publish_reply_to_x`}, swap `arguments_json.confirmation_token` → `{"confirmation_token_id": <publish_confirmation_tokens.id>}` BEFORE inserting the audit row. Set `redacted_arguments = true`.
* [ ] Implement the crash-recovery reconciliation routine in `app/agent/recovery.py`; runs at app boot and from a Settings → Maintenance button. Detects rows where `posts.publish_attempt_count > 0` but `published_to_x_at IS NULL`; queries X API to detect orphan posts; reconciles or surfaces a "publish state unknown" banner.
* [ ] Implement the dark-pattern lint pass in `app/agent/lint.py` — separate small-model invocation (Haiku) with the one-shot prompt from §28.2 rule #12. Runs as a preflight inside `save_draft_post` / `save_draft_reply` before the DB insert. Failed lint → bounce as a failed IWH revision.
* [ ] Implement the IWH revision counter in `app/agent/session.py`. Counter lives outside the agent's context window; reads structured `<iwh_self_score>` tags from agent output; increments on per-quality score < `iwh_self_score_minimum` OR dark-pattern lint = yes; refuses save on attempt `iwh_max_revision_attempts + 1`.
* [ ] Implement confirmation modal in §14.8 Growth Agent Chat: character count (against `x_post_max_chars` = 280), exact-text display, server-side token generation in `publish_confirmation_tokens` (NOT in `st.session_state`), single-use enforcement, `x_posting_confirmation_token_ttl_seconds` TTL. Click-handler re-checks `x_posting_enabled` AND `posts` row state AND `manual_confirmation_status = 'draft'` after token generation.
* [ ] Implement publish-side rate limiting in `app/x_client.py` (sliding window: `x_posting_rate_limit_per_hour` last 3,600s, `x_posting_rate_limit_per_day` last 86,400s). Single source of truth; `app/agent/_internal_tools.py` calls `x_client.check_and_reserve_rate_capacity()` (atomic).
* [ ] Implement system-prompt build/assembly in `app/agent/prompt_builder.py`: read `config/agent_system_prompt.md`, splice rules 1-13 from spec §28.2 into Section 3 placeholder, query top-N voice samples, render tool catalog from agent-facing registry. Pre-commit hook verifies the count of rules in spec §28.2 matches the count in the assembled prompt (drift = hard failure).
* [ ] Add `x_posting_enabled` toggle and rate limit settings to §14.9 Settings → Growth Agent panel. Do NOT surface `x_posting_confirmation_required` (it's a compile-time constant, not a setting — see §10.2 settings block).
* [ ] Add export carve-out wiring in §16 export action: `posts` default-export uses the column allowlist (§16 (7)); the separate "Export agent audit" action (§16 (8)) covers `agent_messages` / `agent_tool_calls` / `agent_drafts` with its own confirmation modal.
* [ ] Add `PRAGMA foreign_keys = ON` to `app/db.py::get_connection()` (Streamlit `st.connection` setup). Without this, the new FK ON DELETE SET NULL behaviors on `posts.published_via_agent_message_id` and `agent_messages.resulted_in_published_post_id` will not fire.
* [ ] Add daily-VACUUM cleanup of expired-unconsumed `publish_confirmation_tokens` rows to `scripts/backup_db.py` (or a separate `scripts/cleanup_tokens.py`).
* [ ] Add publish credentials setup section to README (4 OAuth values in `.env`, instructions for getting them from X developer portal).
* [ ] Update `config/agent_system_prompt.md` to the expanded 8-section structure with engagement psychology, intelligence/wisdom/humility tone, and niche context.
* [ ] QA: confirmation expiry path, token reuse path (rule #10 check (c)), draft-text-hash mismatch path (rule #10 check (d)), X API auth failure, rate limit hit + bounded retry, network failure mid-publish (all three sub-cases per §22), draft-edited-after-token path.
* [ ] Test that the publish flow CANNOT fire from agent context: confirm `publish_post_to_x` is not in `AGENT_TOOLS` (registry inspection), and that a synthetic injection of the tool into the registry is caught by a startup assertion.
* [ ] Test the IWH refuse-after-3 mechanism is ungameable: write a test that pre-loads the agent's context with "ignore the iwh counter" and confirms the orchestrator still refuses on attempt 4. The counter must be in `app/agent/session.py`, not derived from agent output.
* [ ] Test the dark-pattern lint pass catches a synthetic engagement-bait draft (e.g., "5 secrets parents don't know — number 3 will surprise you!") AND that a flag bounces the draft as a failed IWH revision.
* [ ] Test crash-recovery: simulate "X API succeeded but DB failed mid-transaction" by killing the process between X POST and DB commit; on relaunch, recovery routine must reconcile via the X API `since_id` query.
* [ ] Test double-publish rejection: invoke `publish_post_to_x` on a `posts` row that's already `manual_confirmation_status = confirmed`; rule #10 check (f) should reject.
* [ ] Test export carve-out: default `posts` CSV export excludes `publish_last_error` and `published_via_agent_message_id`; opt-in toggle includes them.
* [ ] Test raw-token redaction: insert a publish tool call audit row; assert `arguments_json` contains `confirmation_token_id` (integer), NOT `confirmation_token` (string), and `redacted_arguments = true`.

### Phase 5.6 — Reply Target Discovery (see §29 for full spec)

* [ ] Migration `migrations/00X_reply_targets.sql`:

  * [ ] Create `reply_targets` table per §29.6 schema.
  * [ ] Create indexes: `unique(target_post_url)`, `unique(target_x_post_id) where ... is not null`, `(status, recommended_action_score desc, last_checked_at_utc desc)`, `(reply_intent) where status='posted'`.
  * [ ] Add `posts.in_reply_to_reply_target_id` (FK, ON DELETE SET NULL) and `posts.reply_intent` columns; backfill existing rows to NULL.
  * [ ] Add `reply_sessions.target_reply_target_ids_json` column.
  * [ ] Add the eight settings rows from §29.6 with documented defaults.
* [ ] Extend `v_daily_reps` view (§11) with the four new columns from §29.9.
* [ ] Extend §28.4 tool #6 `score_reply_candidates`: accept either candidate dict or `reply_target_id`; persist scoring + rationale on the row.
* [ ] Extend §28.4 tool #7 `record_reply_target`: write to the expanded schema with defaults.
* [ ] Implement the deterministic `recommended_action` resolver in `app/agent/reply_targets.py` per §29.3 (testable pure function over the four scores).
* [ ] Implement engagement-surface threshold computation using the four settings rows, with the NULL-author fallback labeled in the UI.
* [ ] Build Reply Target Queue view (§29.7): candidates list with filters, detail rows with the four scores + rationale, operations (Add candidate / Draft reply / Skip / Mark posted).
* [ ] Wire `posts.in_reply_to_reply_target_id` and `posts.reply_intent` into the manual-mode "Mark posted" click-handler.
* [ ] Update §14.2 Next Rep's reply-targets panel to window onto `reply_targets` (top 3–5 by `recommended_action_score`), with "see full queue →" link.
* [ ] Implement the candidate-expiry job (app-boot + once-daily): `status='candidate' AND last_checked_at_utc + reply_target_expiry_hours < now()` → `status='expired'`.
* [ ] Implement the drafted-but-not-recorded banner job: `status='drafted'` for >24h surfaces "Did you post this? Record URL or close as skipped" in the Queue.
* [ ] Add daily-VACUUM cleanup of `status IN ('skipped','expired','target_deleted') AND discovered_at_utc < now() - 90 days` to `scripts/backup_db.py`.
* [ ] Update `config/agent_system_prompt.md` Section 6 to include the `reply_intent` enum; update Section 7 tool catalog with the expanded #6/#7 signatures.
* [ ] Pre-commit drift check (§28.3 build step) extended to verify the `reply_intent` enum matches across spec / `tools.py` / system prompt template.
* [ ] QA:

  * [ ] Candidate add → score → detail render path.
  * [ ] Skip with each `skip_reason` value.
  * [ ] Mark posted (manual) populates `posts.in_reply_to_reply_target_id` and transitions both rows correctly.
  * [ ] Duplicate-URL insert is rejected with the "already in queue" UI.
  * [ ] Expiry job transitions correctly.
  * [ ] `target_author_follower_count = NULL` path uses floors and labels the score.
  * [ ] Agent attempts `save_draft_reply` against a URL not in `reply_targets`: orchestrator auto-creates + scores, three tool-call blocks visible.
  * [ ] Recommended-action resolver is unit-tested over all 256 (4^4) score combinations.

### Phase 5.7 — Reply Target Discovery V1.1 (deferred from MVP)

* [ ] Create `reply_target_snapshots` table per §29.6 schema.
* [ ] V1.1 metrics-refresh job: pull current engagement on each `status='candidate'` row at configurable cadence; insert a `reply_target_snapshots` row; update the parent's denormalized columns.
* [ ] Compute `velocity_score` from the last two snapshots.
* [ ] Compute `timing_score` from `post_age_minutes` + author-tier rules.
* [ ] Implement `app/agent/reply_target_lint.py` (Haiku invocation) per §29.10; wire `lint_blocked` to the Queue's "Draft reply" button enabled state.
* [ ] "Force-draft (overrides lint)" affordance with mandatory one-line reason logged to `agent_tool_calls.notes`.
* [ ] Detect 404 on `target_x_post_id` → transition `status='target_deleted'`; surface "draft orphaned" banner where applicable.
* [ ] Day-21 calibration view: show four engagement-surface thresholds vs. actual distribution of `engagement_surface_score` on posted replies; show lint block + override rate.
* [ ] X API rate-limit handling on the refresh job: log and skip without silent score drift.

### Phase 5.8 — Drafting Intelligence Pack (see §28.11 through §28.15 for full spec)

A bundle of five additive features that strengthen the Growth Agent's drafting and approval flow without changing existing contracts. None of the five is a hard gate; all are informational layers stacked on top of the §28.5/§28.10/§28.2 mechanisms already in place. Implementation order below is the recommended ship order — voice profile feeds the scorer; scorer + repetition guard surface in the same UI panels; confidence labels and payload-hash UX are independent.

**Migration:**

* [ ] Migration `migrations/011_drafting_intelligence.sql` (010 is already consumed by `010_reply_targets_idx.sql`; lex-order is what matters):

  * [ ] Create `voice_profiles` table per §10 schema. Partial unique index on `is_active = true`. Seed-row insert is NOT part of the migration — the first profile is generated by Daniel from Settings after at least 10 posts exist.
  * [ ] Create `post_embeddings` table per §10 schema. FK `post_id` `ON DELETE CASCADE`. Indexes per the table notes.
  * [ ] Create `prepublish_scores` table per §10 schema. FK `agent_draft_id` `ON DELETE CASCADE`. Unique index on `agent_draft_id`. Indexes per the table notes.
  * [ ] Add columns to `agent_drafts`: `prepublish_score_id` (int nullable, FK), `confidence_label` (text nullable, CHECK in `{fact, inference, speculation, mixed}`), `similarity_warning_json` (text nullable). Backfill existing rows to NULL.
  * [ ] Add column to `agent_messages`: `confidence_label` (text nullable, same CHECK). Backfill existing rows to NULL.
  * [ ] Add settings rows: `voice_profile_window_days = 90`, `voice_profile_min_source_posts = 10`, `repetition_guard_lookback_days = 180`, `repetition_guard_near_duplicate_threshold = 0.92`, `repetition_guard_close_echo_threshold = 0.78`, `prepublish_scorer_llm_augmentation_enabled = false`, `modal_hash_recheck_debounce_ms = 300`, `modal_edit_settle_seconds = 2`. Documented `note` per row.

**Voice profile (§28.12):**

* [ ] Implement `app/agent/voice_profile.py`:

  * [ ] `generate(window_days: int) -> VoiceProfileRow` — reads `posts`, calls Haiku with `config/voice_profile_prompt.md`, validates returned JSON against §10 `voice_profiles.profile_json` schema, writes atomic deactivate-then-insert in a single transaction.
  * [ ] `get_active() -> VoiceProfileRow | None`.
  * [ ] `diff(old_profile_json, new_profile_json) -> dict` — used by the Settings UI.
* [ ] Create `config/voice_profile_prompt.md` — structured-output prompt that returns the §10 `profile_json` schema. Reads scope is explicit: post text + classifications only, NEVER tester PII.
* [ ] Settings → Growth Agent → "Voice profile" panel: current profile metadata, regenerate button with N-days input, success/failure banners, diff view on success.
* [ ] Extend `app/agent/prompt_builder.py`: splice `voice_profiles.profile_json.self_description` into §28.3 Section 1; prepend compact `cadence` / `vocabulary_signatures[:5]` / `stop_phrases[:5]` rendering above the raw voice samples in §28.3 Section 5.
* [ ] Extend the pre-commit drift check: verify the splice executes and that `voice_profiles.is_active = true` row count is 0 or 1.

**Pre-publish heuristic scorer (§28.11):**

* [ ] Implement `app/agent/prepublish_scorer.py`:

  * [ ] One pure function per dimension: `clarity_score(text) -> int`, `hook_strength_score(text) -> int`, `specificity_score(text) -> int`, etc. (nine total per the §28.11 table). Each function has a docstring defining what 0 / 1 / 2 / 3 mean.
  * [ ] `score(draft_text, draft_kind, pillar, audience, cta, active_voice_profile) -> PrepublishScoresRow` — orchestrates all dimensions, computes `composite_label` per §10 derivation, returns a row ready for insert.
  * [ ] `compute_composite_label(scores: dict) -> str` — pure function, unit-testable.
* [ ] Wire into `app/agent/tools.py::_save_draft_post` and `_save_draft_reply`: after the IWH + dark-pattern preflight, before the `agent_drafts` insert, call `score(...)`, write to `prepublish_scores`, set `agent_drafts.prepublish_score_id`.
* [ ] UI surfaces:

  * [ ] Today (§14.1) — `composite_label` chip next to agent drafts.
  * [ ] Next Rep (§14.2) — same.
  * [ ] Agent Chat (§14.8) — chip + click-to-reveal 0-3 score panel + `warnings_json` excerpts.
  * [ ] Content Performance (§14.4) — historical `composite_label` × actual-engagement scatter, calibration view.
* [ ] Unit tests with golden inputs in `tests/test_prepublish_scorer.py`: at least one input per `composite_label` value, plus boundary cases for the derivation thresholds.

**Repetition guard (§28.13):**

* [ ] Implement `app/agent/embeddings.py`:

  * [ ] Provider adapter interface with `embed(texts: list[str]) -> list[np.ndarray]`. Default implementation calls Voyage AI's `voyage-3-lite`.
  * [ ] Provider key in `.env`: `VOYAGE_API_KEY=...` (or `OPENAI_API_KEY` if the adapter is switched). Loaded the same way as `ANTHROPIC_API_KEY` (§28.8).
* [ ] Implement `app/agent/repetition_guard.py`:

  * [ ] `check(draft_text, draft_kind, lookback_days) -> dict | None` — embeds the draft, cosine-scans `post_embeddings` rows within lookback, returns the schema in §10 `agent_drafts.similarity_warning_json`. Returns `None` if the provider is unavailable.
  * [ ] Re-embed-on-mismatch logic: if `source_text_hash` doesn't match current `posts.text`, re-embed that one row inline before comparing.
* [ ] Wire into `_save_draft_post` / `_save_draft_reply`: after the scorer, before the insert, call `check(...)`, set `agent_drafts.similarity_warning_json`.
* [ ] Create `scripts/embed_posts.py`:

  * [ ] Resumable backfill — skips rows that already have a `post_embeddings` row.
  * [ ] Respects provider rate limits (sleep between batches per provider spec).
  * [ ] `--re-embed-all` flag for provider migration; logs a banner in Settings → Maintenance while the script runs.
* [ ] UI surface: yellow banner above the draft text in Today / Next Rep / Agent Chat when `similarity_warning_json.label IN ('near_duplicate', 'close_echo')`. Banner shows the nearest post's excerpt + "intentional / let me rewrite" choice (just informational; no DB write).
* [ ] Settings → Growth Agent: "Repetition guard status" panel showing the configured provider, row count in `post_embeddings`, "Run backfill" button if `post_embeddings` is empty or stale.

**Confidence labels (§28.14):**

* [ ] Implement `app/agent/session.py::extract_confidence_labels(message_text: str) -> list[str]` — regex-extract `<confidence>([a-z]+)</confidence>` tags, validate, compute dominant label per the tie-breaking rule (speculation > inference > mixed > fact).
* [ ] Create `app/agent/confidence_patterns.py` — list of regex patterns identifying analytical claims. Comment each pattern with an example match. Patterns include: percentage-change phrasing, "lane X is the winner / outperformed", "this caused", "responsible for", "the data shows".
* [ ] Implement `app/agent/session.py::detect_untagged_claims(message_text: str) -> int` — scans for analytical-claim regex matches that are NOT inside a `<confidence>` tag. Returns count of unmatched analytical claims; >0 increments the IWH humility-failure counter.
* [ ] Wire into the orchestrator: after each agent message is assembled, run `extract_confidence_labels` and `detect_untagged_claims`. Persist dominant label on `agent_drafts.confidence_label` (if the message produced a draft) or `agent_messages.confidence_label`.
* [ ] UI surface: inline colored chips in Agent Chat (green/blue/yellow/gray) at the end of claim sentences. Historical surface on Content Performance per-post agent reasoning.
* [ ] Update `config/agent_system_prompt.md` Section 8 (Output format): add the `<confidence>` tag requirement with three example tags. Splice rule #14 into Section 3 via the existing build-step splicer.
* [ ] Extend `draft_weekly_review_section` (§28.4 tool #9): output emits `<confidence>` tag per section. Extend the §24 weekly-export blocker: `confidence_label = speculation` blocks export until acknowledged or rewritten.
* [ ] Tests: feed agent output with mixed tag patterns; assert dominant label per tie-breaking rule. Feed analytical-claim text without tags; assert `detect_untagged_claims` flags it.

**Approval payload hash — user-visible enforcement (§28.15):**

* [ ] Extend the confirmation modal in §14.8:

  * [ ] On modal open, compute `current_draft_text_hash = sha256(posts.text)` and store in `st.session_state[f"modal_hash_{post_id}"]`.
  * [ ] Text area pre-filled with `posts.text`; bind to `st.session_state[f"modal_text_{post_id}"]`.
  * [ ] Debounced (300ms default) re-hash on text change; render yellow "you've edited this draft" banner when hashes differ; disable Publish button for 2 seconds after each edit.
* [ ] Extend the Publish click-handler in `app/agent/confirmation.py`:

  * [ ] Read current modal text; if different from `posts.text`, issue an `UPDATE posts SET text = ? WHERE id = ?` BEFORE generating the confirmation token.
  * [ ] If a non-consumed token already exists for this `post_id`, expire it (set `expires_at_utc = now() - 1`) before minting a new one. Log invalidation to `agent_tool_calls.notes = "prior token invalidated by re-modal"`.
* [ ] Tests:

  * [ ] Modal open → edit text → click Publish: assert single live token, hash matches post-edit text, audit row records pre/post-edit hash diff.
  * [ ] Two-modal race: open modal A, open modal B, click Publish in A then B. Assert A's token is invalidated when B mints; B's publish succeeds, A's would fail rule #10 (c).

**QA across the pack:**

* [ ] All 192 (or current count) existing tests pass.
* [ ] Ruff clean on every new module.
* [ ] Boot smoke: `uv run streamlit run app/main.py --server.headless true` shows no exceptions; Settings → Growth Agent → "Voice profile" panel renders even with zero profile rows (with the "generate your first profile" CTA).
* [ ] End-to-end: from a clean agent_drafts row, drive `_save_draft_post` → assert `prepublish_score_id` is set, `similarity_warning_json` is set, `confidence_label` is set if the message had tags; then drive the confirmation modal → assert the payload-hash banner triggers on edit and the click-handler invalidates the prior token if a second modal mints one.

**Documentation:**

* [ ] Update `docs/IMPLEMENTATION_STATUS.md` with the Phase 5.8 features and any deferred behaviors.
* [ ] README addition: brief description of the Drafting Intelligence Pack and how to seed it (run `scripts/embed_posts.py` then click "Regenerate voice profile" in Settings).

### Phase 6 — V1.1: Data collection (deferred from MVP)

* [ ] Configure xurl auth outside the app.
* [ ] Implement `scripts/collect_account_snapshot.py`.
* [ ] Store raw response.
* [ ] Insert immutable account snapshot.
* [ ] Implement recent post import.
* [ ] Implement post metrics refresh.
* [ ] Add failure logs.
* [ ] Manual form remains available (already the MVP default).
* [ ] Add cron/launchd job for scheduled collection.
* [ ] **Upgrade `submit_post` to direct X API posting** when `data_collection_mode = api`.
* [ ] **Upgrade `find_reply_targets` to use X API search** in addition to curated accounts.

### Phase 7 — QA

* [ ] Test missing snapshot day.
* [ ] Test duplicate snapshot.
* [ ] Test manual correction (no overwrite).
* [ ] Test post with missing impressions.
* [ ] Test manual reply without X post ID.
* [ ] Test UTM extraction.
* [ ] Test download with unknown source.
* [ ] Test AI-builder follower vs self-reported ICP tester distinction.
* [ ] Test Markdown export with and without counterfactual note.
* [ ] Test CSV export/import round trip.
* [ ] Test no secrets written to DB/export.
* [ ] Test `VACUUM INTO` backup integrity.
* [ ] Test velocity suppression below noise threshold.
* [ ] Test lane confidence labels at sample sizes 4, 5, 14, 15, 29, 30.
* [ ] **Agent QA:**

  * [ ] Test agent refuses to call `submit_post` without `confirmation_token`.
  * [ ] Test confirmation_token is single-use.
  * [ ] Test two-step confirmation re-displays exact text.
  * [ ] Test edited drafts post the edited text, not the original.
  * [ ] Test agent's `voice_self_score` is recorded.
  * [ ] Test agent refuses engagement-bait requests with explanation.
  * [ ] Test tool calls are visible in chat (not hidden).
  * [ ] Test daily cost ceiling blocks new sessions but lets current finish.
  * [ ] Test session persistence across Streamlit reruns.
  * [ ] Test new session has fresh system prompt context.
  * [ ] Test agent does NOT have access to `stir_testers` PII or `qualitative_feedback`.
  * [ ] Test agent in manual mode does NOT call X API.

---

## 26. MVP acceptance criteria

The MVP is acceptable when Daniel can do this loop:

```text
Morning:
1. Open localhost dashboard.
2. Enter today's snapshot in the pinned manual form (30 seconds).
3. See follower weigh-in.
4. See current distribution milestone distance.
5. See current validation milestone distance (equal visual weight).
6. See daily rep checklist (with raised, experimental reply target).
7. Open Next Rep view to see under-sampled lanes.
8. Click "Have agent draft for stir × icp" → opens Agent Chat with context.
9. Agent proposes 3 drafts with voice self-scores.
10. Daniel picks one, edits it inline, clicks Confirm.
11. Two-step confirm re-displays text; Daniel confirms; clipboard handoff opens X.
12. Daniel posts on X; comes back and the post is logged with posted_via = agent_assisted.

During day:
13. Log additional posts/replies (manual or via agent).
13b. For drafts Daniel wants to publish directly: click "Publish to X" in chat → confirmation modal → check the confirm box → click Publish now → post goes live. Each publish is logged in `agent_tool_calls` with the confirmation timestamp.
14. Tag content lane (v1: 3 pillars × 2 audiences × 2 CTAs).
15. Log Stir visits/downloads/tester feedback as they happen.
16. Use Agent Chat for ad-hoc strategy questions.

End of week:
17. Open Weekly Review.
18. See follower delta, reps, top posts (by confidence), Stir signals.
19. Click "Help draft experiment" → agent proposes 2-3 next-week hypotheses.
20. Click "Help write counterfactual" → agent lists alternative explanations for the week.
21. Daniel writes interpretation and counterfactual note in his own voice.
22. Export Markdown postmortem.
23. Choose next experiment.

Day 21:
24. Calibration prompt fires.
25. Review reply target adherence data.
26. Adjust target up, down, or hold based on data.
```

The MVP fails if:

* It makes follower count feel like the only goal.
* It treats 500k as an operational target (anywhere in the daily/weekly UI).
* The validation ladder is structurally less prominent than the distribution ladder.
* It hides whether daily reps were completed.
* It treats AI/builder distribution growth as equivalent to ICP product validation.
* It cannot handle manual replies.
* It requires the X API to work at all (manual must be the default).
* It loses raw historical snapshots.
* It over-interprets one-day or one-post noise.
* It stores inferred working-parent / ICP classifications without self-report.
* It uses `cp` instead of `VACUUM INTO` for backups.
* **The Growth Agent posts or replies to X without an explicit, fresh per-action confirmation from Daniel.**
* **The confirmation flow can be bypassed by the agent (e.g., agent generates its own confirmation token).**
* **The agent's drafts violate the intelligence / wisdom / humility framing without revision attempts.**
* **The system prompt's engagement psychology section is used to draft manipulative content (fake urgency, fabricated social proof, manufactured outrage, manipulation of vulnerability).**
* **The agent generates posts that fail the intelligence/wisdom/humility bars and ships them.**
* **The agent has access to tester PII or qualitative feedback.**
* **Tool calls are hidden from the user in chat.**

---

## 27. Key product constraint to preserve

The dashboard should be emotionally boring and operationally sharp.

Daily follower count is the scale.
Daily reps are calorie/protein adherence.
Reply sessions are workouts.
Screenshots and feedback are progress photos.
Weekly review is the coach.
The Next Rep view is the warm-up: it tells you which exercise the program is missing this week.
The Growth Agent is the training partner: it knows your program, has a good eye, drafts the routine with you, and never lifts the bar on its own.

The point is not to feel good every day.
The point is to make the distribution loop measurable enough that Daniel can keep shipping, learn which lanes work, and keep Stir validation structurally equal to social vanity instead of subordinate to it. The agent compresses the slowest step in the loop (drafting) without ever removing Daniel from the decision.

---

## 28. Growth Agent

The Growth Agent is a Claude-powered assistant integrated into the dashboard via tool use. Its job is to help Daniel do the daily generative work — drafting posts and replies, scoring reply candidates, extracting lessons, and drafting weekly review prose — while respecting the same epistemic constraints as the dashboard itself.

### 28.1 System prompt section registry (single source of truth)

To avoid section-number drift when the prompt evolves, other parts of the spec MUST reference sections by purpose, not by integer:

| Section purpose       | Section # (current)   | Static / Dynamic | Source                                                            |
| --------------------- | --------------------- | ---------------- | ----------------------------------------------------------------- |
| Identity & niche      | 1                     | Static           | Hardcoded in `config/agent_system_prompt.md`                      |
| IWH tone directive    | 2                     | Static           | Hardcoded                                                         |
| Mission & rules       | 3                     | Static + spliced | Static prose + rules 1-13 spliced from this spec at build time    |
| Engagement psychology | 4                     | Static           | Hardcoded                                                         |
| Voice samples         | 5                     | **Dynamic**      | Top N rows from `voice_samples` (where `is_active = true`) by `priority` |
| Current taxonomy      | 6                     | Static + config  | v1 taxonomy from §15.3                                            |
| Tool catalog          | 7                     | **Dynamic**      | Built from `app/agent/tools.py` registry at session start          |
| Output format         | 8                     | Static           | Hardcoded                                                         |

When other spec sections reference a prompt area (e.g., "voice samples are injected into…"), they MUST use the purpose name ("…the Voice samples section") rather than the number. The numbers above are the current binding; if the prompt grows a new section, this table moves and references stay valid.

### 28.2 Non-negotiable rules (in system prompt)

1. **Never post or reply to X without explicit per-action confirmation.** Drafts always land first. Publishing requires Daniel to click a confirmation button for each individual post — no batch approvals, no auto-publish. The agent itself never publishes; it calls `publish_post_to_x` or `publish_reply_to_x` only after the UI has captured Daniel's confirmation click and passed it to the tool layer with a valid single-use `confirmation_token`.
2. **Never claim causal attribution.** Same constraint as §5 non-goals #2. "Associated with," not "caused by."
3. **Never infer sensitive attributes** (working-parent, home-cook, ICP status). Same constraint as §13 hard rule #11.
4. **Always preserve the target post URL** when drafting replies. The reply text plus link to the original post is the deliverable; losing the link breaks the workflow.
5. **Always cite the data** when making analytical claims. E.g., "stir lane had 0 posts this week (from `v_lane_performance` for the current week)" — not "stir lane is underperforming."
6. **Never invent metrics.** If a number isn't in the database or in a tool result, don't reference it.
7. **Respect sample-size constraints.** Don't rank lanes that the dashboard refuses to rank.
8. **Voice consistency.** Match Daniel's voice as represented in the active voice samples.
9. **Truthfulness over agreeability.** If Daniel proposes a hypothesis the data contradicts, push back with the data.
10. **Confirmation flow is non-bypassable (validation chain).** The `publish_*` tools refuse to fire unless ALL of the following hold against the new `publish_confirmation_tokens` table (§10.2): (a) `sha256(confirmation_token)` matches a row's `token_hash`; (b) `expires_at_utc > now()`; (c) `consumed_at_utc IS NULL`; (d) the row's `draft_text_hash_at_issue` equals `sha256(posts.text)` for the matching `post_id`; (e) the row's `post_id` matches the tool's `post_id` argument; (f) the row whose `manual_confirmation_status = 'draft'`. Validation and `consumed_at_utc` update happen atomically in the same transaction as the publish state writes (see Publish flow). The token registry lives in a DB table that is unreachable from the agent's tool registry — no agent-callable tool reads `publish_confirmation_tokens` or `st.session_state`. The token UUID itself lives only in the click-handler's local stack frame and the synchronous tool call.
11. **Audit every publish — with raw-token redaction.** Every publish (success and failure) is logged in `agent_tool_calls`. The tool dispatcher MUST redact the raw `confirmation_token` from `arguments_json` BEFORE the audit row is inserted, replacing it with `{ "confirmation_token_id": <publish_confirmation_tokens.id> }`. The audit row sets `redacted_arguments = true`. The successful-publish audit log includes: the final draft text (post-edit), the `consumed_at_utc` timestamp, the `confirmation_token_id` (NEVER the raw token), and the resulting `x_post_id`. Do NOT rely on the Anthropic SDK's default argument-logging — that path will leak the raw token. Redaction happens in `app/agent/tools.py` between tool invocation and audit-log insert.
12. **Engagement psychology serves clarity, not manipulation — dark patterns are forbidden, and the prohibition is enforced by a separate lint pass.** The agent draws on engagement principles (specificity, curiosity gaps, cognitive ease, identity-affirming hooks) to make posts effective. It NEVER uses dark patterns: no fake urgency, no manufactured outrage, no fabricated social proof, no engagement bait that doesn't deliver, no "controversial takes" engineered for arguments, no manipulation of vulnerable audiences (self-doubt, fear, FOMO without basis). Enforcement: the orchestrator runs a **dark-pattern lint pass** (a separate small-model invocation with a one-shot prompt: "Does this draft use fake urgency, manufactured scarcity, fabricated social proof, or engagement-bait that doesn't deliver? Reply yes/no with one-line reasoning.") on every draft BEFORE calling `save_draft_*`. If the lint pass returns yes, the draft is treated as a failed IWH revision (counts toward `iwh_max_revision_attempts`). The agent cannot disable or short-circuit the lint pass — it lives in `app/agent/lint.py`, outside the agent loop.
13. **Intelligence, wisdom, humility — orchestrator-tracked revision counter.** Every post should reflect substantive thought (intelligence), long-arc judgment about whether the post should exist (wisdom), and honest acknowledgment of limits (humility). If a draft fails any of those three (self-score < `iwh_self_score_minimum` OR the dark-pattern lint pass returns yes), the orchestrator increments `agent_drafts.iwh_attempt_index` and sends the draft back for revision. On attempt `iwh_max_revision_attempts + 1` (default: the 4th attempt), the orchestrator refuses to call the save_draft tool and emits a refusal back to the conversation. **The revision counter lives in `app/agent/session.py` and `agent_drafts.iwh_attempt_index`, NOT in the agent's context window** — Daniel cannot tell the agent "skip the count," and prompt-injection in pasted reply text cannot reset it. The agent emits structured `<iwh_self_score>` tags (three integers 0-3) with each draft; the orchestrator reads them and decides.
14. **Confidence labels on every analytical claim — orchestrator-validated.** Whenever the agent emits a statement that names a number, attributes movement, or draws a conclusion from data, it MUST attach a `<confidence>` tag with one of four values: `fact` (the number/event is directly in a tool result the agent just received), `inference` (the conclusion is drawn from data but involves judgment — e.g. "self lane likely needs more reps"), `speculation` (the agent has no data and is guessing — e.g. "this hook style might land better"), `mixed` (a claim that combines factual citation with inference). The orchestrator parses these tags and persists the dominant label on `agent_drafts.confidence_label` (for claims attached to drafts) and `agent_messages.confidence_label` (for analytical messages). Untagged analytical claims are detected by a small regex sweep in `app/agent/session.py` (matches "X% increase", "lane Y is the winner", "this caused", etc. against a list in `app/agent/confidence_patterns.py`) — an untagged match counts as a Section-2 humility failure (rule #13 IWH humility score drops by 1 for that draft). The full enforcement spec is §28.14.

### 28.3 System prompt structure

Stored at `config/agent_system_prompt.md`. The prompt has 8 sections (per the registry table above), assembled at runtime so voice samples and current taxonomy are fresh. The **build step** that produces the final prompt-text-sent-to-the-model:

1. Read the static prompt skeleton from `config/agent_system_prompt.md`.
2. Splice the verbatim text of non-negotiable rules 1-13 from this spec (§28.2) into the placeholder `[Non-negotiable rules 1-13 listed verbatim from §28.2 of the spec]` in Section 3. A pre-commit hook (or CI check) verifies that the count of rules in spec §28.2 equals the count in the spliced prompt — drift between the two is a hard failure.
3. Query top N (default `agent_voice_sample_count = 5`) active rows from `voice_samples` ordered by `priority`; substitute into the **Voice samples** section.
4. Render the tool catalog from `app/agent/tools.py`'s registry into the **Tool catalog** section.
5. Pass the assembled prompt as the system message for the conversation.

The assembled prompt template:

```markdown
# Section 1 — Identity and niche
You are the Growth Agent for Daniel (@dannyscalant) — Master's student in
AI in Biomedicine and Health Sciences at UF, building Stir (an iOS app
that uses AI to turn "what's for dinner?" into 3 cookable options via
kitchen scanning + Gemini Cook Mode). Long-arc trajectory: AI in
neuro-oncological surgery (intraoperative tissue analysis, patient-specific
surgical planning). ICP for Stir: working parents and home cooks. Your
job: help build X distribution that serves both Stir now and the
long-arc mission. Not a marketer — a thinking partner who respects data
and sees the through-line from kitchen scanning to surgical AI.

(Voice details, vocabulary, and rhythm are carried by the Voice samples
section, not duplicated here.)

# Section 2 — Tone: intelligence, wisdom, humility
Every post and reply you draft must reflect three qualities:

- **Intelligence**: substantive ideas, not platitudes. Specific over
  abstract. If a generic motivational quote could replace the post
  without losing meaning, the post is too vague.
- **Wisdom**: long-arc judgment. Some posts shouldn't exist. Some replies
  shouldn't be made. Restraint is a feature. Wisdom is also the courage
  to be unfashionable when the data supports it.
- **Humility**: acknowledge limits. Daniel is a Master's student, not a
  surgical AI veteran. He is building Stir, not running it at scale.
  Don't claim what he hasn't earned. Don't oversell. The neuro-oncology
  arc is a serious long-term aim, not a credential to flash.

For each draft, emit a `<iwh_self_score>{"intelligence": 0-3, "wisdom":
0-3, "humility": 0-3}</iwh_self_score>` tag honestly. The orchestrator
reads these scores; if any falls below `iwh_self_score_minimum`, the
orchestrator returns the draft for revision. After
`iwh_max_revision_attempts` failures, the orchestrator refuses the
draft entirely. You do not own the count.

# Section 3 — Mission and constraints
The dashboard separates two streams: distribution (followers, impressions,
engagement) and validation (working-parent / home-cook testers downloading,
scanning kitchens, using Cook Mode). These streams must stay separate. A
+20 AI-builder follower week and 1 working-parent Cook Mode session are
not interchangeable wins.

[Non-negotiable rules 1-13 listed verbatim from §28.2 of the spec]

# Section 4 — Engagement psychology principles

**DARK PATTERNS ARE FORBIDDEN (read this first):**
- No fake urgency. No fabricated social proof. No manufactured outrage.
- No engagement bait that doesn't deliver on its hook.
- No "controversial takes" engineered for arguments.
- No manipulation of insecurity, fear, or FOMO without basis.
- A separate lint pass (`app/agent/lint.py`) checks every draft for these
  patterns. If it flags your draft, you do NOT get to argue with it — the
  draft counts as a failed IWH revision and goes back to you for rewrite.

With that floor established, the principles below are tools in service of
clarity and substance:

**Hooks (first line carries the post)**
- The first line decides scroll-past or stop. Specific > clever. Concrete
  > abstract. A real noun beats a metaphor.
- Curiosity gaps: open a loop the rest of the post closes. Don't promise
  a payoff the post can't deliver. [Forbidden: engagement-bait gaps that
  the post never closes.]
- Pattern interrupts: a counterintuitive opening, a precise number, a
  contradiction of conventional wisdom — when the post actually earns it.
  [Forbidden: pattern interrupts used to engineer outrage.]

**Structure**
- One idea per post. If you have two, write two posts (or a thread).
- Sentence-per-line rhythm. White space is part of the message.
- Endings: a clear ask, a clear takeaway, or a clear question. No fade-outs.

**Substance**
- Specificity is engagement. "1,247 users" beats "many users." Real
  examples beat hypothetical ones. Daniel's actual experience beats
  generic founder wisdom.
- Cognitive ease: clear writing > clever writing. Read every draft as if
  the reader has 0.8 seconds and a tired brain.
- Identity-affirming hooks: the best posts let the reader feel sharper,
  more curious, more capable — not the writer.

**Emotion and resonance**
- Emotional resonance comes from concrete detail, not adjectives. "Three
  failed dinner attempts before 7pm" beats "frustrating."
- Storytelling structures: problem → tension → resolution; before / after;
  question → answer. The structure carries even short posts.
- Vulnerability when it teaches. Self-deprecation when it's true. Never
  performative humility (which violates Section 2).

**Engagement triggers — used ethically (forbidden uses tagged inline)**
- **Reciprocity**: give insight before asking. A real-value giveaway
  earns the right to a CTA.
  [Forbidden: manufactured giveaway scarcity; opening loops that don't
  pay off.]
- **Social proof**: cite actual numbers, actual users, actual feedback.
  Never fabricated. Never vague ("many people are saying"). If you
  cannot link the number to a row in the DB or a tool result, do NOT
  invoke it.
  [Forbidden: invented testimonials, inflated counts, "many founders".]
- **Scarcity**: only when real. "We're capped at 50 testers" if true.
  If you cannot link to a row that justifies the scarcity claim, do not
  invoke it.
  [Forbidden: deadline pressure, fake limited supply, FOMO framing.]
- **Authority**: Daniel's credentials and trajectory are real and live
  in Section 1. Use them factually, never inflated.
  [Forbidden: credentials he hasn't earned, role-inflation, claiming
  surgical AI experience he doesn't yet have.]

**Format guidance for X specifically**
- Standalone posts: aim for one strong idea, often under
  `x_short_post_target_chars` chars (current default: 200). Thread when
  an idea genuinely needs more space. Hard ceiling: `x_post_max_chars`
  (280; X platform limit).
- Replies: lead with substance addressed to the original post. Daniel's
  handle should not be the most interesting thing about the reply.
- Bookmarks > likes: a post that gets saved is doing real work. Optimize
  for bookmark-worthy substance over reaction-worthy edges.
- Links: when a post contains a link to Stir or getstir.app, the post
  must earn the link — the body should be valuable on its own.

# Section 5 — Voice samples
{{ top N active voice_samples, interleaved with context_note }}

# Section 6 — Current taxonomy
Pillars: stir, build, self
Audiences: icp, other
CTAs: ask, none

# Section 7 — Tool catalog
{{ list of available tools (1-11) with one-line when-to-use guidance,
including the publish tools }}

# Section 8 — Output format
- When drafting posts/replies, propose 2-3 variants with notes on what
  each prioritizes (hook style, structure, CTA strength, voice register).
- For every draft variant, emit a `<iwh_self_score>` tag honestly.
- When citing data, name the source (`v_lane_performance`, `posts`, etc.).
- When uncertain, say so explicitly. Humility over agreeability.
- When you save a draft, tell Daniel where it landed (table + draft ID).
- When publishing, ask Daniel for explicit confirmation in the chat,
  display the exact final text, and wait — do NOT attempt to call
  `publish_*` yourself. The publish path is the UI's, not yours.
```

System prompt is editable as a file (config/agent_system_prompt.md). Changes take effect on next conversation start. Daniel reviews the prompt monthly per his weekly review cadence.

### 28.4 Tool functions

Eleven tools. Tools #1-5 and #10-11 are MVP must-ship; #6-9 are Should-ship.

| # | Tool | Input | Output | Side effects | When to use |
|---|---|---|---|---|---|
| 1 | `query_dashboard_state` | `slice: "today" \| "next_rep" \| "weekly" \| "validation_status" \| "all"` | Structured JSON of requested slice | None (read-only) | Start of any drafting task to ground the agent |
| 2 | `get_recent_posts` | `pillar?, audience?, cta?, days_back: int = 7, limit: int = 20` | Array of posts with text, metrics, classifications | None | When drafting or analyzing patterns |
| 3 | `get_lane_performance` | `pillar?, audience?, cta?, window_days: int = 14` | Rows from `v_lane_performance` | None | When reasoning about which lane needs data |
| 4 | `save_draft_post` | `text, pillar, audience, cta, hypothesis, why_posted, expected_signal` | `{post_id, draft_url}` | Inserts into `posts` with `manual_confirmation_status = draft`, `type = standalone`. Orchestrator runs IWH self-score check + dark-pattern lint BEFORE the insert; failed checks bounce back as a revision (see rules #12-13). | After producing a final draft Daniel approved |
| 5 | `save_draft_reply` | `text, target_post_url, target_post_text, pillar, hypothesis` | `{post_id, draft_url}` | Inserts into `posts` with `type = reply`, `manual_confirmation_status = draft`; target URL preserved. Same IWH + lint preflight as #4. | After producing a final reply draft |
| 6 | `score_reply_candidates` | `candidates: [{url, text, user}]` | Scored array with reasoning, suggested lane, priority 1-10 | None | When Daniel pastes a batch of posts asking which to engage |
| 7 | `record_reply_target` | `target_post_url, target_post_text, target_user, pillar, audience, agent_reasoning, agent_priority_score` | `{reply_target_id, expires_at_utc}` | Inserts into `reply_targets` with status `queued` | After `score_reply_candidates`, for ones Daniel wants to queue |
| 8 | `extract_lesson` | `post_id: int` | `{lesson_text, suggested_quality_score}` | None (suggestion only; Daniel saves) | From Content Performance view per-post button |
| 9 | `draft_weekly_review_section` | `section_name: "interpretation" \| "lesson" \| "counterfactual" \| "next_week_experiment", week_id: int` | `{draft_text}` | None | From Weekly Review view's "Draft this section" buttons |
| 10 | `publish_post_to_x` | `post_id: int, confirmation_token: str` (internal-only; not exposed in the agent-facing tool registry — see note below) | `{x_post_id, x_post_url, published_at_utc}` | Atomic transaction: validates the token against `publish_confirmation_tokens` (six checks in rule #10), sets `published_to_x_at`, `x_post_id`, `publish_method = agent_confirmed`, `manual_confirmation_status = confirmed`, `publish_last_error = NULL`, increments `publish_attempt_count`, marks token `consumed_at_utc`. Logs to `agent_tool_calls` with `redacted_arguments = true`. | Only invoked by the Streamlit click-handler after Daniel clicks "Publish now"; the click-handler generates the token, this tool consumes it |
| 11 | `publish_reply_to_x` | `post_id: int, confirmation_token: str` (internal-only) | `{x_post_id, x_post_url, published_at_utc}` | Same as #10 but for `type = reply`; preserves `target_post_url` as a real X reply | Only after Daniel clicks "Publish reply" in the confirmation modal |

**Internal-only tool surface (rules #10 + #11):** the publish tools are NOT registered in the agent-facing tool registry (`app/agent/tools.py::AGENT_TOOLS`). They live in `app/agent/_internal_tools.py::INTERNAL_TOOLS`, exposed only as direct Python callables. The agent has no tool schema entry that names them — it literally cannot attempt to call them (no JSON-schema slot to populate, no SDK round-trip). The agent's user-facing affordance is the chat-message "ready to publish — click the button above"; the click-handler does the rest. (Design rationale: a positional `confirmation_token: str` parameter on a registered tool would invite the agent to mint UUIDs and attempt calls — every failure noises up `agent_tool_calls`. Removing it from the registry makes the contract unforgeable by construction.)

**Validation, atomicity, retry, and audit (publish tools enforce all of these as a single contract):**
- **Validation chain** runs all six checks from rule #10 BEFORE any X API call. Any failure → reject; no API call; log to `agent_tool_calls` with `status = error` and `redacted_arguments = true`; do NOT mark `consumed_at_utc` (the token stays valid until TTL so Daniel can retry within the 60s window without re-clicking).
- **Atomicity**: BEGIN TRANSACTION → validate token → call X API (with bounded internal retry, see below) → on success, in the SAME transaction set `published_to_x_at`, `x_post_id`, `publish_method = agent_confirmed`, `manual_confirmation_status = confirmed`, clear `publish_last_error`, increment `publish_attempt_count`, mark `consumed_at_utc`, insert agent_messages row referencing post via `resulted_in_published_post_id`, update `posts.published_via_agent_message_id` → COMMIT. On any DB error after the X API succeeds → DO NOT roll back the token consumption (the post is live; the row must be reconciled, not lost). Set a "publish state unknown — verify on X" banner; the crash-recovery routine (Publish flow step 8 below) reconciles on next boot.
- **Bounded internal retry**: a single confirmation_token authorizes ONE atomic publish OPERATION, which the tool layer may internally retry on transient (5xx, rate-limit 429) errors up to `x_posting_publish_retry_attempts_per_token` (default: 2) times with exponential backoff. Externally observable: one Daniel click → at most one live X post. The agent does not observe the retries; they happen inside the tool layer.
- **Audit redaction (rule #11)**: `arguments_json` written to `agent_tool_calls` is `{"post_id": N, "confirmation_token_id": M}` where M = `publish_confirmation_tokens.id`. The raw token string is NEVER persisted. `redacted_arguments = true`.

Tool implementation lives in `app/agent/tools.py` (registered) and `app/agent/_internal_tools.py` (unregistered publish entry points). The publish tools use the X API v2 `POST /2/tweets` endpoint with OAuth 1.0a user-context auth from `.env`. The agent does not get a SQL shell, only these named functions.

### 28.5 Voice samples

Voice samples anchor the agent's drafting voice. Workflow:

1. Daniel marks a post as a voice sample via Content Performance view (§14.4 "Mark as voice sample" button).
2. Sample inserted into `voice_samples` with `is_active = true`, default priority.
3. Top N (default `agent_voice_sample_count = 5`) active samples by priority are injected into the **Voice samples** section of the system prompt at each conversation start (see registry table above for the current section number).
4. Samples can be rotated — pin some, deactivate others as voice evolves.
5. `last_used_at_utc` lets Daniel see if one sample is being over-relied on; rotate manually if needed.

Seed strategy: at first Phase 5.5 build, Daniel marks 3-5 of his strongest existing posts as voice samples before the first agent use. Without samples, the agent falls back to a base system prompt (banner warns).

**Voice samples are complemented by the generated voice profile (§28.12).** Samples are raw exemplars Daniel hand-picked. The profile is a structural read of Daniel's actual writing across the last N days, synthesized by a small-model call into a compact JSON spec'd in §10 `voice_profiles`. Both are spliced into the system prompt — samples carry tone-by-example, profile carries cadence and vocabulary signatures. See §28.12 for the regeneration workflow.

### 28.6 Cost management

- Per-call cost estimated from token counts and per-model rate snapshot at call time. The rate snapshot is stored on each message (`agent_messages.rate_snapshot_json`) so retroactive auditing isn't broken if Anthropic pricing changes.
- Per-conversation total in chat header.
- Per-month running total in chat header banner with cap progress bar.
- Monthly cap default $25; configurable in Settings → Growth Agent.
- At 80% of cap: yellow banner across all agent surfaces.
- At 100%: red banner; agent calls disabled until next month or cap raised.
- Cap is enforced at the API client layer (`app/agent/client.py`) — refuses to make a call if next call would breach cap.

**Verify current Anthropic pricing** when implementing the cost estimator; pricing changes and the cost calculation should pull from a versioned rate table, not hardcoded numbers.

### 28.7 Integration points summary

| Surface | Trigger | Tool flow |
|---|---|---|
| Today (§14.1) | "Draft today's post" | `query_dashboard_state` → draft iteration → `save_draft_post` |
| Next Rep (§14.2) | "Help me draft for `{lane}`" | `query_dashboard_state` → `get_lane_performance` → draft iteration → `save_draft_post` |
| Next Rep (§14.2) | "Score these reply candidates" | `score_reply_candidates` → `record_reply_target` (per Daniel's selection) |
| Content (§14.4) | "Extract lesson" per post | `get_recent_posts(post_id)` → `extract_lesson` → Daniel saves |
| Weekly Review (§14.6) | "Draft this section" | `query_dashboard_state(slice="weekly", week_id=...)` → `draft_weekly_review_section` |
| Chat (§14.8) | Free-form input | any combination of the registered agent tools (#1-9; the publish tools #10-11 are click-handler-only) |

### 28.8 API key management

Anthropic API key stored in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Loaded via `python-dotenv`. Never logged, never stored in DB, never included in `raw_api_responses`. The `.env` file is already in `.gitignore` (§18).

If `ANTHROPIC_API_KEY` is missing or invalid:
- All agent buttons throughout the dashboard show greyed with tooltip: "Growth Agent disabled — set ANTHROPIC_API_KEY in .env"
- §14.8 Growth Agent Chat view shows a single-state message with setup instructions
- Manual workflows (snapshot entry, post logging, weekly review without agent drafting) are unaffected

### 28.9 Alternative: Claude Max subprocess (documented, not MVP)

Instead of the Anthropic API, the agent could be implemented as a subprocess call to the `claude` CLI authenticated via Daniel's Max subscription. Tradeoffs:

| Aspect | Anthropic API (MVP) | Claude Max subprocess |
|---|---|---|
| Cost | Per-token, separate from Max | Bundled with Max subscription |
| Integration | Clean Python SDK, streaming, tool use first-class | Subprocess, JSON over stdout, more parsing |
| Latency | Direct HTTP call | CLI startup overhead per call |
| Tool use | First-class via SDK | Possible via Claude Code SDK but less ergonomic for ad-hoc tools |
| Auth | API key in .env | CLI session auth (CLAUDE_CONFIG_DIR profile) |

The subprocess path may become attractive if API costs grow uncomfortable. Until then, the API is the build target.

### 28.10 Publish flow — confirmation-gated direct posting

The agent can draft AND publish to X, but only via an explicit confirmation flow per post. Server-side token generation, atomic transaction, and crash recovery are non-negotiable:

1. Agent produces a draft (via `save_draft_post` or `save_draft_reply`). The orchestrator runs IWH self-score check + dark-pattern lint preflight; on failure the draft bounces back for revision (rules #12-13).
2. Draft appears in chat with a "Publish to X" button.
3. Click → confirmation modal opens showing:
   - The exact final text that will be posted
   - The target URL (for replies) with "open original" link
   - Pillar, audience, CTA classification
   - Character count (against `x_post_max_chars` = 280)
   - "I confirm this is what I want to publish" checkbox
4. Daniel checks the box → "Publish now" button activates.
5. Click "Publish now" → the **server-side Streamlit click-handler** (NOT the agent loop, NOT a tool function) generates `confirmation_token = uuid4()` and writes a new row to `publish_confirmation_tokens` with `token_hash = sha256(token)`, `post_id`, `draft_text_hash_at_issue = sha256(posts.text)`, `created_at_utc = now()`, `expires_at_utc = now() + x_posting_confirmation_token_ttl_seconds`. The raw UUID lives ONLY in the click-handler's local stack frame and is passed via synchronous Python call to `_internal_tools.publish_post_to_x(post_id, confirmation_token)`. It is NEVER written to `st.session_state` or any session-scoped store. The agent cannot observe the token's value.
6. Tool layer: BEGIN TRANSACTION → run the six-check validation chain (rule #10) → if any check fails, COMMIT (just to record nothing changed), return error to the click-handler, surface error in chat; the token stays unconsumed if its expiry hasn't passed (Daniel can fix and retry). → On all checks pass: call X API (`POST /2/tweets`) with bounded retry (`x_posting_publish_retry_attempts_per_token` times on 5xx/429). → On X API success: in the SAME transaction, set `posts.published_to_x_at`, `x_post_id`, `publish_method = agent_confirmed`, `manual_confirmation_status = confirmed`, clear `publish_last_error`, increment `publish_attempt_count`, mark `publish_confirmation_tokens.consumed_at_utc` and `consumed_by_x_post_id`, insert `agent_messages` row, update `posts.published_via_agent_message_id`, redact the raw token from the `agent_tool_calls.arguments_json`, set `redacted_arguments = true` → COMMIT. → On X API failure after retries exhausted: ROLLBACK; set `publish_last_error = "<X API error>"`, increment `publish_attempt_count`, log `agent_tool_calls` with `status = error`; the token stays unconsumed.
7. Confirmation appears in chat with the live X URL.
8. **Crash recovery (X succeeded, DB never committed):** on every app boot, scan `publish_confirmation_tokens` for rows where `expires_at_utc > now() - 1 day` (i.e., last 24h of activity) where `consumed_at_utc IS NULL` but `posts.publish_attempt_count > 0` for the same `post_id`. For each ambiguous row, call X API `GET /2/users/:id/tweets?since_id=<last_known_x_post_id>` to detect orphan posts. If a matching post is found (matched by text hash against `draft_text_hash_at_issue`), write the reconciliation row: set `consumed_at_utc`, `consumed_by_x_post_id`, update `posts.x_post_id`, `published_to_x_at`, `publish_method = agent_confirmed`, `manual_confirmation_status = confirmed`. If not found, surface a "publish state unknown — verify on X" banner with manual-mark-resolved button.

Cancellation at any step (before step 5 click) returns to draft state. The draft is never lost. Tokens left unconsumed expire silently after `x_posting_confirmation_token_ttl_seconds`; a daily VACUUM removes expired-and-unconsumed rows from `publish_confirmation_tokens` to keep the table bounded.

**Hard constraints:**
- No "publish all drafts" batch action. Each publish requires its own confirmation.
- No "auto-publish if Daniel hasn't reviewed in 24 hours" rule. Drafts can sit forever.
- No "remember this draft is approved" persistent flag. Every publish requires a fresh confirmation, even of an old draft.
- The confirmation modal cannot be triggered by the agent. Only by Daniel's click in the UI.
- Editing the draft text after a token is generated invalidates the token (rule #10 check (d) fails on hash mismatch); fresh confirmation required.
- The agent's tool registry does NOT include a tool that reads `publish_confirmation_tokens` or `st.session_state`. The token registry is unreachable from the agent loop by construction (rule #10).
- A single token authorizes ONE atomic publish operation (with bounded internal retry on transient errors). One Daniel click → at most one live X post.

### 28.11 Pre-publish heuristic scorer

The dark-pattern lint (§28.2 rule #12) catches what a draft should NOT do. The pre-publish scorer is its positive counterpart: a deterministic, fast read of what the draft IS doing well or weakly, surfaced in the UI before Daniel clicks "Publish to X." The scorer never blocks; it informs.

**Design rules:**

1. **Deterministic-first.** The default scorer is a pure Python function over the draft text + draft metadata + active voice profile. No LLM call. Same inputs → same outputs. Reasoning: an LLM scorer drifts run-to-run and would make the Today panel feel slot-machine-y; a deterministic scorer is repeatable and debuggable. The scorer lives at `app/agent/prepublish_scorer.py` and is unit-tested with golden inputs.
2. **Optional LLM augmentation, opt-in only.** A `prepublish_scorer_llm_augmentation_enabled` setting (default `false`) can layer a small-model second pass that produces the `warnings_json` plain-language read. The deterministic scores are unchanged by this layer.
3. **Voice-profile-aware.** The `voice_fit_score` reads the active `voice_profiles.profile_json` (§28.12) — vocabulary signatures, cadence, one-idea-per-line rate — and scores how close the draft sits to Daniel's actual writing. If no active voice profile exists, `voice_fit_score = NULL` and the composite label is computed without it.
4. **Never blocks the publish flow.** The §28.10 click-handler does not consult `prepublish_scores`. The scorer runs at `save_draft_*` time and writes its row; the UI consumes the row to render the panel; that's it.
5. **`composite_label` only by default.** The Today / Next Rep / Agent Chat panels show `composite_label` (`weak | viable | strong`) as a colored chip. Individual 0-3 scores reveal on click. No numeric composite. This mirrors §11 `v_lane_performance`'s graduated-confidence discipline: the precision the underlying numbers suggest is more precision than the input supports.

**Score dimensions (all 0-3, definitions live in `app/agent/prepublish_scorer.py` as docstrings on the scoring functions):**

| Dimension | What 3 looks like | What 0 looks like |
| --- | --- | --- |
| `clarity_score` | one idea, clean syntax, would be read correctly on first pass at 0.8s of attention | jargon-dense, multiple ideas mashed together, parsing required |
| `hook_strength_score` | first sentence is concrete + carries the post; would stop a scroll | generic opener; could be the first line of any post |
| `specificity_score` | real nouns, real numbers, real artifacts. "Three failed dinner attempts before 7pm." | "many," "people," "things." |
| `length_fit_score` | within `x_short_post_target_chars` (200) for a standalone, or earns its longer length | over `x_post_max_chars` (280), or way under what the idea needs |
| `format_fit_score` | sentence-per-line where appropriate; ending lands | wall of text or aimless trailing |
| `topic_fit_score` | clearly inside the declared pillar | drift from the declared pillar |
| `reply_substance_score` | addresses the original post substantively before pivoting to Daniel's angle | thin "great post" with a self-promo bolt-on |
| `cta_strength_score` | clear ask matching `cta` field; reader knows what to do next | "what do you think?" generic ask, or no ask when one is declared |
| `voice_fit_score` | reads like a row sampled from `voice_profiles.profile_json`'s `vocabulary_signatures` / `cadence` | reads like an LLM trying to sound like a creator |

**`composite_label` derivation:** see §10 `prepublish_scores` table notes.

**UI surfaces:**

- Today (§14.1) — when an agent draft is on the page, a `composite_label` chip next to the draft text.
- Next Rep (§14.2) — same as Today for per-lane drafts.
- Agent Chat (§14.8) — `composite_label` chip + click-to-reveal scores panel in the draft action row.
- Content Performance (§14.4) — post-publish view of historical `prepublish_scores` for shipped agent drafts, so Daniel can correlate "what the scorer said before I published" with "what actually happened." Drives §28.11 calibration over time.

**Calibration cadence:** after every 50 shipped agent drafts (or quarterly, whichever comes first), Daniel reviews the `composite_label` × actual-engagement scatter in Content Performance and tunes the threshold constants in §10 `prepublish_scores` notes if the labels are mis-calibrated to his sense.

### 28.12 Generated voice profile

A compact JSON description of how Daniel actually writes — cadence, hooks, vocabulary, tone — synthesized from his recent posts and spliced into the system prompt alongside `voice_samples`. See §10 `voice_profiles` table for the persisted schema.

**Workflow:**

1. Daniel opens Settings → Growth Agent → "Voice profile."
2. The panel shows the currently-active profile (`is_active = true`) with metadata: when it was generated, how many source posts fed it, the `self_description` line that gets spliced into Section 1 of the system prompt.
3. "Regenerate from last N days" button (N defaults to `voice_profile_window_days = 90`, editable per-run). On click:
   - Query `posts` for rows where `x_post_id IS NOT NULL AND text IS NOT NULL AND created_at_utc >= now() - N days`.
   - If `count < voice_profile_min_source_posts` (default 10), abort and show "not enough posts in window — try a longer N or wait until you've shipped more."
   - Otherwise, single Haiku call with a structured-output prompt (template at `config/voice_profile_prompt.md`) that returns the `profile_json` schema in §10.
   - Insert the new row; in the SAME transaction, set the previously-active row's `is_active = false` and `superseded_by_profile_id`. Atomic activation — never a moment when 0 or 2 rows are active.
   - On failure (model returns bad JSON, Anthropic 5xx after bounded retry): leave the active row unchanged, surface "regeneration failed, prior profile still active."
4. Diff view: when a new profile is generated, the panel shows a side-by-side of the old and new `profile_json` so Daniel can see what shifted before committing — but commit is automatic on successful generation; the diff is informational.

**Splice into system prompt:**

- §28.3 Section 1 (Identity and niche) — at build time, append `voice_profiles.profile_json.self_description` as the closing paragraph of Section 1, prefixed with `"Voice self-description (generated from your last N days):"`.
- §28.3 Section 5 (Voice samples) — at build time, prepend a compact rendering of `cadence`, `vocabulary_signatures[:5]`, `stop_phrases[:5]` from the profile, ABOVE the raw voice samples.
- `app/agent/prompt_builder.py` is responsible for the splice; the pre-commit drift check (§28.3 build step) is extended to verify the splice happens and the `voice_profiles` row count `is_active = true` is exactly 0 or 1.

**Privacy and read scope:** the profile is generated from `posts` rows only — never from `stir_testers`, `stir_conversion_events.qualitative_feedback`, or any `agent_messages`. The generation prompt explicitly enumerates the read scope, and the small-model call gets only the post text + classifications, never tester PII.

**No automatic regeneration.** No cron. Daniel decides when his voice has shifted enough to refresh. The Settings panel surfaces "last regenerated N days ago" so it's visible without nagging.

### 28.13 Repetition guard via embedding similarity

Before saving a draft, the orchestrator computes a cosine-similarity check against Daniel's recent posts. The goal is not to block repetition (sometimes a returning idea earns the repeat); it is to surface "you said almost exactly this on 2026-04-12" so Daniel can decide consciously. See §10 `post_embeddings` table for the persisted schema.

**Flow:**

1. At `save_draft_post` / `save_draft_reply` time, after IWH self-score check and dark-pattern lint pass, the orchestrator calls `app/agent/repetition_guard.py::check(draft_text, draft_kind, lookback_days)`.
2. The guard embeds the draft text (single embedding call to the configured provider) and cosine-compares against `post_embeddings` rows whose parent `posts.created_at_utc >= now() - repetition_guard_lookback_days` (default 180).
3. Top-1 nearest neighbor + its cosine distance are returned.
4. The orchestrator writes `agent_drafts.similarity_warning_json` per §10's schema, with `label` derived from the cosine score:
   - `near_duplicate` if cosine >= `repetition_guard_near_duplicate_threshold` (default 0.92)
   - `close_echo` if cosine >= `repetition_guard_close_echo_threshold` (default 0.78)
   - `distinct` otherwise
5. The UI surfaces `near_duplicate` and `close_echo` as a yellow banner above the draft text, with the linked nearest post's text excerpt and a "Yes, I'm intentionally returning to this idea / No, let me rewrite" choice. `distinct` shows nothing.

**Soft check, never a gate.** The guard does not block `save_draft`. The IWH framework + dark-pattern lint are the hard gates; this is informational. Daniel can ship a near-duplicate consciously.

**Backfill at Phase 5.8 install:** all rows in `posts` where `x_post_id IS NOT NULL AND text IS NOT NULL` get embedded in a one-shot backfill script (`scripts/embed_posts.py`); the script is resumable (checks `post_embeddings.post_id` before embedding) and respects the configured embedding provider's rate limits.

**Provider choice:** the embedding provider is a configuration in `app/agent/embeddings.py`, NOT a setting. Defaults: Voyage AI's `voyage-3-lite` (cheap, 1024-dim, good for this use). OpenAI's `text-embedding-3-small` is the documented alternative. Switching providers requires (1) editing the adapter, (2) running `scripts/embed_posts.py --re-embed-all`, (3) the Settings → Maintenance UI shows the migration state. No silent provider swap.

**Failure modes:**

- Embedding provider unavailable / rate-limited / API key missing → guard returns `similarity_warning_json = NULL`; draft proceeds; Settings → Growth Agent shows "repetition guard offline" banner.
- `post_embeddings` table is empty (fresh install before backfill) → guard returns `similarity_warning_json = NULL`; first-run Settings panel surfaces "run `scripts/embed_posts.py` to enable the repetition guard."
- `posts.text` was edited after embedding → `source_text_hash` mismatch on read; guard re-embeds that one post inline before computing the comparison.

### 28.14 Confidence labels on agent outputs

The structural enforcement of §28.2 rule #14. Every analytical claim the agent emits carries one of four labels: `fact`, `inference`, `speculation`, `mixed`.

**Tag format:**

The agent emits inline `<confidence>fact</confidence>` (etc.) tags adjacent to the claim they label. For drafts that include an analytical justification, the tag attaches to the justification, not the draft text itself. Example agent output:

```
Here's a draft for the build lane:

[draft text]

Reasoning: the build lane has 0 posts in the last 7 days <confidence>fact</confidence>,
which suggests it's a good slot to fill <confidence>inference</confidence>. A
specificity-forward hook would likely outperform a generic one <confidence>speculation</confidence>.
```

**Orchestrator parsing (`app/agent/session.py::extract_confidence_labels`):**

1. After the agent's response is assembled, scan the message text for `<confidence>([a-z]+)</confidence>` tags.
2. Validate each captured label against `{fact, inference, speculation, mixed}`. Unknown labels → IWH humility failure (rule #13).
3. Persist the dominant label (most frequent; ties broken in the order speculation > inference > mixed > fact, i.e. the least-confident tag wins ties, which favors humility) on:
   - `agent_drafts.confidence_label` for messages that produced a draft (linked via `save_draft_*` tool result).
   - `agent_messages.confidence_label` for analytical messages without a draft.
4. If the message contains analytical claims (detected by regex sweep against `app/agent/confidence_patterns.py` — patterns include "lane X is the winner", "this caused", "%-change phrasing", "outperformed", etc.) AND no `<confidence>` tag is present, increment the IWH humility-failure counter for the current draft.

**UI surface:**

- Agent Chat (§14.8) — confidence labels render as small colored chips inline with the claim text: green `fact`, blue `inference`, yellow `speculation`, gray `mixed`. Hovering shows the label name.
- Content Performance (§14.4) — per-post agent reasoning shows the historical confidence label so Daniel can see which lessons the agent was confident vs. guessing about.
- Weekly Review (§14.6) — agent-drafted sections (`draft_weekly_review_section`) carry a per-section confidence label; the export-blocked rule (§24) is extended: a section with `confidence_label = speculation` cannot be exported until Daniel either edits it or marks "I'm publishing this speculation deliberately."

**Why this is a separate rule from §28.2 #12 / #13:** dark-pattern lint catches manipulation; IWH catches shallowness; confidence labels catch overclaiming. They overlap but are not redundant — a draft can be honest (no dark pattern), substantive (IWH passes), and still slide a `speculation` past as if it were `fact`. The label system makes the epistemic claim explicit at the structural level.

### 28.15 Approval payload hash — user-visible enforcement

§28.10 step 5 + §10 `publish_confirmation_tokens.draft_text_hash_at_issue` already enforce that editing the draft text after the confirmation token is generated invalidates the token. This subsection extends that enforcement with a user-visible warning so the failure mode is debuggable instead of silent.

**Behavior:**

1. When the confirmation modal opens, the modal computes `current_draft_text_hash = sha256(posts.text)` and stores it in the modal's React-style component state (Streamlit: a deterministic `st.session_state[f"modal_hash_{post_id}"]` key, scoped to the modal lifetime).
2. The modal renders an editable text area pre-filled with `posts.text`. Daniel can edit in place.
3. On every keystroke (debounced to `modal_hash_recheck_debounce_ms`, default 300), the modal recomputes `sha256(text_area_value)` and compares to `current_draft_text_hash`.
4. If the hashes differ, the modal:
   - Shows a yellow banner: "You've edited this draft since opening the modal. The approval hash will be regenerated when you click Publish — this is fine, just confirming you meant to."
   - Disables the "Publish now" button for `modal_edit_settle_seconds` (default 2) so a stray paste doesn't trip an immediate publish.
   - Re-enables Publish after the settle delay.
5. On click "Publish now":
   - Streamlit click-handler reads the current text from `st.session_state[f"modal_text_{post_id}"]`.
   - If different from `posts.text`, the handler issues an `UPDATE posts SET text = ? WHERE id = ?` BEFORE generating the confirmation token. The token's `draft_text_hash_at_issue` is computed against the just-updated text, not the pre-edit version.
   - Audit row in `agent_tool_calls` records both the pre-edit and post-edit hashes when they differ, with `notes = "draft edited at modal time"`.
6. If a token already exists for this `post_id` (e.g. Daniel opened the modal twice), the handler INVALIDATES the prior token (set `expires_at_utc = now() - 1 second`) before generating the new one. Audit-logged. This prevents the "two modals open, two valid tokens" race.

**Why surface this as a separate subsection instead of folding into §28.10:** §28.10's existing hash-mismatch check is correct but silent — a Daniel edit between modal-open and Publish-click currently produces a confusing "token validation failed" error. The user-visible warning + automatic token regeneration in §28.15 turns the silent failure into a smooth experience: the hash mechanism is still load-bearing security, but Daniel sees what's happening.

**Hard constraints (carried from §28.10):**

- The token is still server-side-generated, single-use, TTL-bounded, and SHA-256-hashed at rest.
- The agent still has no read access to `publish_confirmation_tokens` or `st.session_state`.
- Two simultaneous modal sessions still cannot both publish — the second token-mint invalidates the first by construction.

---

## 29. Reply Target Discovery

### Core principle

Replies are distribution. A reply is not communication with the author; it is a small post inserted into an existing attention pool. The reader the reply actually reaches is the comment-section audience of the target post — not the target author.

This reframes what "a reply" optimizes for. Under a low-engagement post, the reply's audience is one person (the author). Under a 300-engagement post in a relevant lane, the reply's audience is the next 30–60 people who scroll the thread. Daniel's first reply sessions optimized for the former by accident; the system from now on optimizes for the latter on purpose.

§29 introduces a workflow for discovering, scoring, and managing candidate posts to reply *under* — the targets — separately from the replies themselves (which continue to live in `posts` as `type = reply`, §10.2).

### 29.1 Scope and version boundaries

**MVP (this section, Phase 5.6 in §25):**

* Manual candidate entry (Daniel pastes URLs, optionally with text and engagement-metric snapshots).
* Four scoring dimensions, no composite score.
* Status lifecycle (`candidate → drafted → posted → expired | skipped | target_deleted`).
* Reply Target Queue view (§29.6) and integration with §14.2 Next Rep.
* Agent extensions to §28.4 tools #6 `score_reply_candidates` and #7 `record_reply_target`.
* Manual reply posting only (clipboard handoff + URL backfill).

**V1.1 — adds X API read access:**

* Automatic candidate enrichment (pull current likes/replies/reposts on save).
* `Velocity` dimension activated (requires repeated snapshots → see `reply_target_snapshots`).
* `Timing` dimension activated.
* Thread-classifier lint pass (§29.10; mirrors §28.2 rule #12 dark-pattern lint pattern).

**V1.2 — adds X API write access:**

* Direct reply posting via X API, *only* under the §28.10 publish-flow contract (token-gated, atomic, auditable). X API may return 403 on cold replies to third-party posts; the manual path remains the always-available fallback.
* **Evaluate Grok X-Search as an optional discovery provider.** Decision deferred to V1.2 explicitly. The unique value Grok offers is real-time X-firehose access for trending discovery in Daniel's lanes — not semantic query expansion (Claude does that adequately, and ~20 hand-written saved searches capture most of it). At MVP and V1.1 candidate volume (~15/day), the cost difference between Grok X-Search ($5 / 1,000 calls) and X API search via xurl is rounding error in either direction; pick on capability, not cost. If Grok is integrated, it slots in via `reply_targets.discovered_via='grok_semantic'` (new enum value) and stays subject to the same §29.2 verification rule: Grok-discovered candidates must still have their engagement metrics confirmed against the X API before they affect `engagement_surface_score`. Grok never replaces the X API as the source of truth for any metric.

**Never in scope:**

* Black-box composite "viability score" with hidden weights. The dashboard shows the dimension scores directly; deterministic `recommended_action` is derived from them. Same rule as §14.4 lane confidence labels: show the inputs, refuse the false summary.
* Stir-download attribution to a specific reply. §14.5's App Store attribution gap applies. Reply postmortems track impressions/likes/replies on the reply itself, never downstream funnel events.

### 29.2 Reconciliation with existing structures

Four places in the existing spec touch reply-related concepts. §29 extends rather than duplicates them:

| Existing concept | What it tracks | Relationship to §29 |
| --- | --- | --- |
| `agent_target_accounts` (§10.2) | Curated *accounts* Daniel wants to engage with over time | `reply_targets.target_author_handle` MAY match an `agent_target_accounts.x_handle`. When it does, the candidate's `relevance_score` gets a +1 prior. Not exclusive: Daniel can target posts from accounts that aren't on the curated list. |
| `reply_sessions` (§10.2) | Daniel's intentional reply-work time blocks | A reply session optionally references the set of `reply_target_id`s worked through in that block via `reply_sessions.target_reply_target_ids_json`. Sessions stay a time/intent concept; targets stay a content concept. |
| `posts` with `type = reply` (§10.2) | Replies Daniel has actually posted | A posted reply MAY link to its originating candidate via new column `posts.in_reply_to_reply_target_id` (nullable FK, ON DELETE SET NULL). Manually-posted replies that bypass the queue stay supported (NULL FK). |
| §14.2 Next Rep view | Daily generative prompt | Now shows the top 3–5 rows from `reply_targets WHERE status = 'candidate' ORDER BY recommended_action_score DESC`. The full Reply Target Queue (§29.6) is the dedicated detail view. No duplicated state — Next Rep is a window onto the queue, not a parallel list. |

`reply_targets` is the new MVP table. `reply_target_snapshots` arrives in V1.1 alongside automated metric refresh.

### 29.3 Scoring model — four MVP dimensions, no composite

Each candidate is scored on **four dimensions**, each on a 0–3 scale (matches the IWH scale from §28.2 rule #13 and the confidence-label scale from §11). The dashboard shows all four; it does not produce a hidden weighted sum.

| Dimension | 0 | 1 | 2 | 3 | MVP signal source |
| --- | --- | --- | --- | --- | --- |
| **Relevance** | Off-topic for Daniel's lanes | Tangentially related | Within a current pillar | Directly under a current open hypothesis (§14.2) | Daniel-tagged at save; agent-suggested via tool #6 |
| **Engagement surface** (relative to author follower count and Daniel's count) | Below medium threshold | Between medium and high | Above high threshold but below "saturated viral" | Above high, comment thread still navigable | Computed from `like_count` + `target_author_follower_count`; see §29.4 |
| **Saturation** | Reply would be #500+; thread is dead | Reply would be in top 100; thread crowded | Reply would be in top 30; thread active | Reply would be in top 10; thread fresh | Computed from `reply_count` and `post_age_minutes` |
| **Reply opportunity** | Generic-only ("so true," "this") | Possible but weak angle | A real specific angle Daniel can write | Daniel has a strong, specific, top-10% reply already in mind | Daniel-judged at save; agent-suggested via tool #6 |

`recommended_action` is **deterministic** from the four scores:

```text
if any score == 0:                                                       → 'skip'
elif relevance >= 2 and engagement_surface >= 2 and saturation >= 2
     and reply_opportunity >= 2:                                         → 'reply_now'
elif relevance >= 2 and reply_opportunity >= 2:                          → 'reply_if_time'
else:                                                                    → 'consider'
```

Stored as `reply_targets.recommended_action_label`. The UI sorts the Queue by an integer ordering: `reply_now (3) > reply_if_time (2) > consider (1) > skip (0)`.

**V1.1 adds two more dimensions** when the metrics-refresh job is running:

| Dimension | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| **Velocity** | Decaying or stale | Flat | Modest engagement gain over last hour | Accelerating: top-quartile per-hour gain for the author's typical post |
| **Timing** | Past optimal window for the author tier | Late in the window | Within the window | Early in the window (first 30 min for large-account targets, first 6h for small-niche) |

**V1.2+ adds** an audience-quality classifier as a seventh dimension.

Until V1.1+ metrics are flowing, `velocity_score` and `timing_score` default to NULL (not 0) and the deterministic action ignores them. The composite "Viability 82/100" score from the original Reply-Target-Finder draft is deliberately omitted: a weighted sum across heterogeneous 0–3 scores fabricates precision that doesn't exist at <100-follower scale.

### 29.4 Engagement-surface thresholds — relative, not absolute

Absolute thresholds ("low: <10 likes") misclassify everything in Daniel's actual reply window. Daniel has 64 followers; a target post with 30 likes from an aligned 500-follower niche account is large for him. The same 30 likes on a 50K-follower account's post is nothing.

Thresholds are computed at score time from settings (§10.2):

```text
medium_like_threshold = max(
    engagement_surface_floor_likes,                  # default: 15
    engagement_surface_pct_of_author *
        target_author_follower_count                 # default pct: 0.001 (= 0.1%)
)

high_like_threshold = max(
    engagement_surface_high_floor_likes,             # default: 50
    engagement_surface_high_pct *
        target_author_follower_count                 # default pct: 0.005 (= 0.5%)
)
```

If `target_author_follower_count` is NULL (manual entry, no API), fall back to the floor values and label the score in the UI with a "no author size" footnote so Daniel knows the score is using the conservative floor.

All four parameters live in `settings`. The day-21 calibration prompt (already wired for the reply-target ratio in §14.7) is extended to re-ask Daniel to validate these thresholds against his actual best-performing reply targets.

### 29.5 Reply intent — orthogonal to pillar / audience / CTA

The original draft proposed five categories (Growth / ICP / Relationship / Product / Thought-leadership) and would have collided with the existing pillar × audience × CTA taxonomy (§15.3). Resolution: **reply intent is a fourth orthogonal axis**, stored separately on the candidate and carried onto the posted reply.

| Axis | Lives on | v1 values | Describes |
| --- | --- | --- | --- |
| Pillar | `post_classifications` (existing) | `stir` / `build` / `self` | Daniel's content position |
| Audience | `post_classifications` (existing) | `icp` / `other` | Who the *post* speaks to |
| CTA | `post_classifications` (existing) | `ask` / `none` | What Daniel asks of the reader |
| **Reply intent** | `reply_targets.reply_intent` + `posts.reply_intent` | `growth` / `icp_discovery` / `relationship` / `product_adjacent` / `thought_leadership` | Daniel's strategic goal for *this specific reply* inserted into the comment-section attention pool |

A single reply can be (pillar=stir, audience=icp, cta=none, reply_intent=icp_discovery). Another can be (pillar=build, audience=other, cta=none, reply_intent=relationship). The four axes don't collapse into each other.

The v1 `reply_intent` enum is set by Daniel at draft time (or suggested by the agent and confirmed). It's editable post-hoc when the postmortem reveals the reply produced a different actual effect.

### 29.6 Data model

#### `reply_targets` (new)

```text
id integer primary key
discovered_at_utc text not null
discovered_via text not null
  -- 'manual' | 'agent_score' | 'next_rep_seed' | 'v1.1_api_search'

-- Target identity
source_platform text not null default 'x'
target_post_url text not null
target_x_post_id text                       -- nullable until parsed/API-confirmed
target_author_handle text not null
target_author_display_name text
target_author_follower_count integer        -- nullable; populated when known

-- Target content
target_text text                            -- nullable at MVP if Daniel pastes URL only
target_created_at_utc text                  -- approximate; backfilled on API enrichment
post_age_minutes integer                    -- computed at last_checked_at

-- Engagement snapshot (latest known; copied from snapshots in V1.1+)
last_checked_at_utc text not null
like_count integer
reply_count integer
repost_count integer
quote_count integer
bookmark_count integer                      -- may be unavailable
impression_count integer                    -- may be unavailable

-- Scores (0-3 each; NULL when not yet scored)
relevance_score integer
engagement_surface_score integer
saturation_score integer
reply_opportunity_score integer
velocity_score integer                      -- V1.1+; NULL until metrics-refresh runs
timing_score integer                        -- V1.1+; NULL until metrics-refresh runs
audience_quality_score integer              -- V1.2+; NULL until classifier runs

recommended_action_label text
  -- 'reply_now' | 'reply_if_time' | 'consider' | 'skip'
recommended_action_score integer            -- 3 | 2 | 1 | 0 for ORDER BY
score_rationale text                        -- one-paragraph human-readable explanation

-- Taxonomy (Daniel's intended angle if/when he replies)
pillar text                                 -- v1: stir / build / self
audience text                               -- v1: icp / other (thread's audience)
reply_intent text                           -- v1 enum, see §29.5
topic_tags_json text                        -- free-form tags

-- Lint pass output (V1.1+; mirrors §28.2 rule #12 pattern)
lint_thread_classification_json text
  -- { ragebait: bool, meme_with_no_serious_reply_path: bool,
  --   low_quality_reply_thread: bool, hijacking_required: bool,
  --   rationale: string }
lint_blocked boolean default false

-- Status lifecycle
status text not null default 'candidate'
  -- 'candidate' | 'drafted' | 'posted' | 'skipped' | 'expired' | 'target_deleted'
skip_reason text                            -- nullable; set when status='skipped'
expired_at_utc text                         -- set on transition to 'expired'

-- Cross-references
agent_draft_id integer references agent_drafts(id) on delete set null
posted_reply_post_id integer references posts(id) on delete set null

-- Audit
created_via_agent_message_id integer references agent_messages(id) on delete set null
notes text
```

Indexes:

```text
unique(target_post_url)
unique(target_x_post_id) where target_x_post_id is not null
index(status, recommended_action_score desc, last_checked_at_utc desc)
index(reply_intent) where status = 'posted'
```

#### `reply_target_snapshots` (V1.1+)

```text
id integer primary key
reply_target_id integer not null references reply_targets(id) on delete cascade
checked_at_utc text not null
like_count integer
reply_count integer
repost_count integer
quote_count integer
bookmark_count integer
impression_count integer
computed_likes_per_hour real
computed_replies_per_hour real
computed_velocity_delta real                -- vs. previous snapshot
```

Snapshots are immutable. Latest values are copied onto the parent `reply_targets` row for cheap reads; the history exists for velocity computation only.

#### `posts` additions (two new columns)

```text
in_reply_to_reply_target_id integer
  references reply_targets(id) on delete set null
reply_intent text                           -- v1 enum, see §29.5; only meaningful when type='reply'
```

Manually-posted replies that bypass the queue leave `in_reply_to_reply_target_id` NULL. The Queue's "Mark posted" action sets it; the V1.2+ publish-flow click-handler sets it as part of the §28.10 atomic transaction.

#### `reply_sessions` additions (one new column)

```text
target_reply_target_ids_json text           -- JSON array of reply_target_id worked in this session
```

#### `settings` additions (§10.2)

```text
engagement_surface_floor_likes integer default 15
engagement_surface_pct_of_author real default 0.001
engagement_surface_high_floor_likes integer default 50
engagement_surface_high_pct real default 0.005
reply_candidate_review_daily_target integer default 15
reply_high_engagement_mix_pct real default 0.5
  -- target fraction of shipped replies with engagement_surface_score >= 2
reply_target_expiry_hours integer default 24
reply_target_lint_enabled boolean default true   -- V1.1+; can be disabled to save cost
```

### 29.7 Reply Target Queue — UI

The dedicated view is reachable from the left-nav sidebar and from §14.2 Next Rep's "Reply targets" panel ("see full queue →"). It is the **ninth** view in the MVP (extending the eight-view list in §19; the §19 view list is updated accordingly).

Top-level layout:

```text
┌─────────────────────────────────────────────────────────────┐
│ Reply Target Queue                                           │
│ Candidates: 12   Drafted: 3   Posted today: 5   Skipped: 2  │
└─────────────────────────────────────────────────────────────┘

Filters: status · pillar · reply_intent · recommended_action · author

[ + add candidate (paste URL) ]

┌─────────────────────────────────────────────────────────────┐
│ ▸ @builder_account · 74 min ago                              │
│   "Building an app is not the same as building a business…"  │
│   312 likes · 48 replies · velocity: — (V1.1)                │
│                                                              │
│   Relevance: 3   Engagement: 3   Saturation: 2   Reply opp:3 │
│   → reply_now · pillar=build · intent=relationship           │
│   Rationale: Active thread in build pillar; aligned audience;│
│   Daniel can contribute the "distribution-as-product" angle. │
│                                                              │
│   [Open original] [Draft reply] [Skip] [Mark posted]         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ ▸ @parenting_account · 6h ago                                │
│   "I'm so tired of the 5pm dinner question…"                 │
│   84 likes · 22 replies · velocity: — (V1.1)                 │
│                                                              │
│   Relevance: 3   Engagement: 2   Saturation: 2   Reply opp:2 │
│   → reply_if_time · pillar=stir · intent=icp_discovery       │
│   Rationale: Direct ICP pain; engagement above niche-medium  │
│   threshold; reply can be specific without pitching Stir.    │
│                                                              │
│   [Open original] [Draft reply] [Skip] [Mark posted]         │
└─────────────────────────────────────────────────────────────┘
```

**Operations table:**

| Action | Effect |
| --- | --- |
| Add candidate (paste URL, optionally text + metrics) | Insert new row, `status='candidate'`, `discovered_via='manual'`. Auto-invoke §28.4 tool #6 `score_reply_candidates` to populate scores + rationale before render. |
| Draft reply | Opens Agent Chat (§14.8) with `score_reply_candidates([this])` and `save_draft_reply` pre-armed. On save: sets `reply_targets.agent_draft_id`, transitions status to `drafted`. |
| Skip | `status='skipped'`, captures `skip_reason` from a dropdown: `off_topic` / `ragebait` / `saturation` / `cant_add_value` / `target_deleted` / `blocked_by_author` / `other`. |
| Mark posted (manual mode) | Daniel pastes the posted reply URL. Click-handler parses `x_post_id`, inserts/updates the `posts` row with `type='reply'`, sets `posts.in_reply_to_reply_target_id` and `posts.reply_intent`, transitions `reply_targets.status` to `posted` and sets `posted_reply_post_id`. |
| Mark posted (V1.2+ API mode) | Goes through the §28.10 publish-flow contract (token-gated, atomic, auditable). On success, all the above happens inside the publish transaction. |
| Expire (auto) | App-boot job + once-daily job: any `status='candidate'` with `last_checked_at_utc + reply_target_expiry_hours < now()` → `status='expired'`. |
| Target deleted | V1.1+: metrics-refresh detects 404 on `target_x_post_id` → `status='target_deleted'`. If a draft exists, surface "draft orphaned" banner per §29.11. MVP: Daniel uses Skip with reason `target_deleted` when he encounters a dead URL. |

§14.2 Next Rep continues to show the top 3–5 `reply_now` candidates inline; the dedicated Queue is the deep view.

### 29.8 Agent integration — expansion of §28.4 tools #6 and #7

The two existing agent tools (§28.4) extend rather than duplicate:

* **#6 `score_reply_candidates`** — input now optionally accepts either a candidate dict (URL + text + observed metrics) or an existing `reply_target_id`. Returns the four MVP scores, `recommended_action_label`, and `score_rationale`. Side effect: persists the scoring on the `reply_targets` row, creating the row if the input was a fresh candidate. V1.1+: also persists the lint classification and may set `lint_blocked=true`.
* **#7 `record_reply_target`** — signature unchanged; now writes to the expanded schema. New columns default per the schema above.

**No new agent tools are added for §29.** The agent never gets a tool that posts a reply directly — same contract as §28.4 #10/#11. The post-reply path (V1.2+) goes through the publish-flow click-handler.

**System prompt changes (§28.3):**

* Section 6 (Current taxonomy) gains the `reply_intent` enum.
* Section 7 (Tool catalog) shows the expanded inputs to tools #6 and #7.
* Section 4 (Engagement psychology) is **not** duplicated — it already governs what makes a reply good. §29 governs which targets are worth replying *under*.
* The pre-commit drift check (§28.3 build step) is extended to verify the `reply_intent` enum values in the spec match those in `app/agent/tools.py` and the system prompt template.

### 29.9 Daily workflow integration

Daniel's daily reps target (§14.1) does not change: 12 replies/day with the 21-day calibration review. §29 adds **one configurable sub-target**: of the shipped replies, what fraction came from candidates with `engagement_surface_score >= 2`?

`v_daily_reps` (§11 computed views) gains four columns:

```text
candidates_reviewed_today integer
  -- count of reply_targets touched (any status transition) today
high_engagement_replies_shipped integer
  -- posts shipped today with engagement_surface_score >= 2 at posting time
icp_intent_replies_shipped integer
  -- posts shipped today with reply_intent='icp_discovery'
average_engagement_surface_of_posted real
  -- mean engagement_surface_score over today's shipped replies
```

The Today view's daily-reps checklist (§14.1) shows:

```text
Replies today: 5 / 12
  · 3 high-engagement (engagement_surface_score >= 2)
       target: 50% of shipped (reply_high_engagement_mix_pct, configurable)
  · 1 icp_discovery
  · 12 candidates reviewed
       target: 15 (reply_candidate_review_daily_target, configurable)
```

The 60/30/10 mix from the original Reply-Target-Finder draft is replaced by a single configurable target (`reply_high_engagement_mix_pct`, default 0.5). Other intent-mix targets can be added later if data justifies them; until then, one knob is enough — same restraint as the §14.7 "Daily reps" defaults.

### 29.10 Lint pass — skip / prioritize as enforceable logic, not prose

The skip/prioritize rules from the original draft become an enforcement layer in V1.1+ when an Anthropic client is available for small-model calls. Same pattern as §28.2 rule #12's dark-pattern lint.

The lint runs once per candidate when it is first scored:

```text
Input:  target_text, target_author_handle, observed engagement metrics
Output: {
  ragebait: bool,
  meme_with_no_serious_reply_path: bool,
  low_quality_reply_thread: bool,
  hijacking_required_to_mention_stir: bool,
  rationale: string  (one line)
}
```

**Blocking rules:**

* If `ragebait = true` OR `hijacking_required_to_mention_stir = true` → `lint_blocked = true`. The candidate still appears in the Queue (transparency) but the row renders with the lint rationale and the "Draft reply" button is **disabled**, with the rationale as tooltip. Daniel can override via a "Force-draft (overrides lint)" affordance; the override is logged with `agent_tool_calls.notes = 'lint override: <Daniel's one-line reason>'`.
* `meme_with_no_serious_reply_path = true` and `low_quality_reply_thread = true` are *signals*, not blocks: each subtracts 1 from `reply_opportunity_score` (floored at 0).

The lint runs on a small model (Haiku per §28.2 rule #12 convention), bounded by `reply_target_lint_enabled` (settings) and folded into the §28.6 monthly cost ceiling. Cost in practice: tens of cents per month at expected candidate volume.

### 29.11 Edge cases — additions to §22

| Edge case | Required behavior |
| --- | --- |
| Target post deleted between candidate creation and reply post | **V1.1+:** metrics-refresh detects 404 → `status='target_deleted'`. If a `drafted` row exists, surface banner: "target deleted — draft orphaned; choose: delete draft / repurpose as standalone post". **MVP:** no automatic detection; Daniel sees the dead URL when clicking "Open original" and uses Skip with `skip_reason='target_deleted'`. |
| Target author blocks or restricts replies | **Manual mode:** Daniel discovers at post time; uses Skip with `skip_reason='blocked_by_author'`. **V1.2+ API publish:** X API returns the relevant 403; the publish-flow tool surfaces it the same as any X API auth/permission error per §28.10 step 6. |
| Reply posted manually but Daniel forgets to record URL | The candidate stays `status='drafted'` indefinitely. App-boot job: any `reply_targets` row with `status='drafted'` and `agent_draft_id` older than 24h surfaces a "Did you post this? Record URL or close as skipped" banner in the Queue. |
| Candidate goes stale (older than `reply_target_expiry_hours`) | App-boot job + once-daily job: `status='candidate' AND last_checked_at_utc + reply_target_expiry_hours < now()` → `status='expired'`, `expired_at_utc=now()`. Expired rows remain queryable for postmortem analysis (e.g., "did skipping expired candidates correlate with missed growth?"). |
| `reply_targets` table growth unbounded | Daily VACUUM job (extension to §17 V1.1 scheduling) deletes rows where `status IN ('skipped','expired','target_deleted') AND discovered_at_utc < now() - 90 days`. Posted candidates stay (joined via `posted_reply_post_id` for postmortem audit). |
| Daniel drafts a reply, then deletes the candidate | Treat the draft as a standalone-reply orphan: the `posts` row stays; `posts.in_reply_to_reply_target_id` becomes NULL by the ON DELETE SET NULL rule. Daniel can re-link via the Queue or repurpose the draft. |
| Two candidates added for the same target URL | The `unique(target_post_url)` index rejects the second insert. The "Add candidate" UI surfaces "already in queue — open existing row" instead of creating a duplicate. |
| Agent attempts to draft a reply against a candidate Daniel has not added | The agent's `save_draft_reply` (§28.4 #5) requires `target_post_url`. If the URL doesn't match an existing `reply_targets` row, the orchestrator first auto-creates the candidate (calling tool #7 internally), scores it (tool #6), then proceeds with `save_draft_reply`. Visible to Daniel as three tool-call blocks in the chat. |
| Lint pass false-positive blocks a relevant candidate | Daniel's "Force-draft (overrides lint)" proceeds; the override is logged. Day-21 calibration view surfaces all lint blocks and overrides for review: if Daniel is overriding the lint more than 20% of the time, the lint prompt or `reply_target_lint_enabled` setting is the wrong calibration. |
| Engagement-surface thresholds misfire as Daniel's follower count grows | The day-21 calibration (and every subsequent calibration window from §14.7) re-shows the four threshold settings alongside the actual distribution of `engagement_surface_score` on Daniel's posted replies and a prompt: "is this still the right floor?" |
| V1.1+ metrics-refresh hits X API rate limit while updating candidates | Refresh job logs the rate-limit hit and skips that candidate's update for this cycle. `last_checked_at_utc` stays at its previous value. No silent score drift. |
| Candidate has `target_author_follower_count = NULL` | Engagement-surface formula falls back to floor values. UI labels the score with a footnote: "no author size — using floor thresholds". Score is not penalized, only labeled. |
| Daniel imports a list of candidates via CSV (V1.5+ "Reply session planner") | CSV import follows the §16 CSV import contract (preview, validate, dedupe by `target_post_url`, rollback). Each row scored on insert via tool #6. |

The §22 master edge-case table gets a one-line pointer at the bottom: "See §29.11 for reply-target-discovery edge cases."

### Anti-feature notes

The original Reply-Target-Finder draft included two ideas that §29 deliberately drops:

* **"Viability 82/100" composite score.** Heterogeneous 0–3 dimensions don't sum into a meaningful 100-point scale at <100-follower scale. The four (eventually seven) dimension scores and the deterministic `recommended_action` carry the same information without the false precision.
* **Auto-attribution of Stir downloads / follower growth to specific replies.** §14.5 App Store gap and §13 hard rule "never claim causal attribution" apply unchanged. Reply postmortems track impressions/likes/replies on the reply itself; downstream funnel effects stay in the funnel view.

---

## 30. Changelog — 2026-05-21 revision

Audit-driven rewrite of the original spec. Substantive changes:

### Structural

1. **Demoted 500k to long-arc reminder.** Introduced `operational_ceiling = 5,000` as the daily-operational anchor. 500k now appears only in a small "long-arc footer" on the Progress view, with no progress bar. (§2, §4, §6, §10 settings, §14.3)
2. **Added validation milestone ladder with equal depth to distribution ladder.** 6 rungs each, from "first download" to "5 Cook Mode completions in a week." (§10 `milestones`, §14.1, §14.3, §23.2)
3. **Collapsed content taxonomy to v1 minimum (3 × 2 × 2 = 12 cells) with v2 expansion path documented.** Old taxonomy of 9 × 6 × 8 = 432 cells could not populate densely enough to learn anything at current post volume. Schema uses `text` columns so v2 is a config change, not a migration. (§10 `post_classifications`, §15.3, §14.4)
4. **Flipped manual/API priority.** Manual entry is now the MVP default path; xurl is V1.1, direct API is V1.2. The Today view has a pinned manual snapshot form. (§7.1, §7.2, §10 settings, §14.1, §15.1, §17, §19, §20)
5. **Added Next Rep view** between Today and Progress. Closes the loop between measurement and the generative act by surfacing under-sampled lanes and open hypotheses. (§14.2, plus references throughout)

### Analysis quality

6. **Replaced binary sample-size threshold with graduated confidence labels.** Old rule: "rank lanes only at n≥5." New rule: insufficient (<5) / low scatter-only (5-14) / moderate (15+) / stronger (30+). IQR shown alongside medians at all sample sizes. (§14.4, §11 `v_lane_performance`, §13 interpretation rules)
7. **Suppressed velocity below noise threshold.** `velocity_7d_per_day` displays only when `|delta_7d| >= 10`; below that, "trend not yet measurable." Eliminates precise-looking noise. (§12, §13, §14.1)
8. **Labeled `engagements_total_approx` consistently.** The computed sum is not the same as X's official `engagements` metric. The schema separates `engagements_total` (API only) from `engagements_total_approx` (computed), and the UI always labels the latter. (§10 `post_metric_snapshots`, §12)

### Privacy

9. **Removed inferred sensitive attributes.** No `inferred_low` confidence level for working-parent / home-cook / ICP classification. These columns are only populated when `attribution_method = self_reported`. Better honest gaps than stored guesses about strangers. (§10 `stir_conversion_events`, §10 `stir_testers`, §13, §18)
10. **Simplified `stir_conversion_events` event taxonomy.** Old 12-value enum was fiction at <10 testers. New schema: 4-value `event_category` (`acquisition` / `activation` / `usage` / `feedback`) plus free-text `event_type`. (§10)

### Infrastructure

11. **Specified `st.connection` for DB access.** Streamlit's idiomatic SQL pattern with built-in caching. (§7.1)
12. **Specified `VACUUM INTO` for backups.** `cp` of an open SQLite file can corrupt; `VACUUM INTO` is safe. Added `scripts/backup_db.py` to the implementation checklist. (§7.1, §18, §25 Phase 4)
13. **Anchored `account_snapshots` unique index on `x_user_id` when available.** Handle changes don't break joins. Falls back to `username` until `x_user_id` is known. (§10)

### Honesty

14. **Made App Store attribution gap explicit.** UTM works for getstir.app visits but does not survive to App Store downloads. Default attribution for downloads is `self_reported`. The Funnel view shows this asymmetry rather than hiding it. (§10, §14.5)
15. **Added counterfactual note as required field in weekly review.** Acknowledges that growth has a baseline (platform drift, cohort effects, day-of-week) the tool cannot measure. Export blocked until filled. (§10 `weekly_reviews`, §14.6, §15.5, §22, §24)
16. **Reframed daily reply target as experimental.** Raised default from 5 to 12 with explicit 21-day calibration review. The intent is deliberate adjustment driven by adherence data, not silent drift. (§10 `daily_activity`, §14.1, §14.7, §26)

### Removed

17. Removed the `inferred_low` ICP confidence enum value.
18. Removed scheduled-jobs-by-default assumption (nothing to schedule in manual-MVP).
19. Removed implicit assumption that the X API would be operational on day one.

### Follow-up revision (same day) — personal-tool scope clarification

20. **Removed Tauri and Electron from the architecture comparison.** Desktop packaging is not on the roadmap for a personal single-user tool. (§7.2)
21. **Removed V2.1 "Desktop app" section.** Renumbered V2.2 → V2.1 (Experiment engine), V2.3 → V2.2 (Qualitative signal library), V2.4 → V2.3 (Content strategy assistant). (§21)
22. **Removed "Multi-user support" and "Cloud sync" from V1.1+ deferred list.** These were forward-compat hedges for a productization that isn't happening. (§19)
23. **Added explicit "personal local tool" framing to §0 revision note and §1 thesis.** Removes ambiguity about whether the spec is preparing for distribution.

Practices retained despite personal scope:
- Immutable snapshots with corrections table — Daniel's data history is worth preserving from Daniel's own future edits.
- `VACUUM INTO` backups — data loss is data loss, even on one machine.
- Self-report-only sensitive attributes — testers are still real people whose attributes shouldn't be inferred, even into a private database.
- `x_user_id` stable identifier — Daniel's own handle could change someday; the schema shouldn't break if it does.
- Schema discipline and sample-size warnings — the dashboard's value depends on these regardless of audience.

### Third revision (same day) — Growth Agent addition

24. **Added Growth Agent as a first-class component (§28).** A Claude-powered assistant that drafts posts and replies, finds reply targets, analyzes content, and posts to X with two-step confirmation. Integrated into Today, Next Rep, Content Performance, Weekly Review, and Settings views; given its own dedicated Agent Chat view (§14.8).

25. **Three voice bars (intelligence, wisdom, humility) as the agent's voice constraint.** Each draft scored 0-3 on each bar by the agent itself; recorded in `agent_drafts.voice_self_score`. Agent refuses to ship drafts that fail any bar at score <2.

26. **System prompt codifies psychology-of-engagement principles** (specificity, hook-tension-(un)resolution, identification over admiration, pattern interrupt, question-leading, openness over certainty, interestingness gradient, earned vulnerability) and a hard anti-pattern list (engagement bait, "most people don't realize" condescension, performed vulnerability, AI-generated-content tells). Prompt lives in `config/agent_system_prompt.md` so it's edited as text, not code.

27. **Added three new tables (§10):** `agent_drafts`, `agent_messages`, `agent_target_accounts`. Modified `posts` to include `agent_draft_id` and added `agent_assisted` to the `posted_via` enum.

28. **Specified 10 tool functions for the agent (§28.4):** `draft_post`, `draft_reply`, `find_reply_targets`, `analyze_post`, `summarize_winners`, `get_open_hypotheses`, `get_lane_gaps`, `save_draft`, `revise_draft`, `submit_post`. Read-only and write tools clearly distinguished; `submit_post` requires a one-time UI-issued `confirmation_token`. *(Note: tool count later expanded to 11 in the follow-up revision — see §28.4 for current tool catalog.)*

29. **Two-step confirmation flow (§28.10) is non-negotiable.** Step 1: review modal with edit-in-place. Step 2: final modal that re-displays the exact text being posted. Token is single-use, 5-minute TTL. Applies in both manual mode (clipboard + X intent URL) and V1.1+ API mode (direct X API post). *(Note: TTL later tightened to 60 seconds — see §28.10 publish flow and item 43.)*

30. **Least-privilege data access for the agent (§28.2 rules #10-11 and #13).** Agent can read posts, metrics, classifications, experiments, target accounts, lane performance, daily activity. Agent cannot read tester PII (`stir_testers`) or qualitative tester feedback (`stir_conversion_events.qualitative_feedback`). The testers' words are not the agent's to weaponize.

31. **Cost ceiling and observability (§28.6 Cost management).** Daily ceiling default $5.00; per-session budget default 50k tokens. Token usage stored per message in `agent_messages`. Settings view shows aggregates. *(Note: monthly cap of $25 is the current default per the follow-up revision — see §28.6.)*

32. **Renumbered the prior changelog from §28 to §29.** Only one section number changed; all internal references in earlier sections to §1-§27 remain valid.

33. **Renumbered §8 (eight) views in MVP** (formerly seven): Today, Next Rep, Progress, Content Performance, Funnel, Weekly Review, Settings, Agent Chat. (§14, §19)

34. **Added Phase 5.5 to the implementation checklist (§25)** specifically for the agent: tables + module structure (`app/agent/client.py`, `tools.py`, `session.py`, `confirmation.py`, `cost.py`), tool function implementations, Agent Chat view, integration buttons across other views, Settings config, cost tracking display, and refuse-and-explain handling.

35. **Added agent failure-mode rows to §22 edge cases:** API key missing/invalid, API timeout/5xx, agent self-flags failing voice bars, daily cost ceiling, per-session token budget, double-click confirm, draft edits between propose and confirm, manual-mode agent trying to call X API, session_id collision, Streamlit rerun during streaming, engagement-bait requests, deleted target posts.

36. **MVP acceptance criteria (§26) extended:** the loop now includes agent invocation, voice self-score visible, two-step confirmation, edited drafts posting the edit. New failure modes for MVP: agent posting without confirmation, agent shipping voice-bar failures, agent reaching tester PII, hidden tool calls.

### Follow-up revision (same day) — Agent publishing capability + engagement psychology

> **Editorial note (added during structural fix on the same day):** items below — through item 66 in the "Review-driven fixes" subsection — reference §15.6 because that's where the Growth Agent spec lived when those revisions landed. §15.6 was later promoted to top-level §28 (see item 67). The §15.6 references are preserved as historical record of which section was touched at the time. **To read the current Growth Agent spec, see §28.**

37. **Added direct publishing capability** with strict per-action confirmation. New tools #10 (`publish_post_to_x`) and #11 (`publish_reply_to_x`). Schema additions to `posts` (`published_to_x_at`, `publish_method`, `published_via_agent_message_id`, `publish_attempt_count`, `publish_last_error`) and `agent_messages` (`resulted_in_published_post_id`). (§5, §10.2, §14.8, §15.6, §18, §19, §22, §25, §26)

38. **Expanded system prompt from 6 to 8 sections** including new Section 1 (niche context: Stir + AI biomed Master's + neuro-oncology long arc), new Section 2 (intelligence/wisdom/humility tone directive), and new Section 4 (engagement psychology principles). (§15.6)

39. **Added engagement psychology principles**: hooks, structure, substance, emotion/resonance, ethical engagement triggers (reciprocity, social proof, scarcity, authority — all real-only), explicit dark-pattern prohibitions, X-format guidance. (§15.6 Section 4)

40. **Added intelligence/wisdom/humility tone directive** as Section 2 of the system prompt. The agent revises drafts that fail any of the three and refuses after three failed attempts. (§28.3 Section 2)

41. **Updated §5 non-goal #1** from "never auto-reply" to "never post without per-action confirmation." The auto-batch prohibition stands; the per-post confirmation flow is the controlled exception. (§5)

42. **Added X API OAuth credentials** to settings and `.env` requirements. Rate limits enforced client-side (10/hour, 50/day defaults). (§10.2, §18, §22)

43. **Added confirmation token mechanism**: single-use UUIDs generated by UI click, 60-second expiry, validated at tool layer. Cannot be generated by the agent. (§15.6 rules 10-11, §22 edge cases)

### Review-driven fixes (same day) — addressing /review-2 findings

Multi-agent review (1 debugger + 1 code-auditor, both Opus 4.7) on the publish-flow + engagement-psychology extension surfaced 9 🔴 critical contradictions/security gaps, 13 🟡 warnings, and 8 🔵 suggestions. All addressed below.

44. **State transition on publish — `posts.manual_confirmation_status = draft → confirmed` on successful publish.** (Critical from DB1) The publish tools were leaving rows in `draft` after going live, enabling double-publish and miscounting in `v_lane_performance`. Now part of the atomic transaction. (§15.6 rule #10, §15.6 publish flow, §10.2 posts.publish_method enum revision, §22 double-publish edge case)

45. **Raw `confirmation_token` redaction in `agent_tool_calls.arguments_json`.** (Critical — corroborated by both agents) Rule #11 required logging only the token ID, not the raw token; the audit-log path was contradicting this by default. Added new `redacted_arguments` column on `agent_tool_calls`, mandated redaction in the tool dispatcher, and added the `publish_confirmation_tokens.id` linkage. (§10.2 agent_tool_calls, §10.2 publish_confirmation_tokens, §28.2 rule #11)

46. **Removed `manual` value from `posts.publish_method` enum.** (Critical — corroborated; severity merged up) Conflicted with `posted_via = manual` semantics and produced ambiguous backfill rules. Enum is now `agent_confirmed | null` only. (§10.2 posts table)

47. **`x_posting_confirmation_required` reclassified as a compile-time constant, NOT a settings row.** (Critical from DB1) Previously editable via `UPDATE settings`, defeating its "non-disableable" claim. Now declared as `CONFIRMATION_REQUIRED = True` in `app/agent/confirmation.py` and explicitly NOT in the settings table. (§10.2 settings block split into CONSTANTS vs EDITABLE)

48. **Confirmation token storage moved to new `publish_confirmation_tokens` table with SHA-256 hashing.** (Critical from CA1) Previous spec was ambiguous about server-side vs client-side generation in Streamlit's same-process architecture; the agent could potentially have read tokens from `st.session_state`. Tokens now live in a DB table that's explicitly excluded from the agent's tool registry. Raw tokens exist only in the click-handler's local stack frame. (§10.2 publish_confirmation_tokens, §15.6 rule #10 six-check validation chain, §15.6 publish flow step 5)

49. **Atomic publish-then-DB-write transaction with crash recovery.** (Critical from CA1) Previous flow was best-effort about partial failure; X-succeeds-then-DB-fails left no trace. Now wrapped in a single transaction with bounded internal retry (`x_posting_publish_retry_attempts_per_token`); a boot-time reconciliation routine queries the X API to detect orphan posts. (§28.10 publish flow steps 6 and 8, §22 network-failure expanded into three sub-cases)

50. **IWH revision counter moved to orchestrator ownership (`app/agent/session.py` + `agent_drafts.iwh_attempt_index`).** (Critical from CA1) Previous spec had the agent self-track its own failure count, which it could game by adjusting its self-scores. Counter now lives outside the agent's context window; agent emits structured `<iwh_self_score>` tags, orchestrator decides. (§28.2 rule #13, §10.2 agent_drafts new columns)

51. **Dark-pattern lint pass added as enforcement layer for §28.2 rule #12.** (Critical from CA1) Section 4's "dark patterns are forbidden" prohibition was previously self-enforced by the same model that wrote the draft. Now a separate small-model (Haiku) invocation in `app/agent/lint.py` runs on every draft before save_draft; failed lint counts as a failed IWH revision. (§28.2 rule #12, §15.6 Section 4 reordered to lead with prohibition, §25 Phase 5.5 new checklist items)

52. **CSV export carve-out for new sensitive `posts` columns.** (Critical from CA1) Default `posts` export was leaking `publish_last_error` (X API diagnostic strings) and `published_via_agent_message_id` (joins to agent chat content). Added explicit column allowlist + opt-in for debug; added separate "Export agent audit" action for agent_messages / agent_tool_calls / agent_drafts. (§16 export, §18 privacy items 18-19)

53. **Rate-limit composition spelled out: sliding windows on both hour and day, with bounded internal retry.** (Warning) Previous spec didn't say whether the hour-counter was sliding or fixed, and didn't define the retry-vs-token-consumption interaction. Both are now explicit. (§10.2 settings, §15.6 publish flow, §22 rate-limit edge case)

54. **Voice samples injection target fixed: "Section 3" → "Voice samples section" (currently Section 5).** (Warning — corroborated by both agents) The 6-to-8 system prompt restructure left the voice-samples workflow pointing at the wrong section. Replaced numeric reference with the new section-number registry (§15.6 top) so future restructures don't reintroduce the drift. (§15.6 voice samples workflow, §15.6 section registry)

55. **System prompt build step documented: rules 1-13 are spliced from spec into Section 3 at build time, with a pre-commit drift check.** (Warning) Previously the placeholder text "Non-negotiable rules 1-13 listed verbatim from §15.6" was implicit; now there's an explicit assembly script (`app/agent/prompt_builder.py`) and a count-mismatch check. (§15.6 system prompt structure)

56. **`publish_attempt_count` and `publish_last_error` reset semantics on successful publish.** (Warning from DB1) Previous spec didn't say whether stale failures linger after a later success. Now: `publish_last_error` clears on success; `publish_attempt_count` increments (records all attempts including the successful one). (§10.2 posts table, §15.6 publish flow atomic transaction)

57. **Rate-limit layer ownership resolved: `app/x_client.py` owns the rate-limit state; `_internal_tools.py` calls atomically.** (Warning from CA1) Previous spec had both layers gating; now there's a single source of truth and the atomic `check_and_reserve_rate_capacity()` call. (§25 Phase 5.5 checklist item)

58. **FK ON DELETE behaviors defined for `posts.published_via_agent_message_id` and `agent_messages.resulted_in_published_post_id`.** (Warning from CA1) Both set to `ON DELETE SET NULL`. Also required `PRAGMA foreign_keys = ON` in §7.1 DB connection setup, otherwise SQLite silently disables FK enforcement. (§7.1, §10.2 posts table, §10.2 agent_messages table)

59. **Magic numbers centralized in §10.2 settings.** (Warning from CA1) `iwh_max_revision_attempts`, `iwh_self_score_minimum`, `x_short_post_target_chars`, `x_post_max_chars`, `x_posting_publish_retry_attempts_per_token` all moved to settings; system prompt references them by name. Reduces drift when defaults are tuned. (§10.2 settings)

60. **Single-use retry semantics: one confirmation_token = one atomic publish operation, may retry internally up to N times on transient errors.** (Warning from CA1) Resolves the contradiction between "no retry without new confirmation" and "wait + retry once with backoff" in the rate-limit edge case. (§15.6 rule #10, §15.6 publish flow, §22 rate-limit edge case)

61. **Publish tools removed from the agent-facing tool registry — `AGENT_TOOLS` excludes #10/#11.** (Warning from CA1) The agent has no schema slot to attempt a publish call, removing the design-time bypass invitation. Internal-only callables in `app/agent/_internal_tools.py` reachable only by the Streamlit click-handler. (§15.6 tool catalog, §25 Phase 5.5)

62. **New §22 edge case rows: draft deleted mid-flow, `x_posting_enabled` toggled mid-flow, concurrent status change, double-publish rejection, dark-pattern lint flag, reconciliation banner ack, publish_confirmation_tokens VACUUM cleanup.** (Warning from CA1, plus three additions surfaced during fix scoping)

63. **Section 1 of system prompt compressed to load-bearing facts.** (Suggestion from CA1) Voice details now carried by the dynamic Voice samples section, not duplicated in Section 1 identity prose. Reduces system-prompt token cost on every call.

64. **Backfill migration explicitly defined for the 5 new `posts` columns.** (Suggestion from CA1) Existing rows get `publish_attempt_count = 0`, other publish_* columns NULL. (§25 Phase 5.5 migration checklist)

65. **"Open decisions" reframed as "Defaults to validate after first week of use."** (Suggestion from CA1) The list was concrete defaults, not blocking decisions.

66. **Section-number registry table at top of §15.6.** (Suggestion from CA1) Single source of truth for system-prompt section structure; other parts of the spec reference sections by purpose-name, not integer.

### Structural fix (same day) — §15.6 → §28 promotion

67. **Promoted §15.6 Growth Agent workflows to top-level §28 Growth Agent.** Structural fix; source block moved verbatim with three transforms: heading-level promotions (h3→h2 for the section heading, h4→h3 for subsection headings), subsection numbering added (§28.1 through §28.10 using §15.6's natural content order), and 4 internal `§15.6` self-references inside the System-prompt-structure subsection rewritten to `§28.2`. Additionally, 6 existing in-text references to `§28.X` subsections (from §9 user stories #28 and #30, §14.8 chat view, and §29 prior-revision items 28, 29, 30, 31) were repointed to the correct new subsections — `§28.6` → `§28.7`, `§28.7` → `§28.10`, `§28.8` → `§28.2`/`§28.6` depending on what each historical item meant. Two prose drifts in the Third-revision items were preserved with inline notes rather than rewritten: item 28's "10 tool functions" (current spec has 11 — see §28.4) and item 29's "5-minute TTL" (current spec has 60-second — see §28.10 and item 43). §15 now ends at §15.5 (Weekly review workflow); §15.6 no longer exists. The 36+ existing `§15.6` references in items 37-66 are preserved as historical record per the editorial note above. (§28 entire, §15 trailing, §29 entire, §0 revision note, §9 user stories, §14.8 chat view)

Defaults to validate after first week of use:
- **Rate limit defaults** (10/hour, 50/day). Conservative; adjust in Settings.
- **Token expiry of 60 seconds.** Short enough to prevent stale confirmations, long enough for a thoughtful publish flow.
- **`x_posting_enabled = false` default.** Opt-in in Settings; means the publish capability is dormant until Daniel explicitly enables it after credentials are configured.
- **`iwh_max_revision_attempts = 3`.** The agent refuses after three attempts. Configurable in Settings if too strict in practice.
- **`iwh_self_score_minimum = 2`.** Per-quality threshold (0-3 scale). Tuneable in Settings.
- **`x_posting_publish_retry_attempts_per_token = 2`.** Bounded internal retry on transient X API errors. Increase if X is flaky during a deploy window.
- **Default-export column allowlist for `posts`.** Currently excludes `publish_last_error` and `published_via_agent_message_id`; opt-in toggle in Settings → Export. Validate that the allowlist matches what Daniel actually wants exported after first-week of use.

Notes on this revision:
- Direct posting via X API is deferred to V1.1; MVP uses clipboard handoff. If you want direct posting in MVP, that's a one-flag change (`data_collection_mode = api` + X API write credentials) but you have to commit to the X API tier costs earlier than the rest of the spec assumes.
- The `agent_target_accounts` table is empty at MVP launch — you populate it with the accounts you actually want to engage with. Without it, `find_reply_targets` falls back to suggesting you populate it.
- The voice self-score is the agent's own assessment, not a hard gate at submit time. The hard gate is your confirmation. The self-score is signal for you to spot when the agent is shipping work it knows is mediocre.

### Reply Target Discovery addition (same day) — §29 + §29→§30 renumber

68. **Added §29 Reply Target Discovery** as a first-class subsystem. Reframes replies as distribution into existing attention pools, not generic daily reps. Includes:

    * Four MVP scoring dimensions (Relevance, Engagement surface, Saturation, Reply opportunity) on a 0–3 scale, with a deterministic `recommended_action` resolver. No composite "viability" score — same epistemological discipline as §11's graduated confidence labels.
    * Engagement-surface thresholds expressed as `max(floor_likes, pct_of_author_followers)` so the score doesn't break when Daniel's follower count grows or when a target's author has very different reach. (§29.4)
    * Reply intent as a fourth orthogonal axis (`growth` / `icp_discovery` / `relationship` / `product_adjacent` / `thought_leadership`), distinct from pillar/audience/CTA. (§29.5)
    * New tables `reply_targets` (MVP) and `reply_target_snapshots` (V1.1+); two new columns on `posts` (`in_reply_to_reply_target_id`, `reply_intent`); one new column on `reply_sessions`; eight new `settings` rows. (§29.6)
    * Reply Target Queue as the **ninth** MVP view; §19's view list updated accordingly. §14.2 Next Rep absorbs the top-N candidates as a window onto the queue, no duplicated state. (§29.7, §14.2)
    * Agent integration via expansion of §28.4 tools #6 (`score_reply_candidates`) and #7 (`record_reply_target`); **no new agent tools**; reply posting stays on the click-handler path same as §28.4 #10/#11. (§29.8)
    * V1.1+ thread-classifier lint pass (`ragebait` / `meme_with_no_serious_reply_path` / `low_quality_reply_thread` / `hijacking_required_to_mention_stir`) mirroring §28.2 rule #12's dark-pattern lint. Blocks at ragebait or hijacking; overrideable with logged reason. (§29.10)
    * Phase 5.6 implementation checklist in §25; Phase 5.7 for the V1.1 metrics + lint additions.
    * Edge cases in §29.11 for target deletion, candidate expiry, lint override, threshold misfire, draft orphaning, duplicate-URL handling, and CSV import.

69. **Deliberately rejected from the original Reply-Target-Finder draft:**

    * Composite "Viability 82/100" score with hidden weights. Same epistemological violation that §11 `v_lane_performance` exists to push back on — a weighted sum across heterogeneous 0–3 dimensions is precision theater at <100-follower scale.
    * Auto-attribution of Stir downloads or follower growth to specific replies. §14.5 App Store gap and §13 hard rule on causal attribution apply unchanged.
    * 60/30/10 reply-mix rule (unjustified; conflicted with §14.1's 12-replies/day target). Replaced with one configurable `reply_high_engagement_mix_pct` (default 0.5), calibrated at day 21 like the rest of the reply targets.
    * Absolute engagement thresholds ("low: <10 likes"). Replaced with the relative formula in §29.4.
    * API-first reply posting framed as the default with manual as a workaround. Manual is the MVP default per §19, V1.2 publish-flow contract per §28.10 applies.

70. **Renumbered the prior Changelog from §29 to §30.** Two non-changelog references updated (§0 revision note, item 67 historical narrative left as-is per the same convention as item 32's "§28 → §29" renumber). The 29 historical `§29` references inside the changelog itself (in items 24–67 referring to "this changelog") are preserved as historical record per the editorial-note convention established in item 36.

71. **§22 master edge-case table gains a one-line pointer** at the bottom to §29.11 so future readers find the reply-target-discovery cases without searching.

72. **§0 revision note adds a fourth paragraph** introducing §29 and noting the renumber to §30.

### Drafting Intelligence Pack addition (2026-05-22) — §28.11 through §28.15

Cross-pollination from a comparison with CreatorOS (Daniel's prior X-growth tool, fully built). Five features identified as philosophically aligned with the XGrowth thesis (single-user instrument, deterministic-first, never hard-gate without cause) were added as Phase 5.8 Should-ship. No existing contracts changed.

73. **Pre-publish heuristic scorer (§28.11).** Deterministic 9-dimension 0-3 scorer (clarity, hook, specificity, length, format, topic, reply substance, CTA, voice fit) producing a single `composite_label` chip (`weak | viable | strong`) above each agent draft. Surfaced in Today, Next Rep, Agent Chat, and historically in Content Performance for calibration. Never blocks publish — informational counterpart to the §28.2 #12 dark-pattern lint. (§10 `prepublish_scores`, §28.11, §25 Phase 5.8)
74. **Generated voice profile (§28.12).** Structural read of Daniel's actual writing (cadence, hooks, vocabulary, stop phrases, self-description) synthesized by a Haiku call from the last N days of posts. Complements `voice_samples` (raw exemplars) — both are spliced into the system prompt at build time. Manual regeneration only, no cron. Atomic activation. (§10 `voice_profiles`, §28.5, §28.12, §25 Phase 5.8)
75. **Repetition guard via embedding similarity (§28.13).** Embedding cosine scan of every new agent draft against the last N days of `posts`; surfaces `near_duplicate` (≥0.92) and `close_echo` (≥0.78) as a yellow banner with the nearest post's excerpt + "intentional / let me rewrite" affordance. Voyage AI `voyage-3-lite` default; swappable adapter at `app/agent/embeddings.py`. Soft check — never blocks. (§10 `post_embeddings`, §28.13, §25 Phase 5.8)
76. **Confidence labels on agent outputs (§28.14).** Added §28.2 rule #14 requiring `<confidence>fact | inference | speculation | mixed</confidence>` tags on every analytical claim, parsed by the orchestrator and persisted on `agent_drafts.confidence_label` / `agent_messages.confidence_label`. Untagged analytical claims (detected via `app/agent/confidence_patterns.py`) trigger an IWH humility-failure increment. Weekly Review export is blocked when a section is labeled `speculation` until acknowledged. (§28.2 rule #14, §10 `agent_drafts.confidence_label`, §28.14, §25 Phase 5.8)
77. **Approval payload hash — user-visible enforcement (§28.15).** Extension to §28.10's existing `draft_text_hash_at_issue` mechanism: the confirmation modal now re-hashes on each keystroke (debounced), surfaces a yellow "you've edited" banner when hashes differ, disables Publish briefly to absorb stray pastes, automatically invalidates prior tokens when a second modal mints one, and audit-logs pre/post-edit hash diffs. Turns the previously-silent hash-mismatch failure into a smooth experience without weakening the security mechanism. (§28.10, §28.15, §25 Phase 5.8)

Deliberately rejected from the CreatorOS comparison:

- Blogs / long-form authoring — explicit §0 + §19 scope (X-only).
- Multi-provider AI — §28 commits to Claude.
- OAuth scope escalation, Chrome extension auth, personal save tokens — single-user local tool (§7.1).
- pgvector / HNSW — overengineered for low-thousands lifetime post volume; numpy cosine over `post_embeddings` BLOBs is sufficient (§28.13).
- Compare-and-swap on draft status + idempotency keys for publish jobs — meaningful in multi-instance deployments; XGrowth is single-instance and §28.10's two-step confirm + W12 `UNIQUE(post_id)` cover the realistic risk.
- Campaigns / experiments tables — useful but expand schema scope; reconsider in V1.2 if reply-strategy iteration in §29 warrants the formal structure.
- AI Coach as a separate view — Weekly Review (§14.6) plus the new §28.14 confidence-label discipline cover this without a dedicated surface.

[1]: https://docs.x.com/x-api/fundamentals/data-dictionary "Data Dictionary - X"
[2]: https://docs.x.com/x-api/getting-started/about-x-api "About the X API - X"
[3]: https://docs.x.com/x-api/posts/create-post "Create or Edit Post - X"
[4]: https://docs.x.com/developer-guidelines "Developer Guidelines - X"
[5]: https://docs.x.com/tools/xurl "xurl - X"
[6]: https://docs.x.com/x-api/getting-started/pricing "Pricing - X"
[7]: https://sqlite.org/about.html "About SQLite"
[8]: https://docs.streamlit.io/develop/concepts/architecture/run-your-app?utm_source=chatgpt.com "Run your Streamlit app"
[9]: https://v2.tauri.app/start/ "What is Tauri? | Tauri"
[10]: https://electronjs.org/docs/latest "Introduction | Electron"
[11]: https://nextjs.org/docs/app/getting-started/installation "Getting Started: Installation | Next.js"
