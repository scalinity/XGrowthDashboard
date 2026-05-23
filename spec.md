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
>
> **Scope expansion (2026-05-22) — long-form blogs as a Phase 6 first-class surface.** After Phases 5.8 through 5.11 closed the consolidation gap with Daniel's prior creator tool (CreatorOS), one capability remained out of XGrowth: long-form blog authoring. Phase 6 (§28.31 through §28.34, full checklist in §25 Phase 6) adds blogs as a first-class production surface — `blogs` / `blog_versions` / `blog_exports` / `blog_to_post_links` tables, two new views (§14.14 Blogs index, §14.15 Blog Editor), agent tools for blog ideation / outlining / drafting / editing, exports (Markdown / HTML / JSON / MDX), and bidirectional repurposing (X thread ↔ blog, with deterministic plagiarism floor). **The single-user local-tool scope clarification in §7.1 is unchanged** — blogs are produced locally, exported to disk, published externally on Daniel's blog platform (not by this app). This is NOT a scope expansion toward multi-user / cloud / packaging; it is a *content-production-surface* expansion within the same single-user-local thesis. **Project name consideration deferred to Daniel.** "X Growth Dashboard" remains accurate as the name describes the primary distribution thesis; blogs serve that thesis via repurposing. A rename to e.g. "Distribution Dashboard" or "Personal Distribution OS" is a one-line spec edit Daniel can make at his discretion; the implementation does not depend on it. See §30 items 93–97 for the full delta.
>
> **API + Grok consolidation (2026-05-22, same day) — V1.1 reads, V1.2 writes, and V1.2 Grok all promoted to comprehensive v1 scope as Phases 7, 8, and 9.** After Phase 6 (blogs) shipped, three deferred capabilities still sat on the V1.x side of the §29.1 / §28.10 / §10 boundaries: X API read integration (`reply_target_snapshots`, metrics-refresh jobs, velocity + timing scoring, thread-classifier lint), X API write integration (the §28.10 publish-flow contract executes against the real X API instead of the manual-clipboard-only branch), and Grok firehose discovery (`reply_targets.discovered_via='grok_semantic'`). Per the comprehensive-default rule these are now in v1: **Phase 7 (X API reads, migration 018)**, **Phase 8 (X API writes, migration 019)**, **Phase 9 (Grok integration, migration 020)**. Grok defaults to ENABLED (`grok_api_enabled = TRUE`); X API writes default to ENABLED (`publish_via_api_enabled = TRUE`); `data_collection_mode` flips default to `'api'` with Phase 7. **Manual workflows remain inviolable as Settings-selectable fallbacks forever** — every API path has a separately-tested manual-equivalent path. The `audience_quality_score` 7th resolver dimension and `v_content_type_x_pillar_performance` cross-pivot stay deferred (density / data-source reasons, not API). See §30 items 98–100 for the full delta, §29.12 for the Grok integration section, and §25 Phases 7/8/9 for the per-phase checklists.

---

## 1. Product thesis

The dashboard is not a social media "analytics dashboard" in the generic sense. It is a **behavior + trend + validation system** — a personal local tool, single user, never distributed.

The core job:

> Make it obvious whether Daniel is doing the daily distribution reps, whether those reps are moving X growth over time, and whether any of that growth is converting into real Stir validation.

**Phase 6 expansion (2026-05-22) clarifies the thesis's content-production boundary:**

> XGrowth is the *distribution-and-validation system* for Daniel's growth. Phase 6 adds a *content-production surface* (blogs) that serves the distribution system via repurposing. Blogs land here because (a) keeping ideation, voice, niche, and personality lore unified across short-form X posts AND long-form essays gives the agent a single coherent identity to draft from; (b) bidirectional repurposing (X thread ↔ blog) belongs in the same app as the X workflow; (c) Daniel's prior blog tool (CreatorOS) is being retired into XGrowth specifically to eliminate the cross-app context-switch.
>
> What Phase 6 does NOT change: this is still a single-user local tool. Blogs are written locally, exported to disk, and published externally on Daniel's blog platform — the app itself never publishes a blog anywhere. Multi-user, cloud sync, blog-platform integrations are not on the roadmap; if any future suggestion implies them, refuse and point back to §7.1.

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
* **`xurl` integration ships in Phase 7 (X API reads)** once the manual loop has proven the dashboard's value. See §25 Phase 7 + §29.1 for the full read-side scope; defaults to enabled (`data_collection_mode = 'api'`) after migration 018.
* **Direct X API write integration ships in Phase 8** under §28.10's existing publish-flow contract — replaces the stubbed manual-clipboard-only branch with a real `POST /2/tweets` call alongside the manual fallback, gated by `publish_via_api_enabled` (default TRUE).
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
    raw_api/                 # empty until Phase 7
    exports/
    weekly_reports/
  scripts/
    backup_db.py             # VACUUM INTO with date suffix
    export_weekly_report.py
    collect_account_snapshot.py   # stub until Phase 7
    collect_recent_posts.py       # stub until Phase 7
    refresh_post_metrics.py       # stub until Phase 7
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
                         │  xurl / X API (Phase 7+) │
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

Preserves raw responses for auditability. Empty until Phase 7 (xurl/X API reads); see §29.12 for the parallel `grok_api_responses` audit table introduced by Phase 9.

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
| `content_type`               | enum                 | `value`, `growth`, `personality`, `proof`, `unspecified`. ORTHOGONAL to pillar/audience/CTA — pillar is *topic* (stir/build/self), content_type is *purpose* per §28.16. Default `unspecified`. Existing rows backfill to `unspecified` (never retro-classified). Required (non-`unspecified`) on new agent drafts via the orchestrator. |

Indexes:

```text
index(created_date)
index(type)
index(conversation_id)
index(utm_campaign)
index(content_type)
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
| `confidence_label` | enum nullable     | `fact \| inference \| speculation \| mixed`. Dominant label parsed by §28.14 orchestrator from `<confidence>` tags in this message. NULL for non-analytical messages and `user`/`system` rows. Added in Phase 5.8 migration. |
| `evidence_citations_json` | text nullable | JSON array of allowlisted `(record_type, record_id)` citations per §28.23 Coach citation discipline. Format: `[{"record_type": "post"|"v_lane_performance"|..., "record_id": int|str, "excerpt": str}]`. Citations that the agent emitted but failed allowlist validation are stripped before persistence; the count of stripped citations is logged to `agent_tool_calls.notes` of the message's parent tool call. NULL for messages that don't carry citations. |
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
| `content_type`      | enum             | `value`, `growth`, `personality`, `proof`. Required on new agent drafts per §28.16 (orchestrator rejects `unspecified`). Mirrors `posts.content_type` and is copied through on save. |
| `reply_quality_lint_passed` | boolean nullable | Per-draft result of the §28.18 reply-quality lint (forced / AI-tasting / selfishly self-promoting detector). NULL when the lint didn't run (e.g. `draft_kind != reply` or `reply_quality_lint_enabled = false`). false counts as a failed IWH revision through the same enforcement path as `dark_pattern_lint_passed`. |

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

### `personality_lore`

Registry of recurring jokes, running bits, and personal motifs the agent should pick up on when drafting `content_type = personality` posts. Helps the agent build on existing threads of voice instead of inventing a new bit every time. See §28.21.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `theme` | text | short name — e.g. "water bottle in frame", "kitchen-scanner fail", "neuro-oncology long arc" |
| `description` | text | one-paragraph explanation of the bit, written by Daniel |
| `example_posts_json` | text nullable | JSON array of `post_id` values where this lore was previously invoked; the agent reads excerpts of these when drafting |
| `invocation_count` | integer default 0 | how many times this lore has been called into a draft; informational, used by §28.21 to surface "over-relied on" warnings |
| `last_invoked_at_utc` | datetime nullable | when this lore was last spliced into a draft |
| `is_active` | boolean | included in the system prompt rotation |
| `priority` | integer | display order in the system prompt splice; lower = earlier |
| `added_at_utc` | datetime | |

Indexes:

```text
index(is_active, priority) where is_active = true
```

Notes:
- Lore is hand-curated by Daniel via Settings → Growth Agent → Personality lore. Never auto-generated.
- The agent CANNOT write to this table — no tool surface exposes it. Daniel-only edit.
- A lore row with `invocation_count > personality_lore_overuse_threshold` (default 8) AND `last_invoked_at_utc > now() - 30 days` triggers a yellow "you're leaning hard on this bit" banner in the lore panel; doesn't disable the lore, just informs.

---

### `brain_dumps`

Capture-first interface (distinct from conversational Agent Chat). Daniel pastes raw thinking; the agent processes into clarifying questions + structured candidate drafts. See §28.22.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `created_at_utc` | datetime | |
| `raw_text` | text | the exact text Daniel pasted; immutable after insert |
| `session_id` | text nullable | groups related dumps from one work session |
| `status` | enum | `unprocessed`, `processing`, `processed`, `failed` |
| `processed_at_utc` | datetime nullable | |
| `clarifying_questions_json` | text nullable | JSON array of strings — questions the agent wants answered before drafting |
| `candidate_drafts_json` | text nullable | JSON array of `{text, content_type, pillar, audience, cta, rationale}` — drafts the agent proposes from the dump |
| `model_used` | text nullable | e.g. `claude-opus-4-7` |
| `tokens_used` | integer nullable | |
| `notes` | text nullable | Daniel's own annotations after processing |

Indexes:

```text
index(status, created_at_utc desc)
index(session_id) where session_id is not null
```

Notes:
- `raw_text` is NEVER edited after insert. Daniel's annotations go in `notes`. If Daniel wants to refine the input, he creates a new row.
- Failed processing (model returns bad JSON, Anthropic 5xx after bounded retry) sets `status = failed`; row is preserved; Daniel can re-run via the view's "Retry" button (creates a new processing attempt on the SAME row).
- Drafts in `candidate_drafts_json` are NOT auto-saved to `agent_drafts`. Daniel reviews and explicitly promotes via the Brain Dump view → "Send to drafts" button, which calls `_save_draft_post` with the candidate's metadata.

---

### `account_research_reports`

Strategic analysis of a target X account — posting patterns, positioning, reply-strategy entry points. Manual-paste workflow for MVP; V1.1+ adds X API auto-pull. See §28.24.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `target_handle` | text | e.g. `@some_user` (without the @ acceptable; normalize on insert) |
| `target_url` | text nullable | full profile URL |
| `target_display_name` | text nullable | |
| `target_bio_snapshot` | text nullable | bio text at research time; manually pasted |
| `target_recent_posts_text` | text nullable | concatenated recent post text Daniel pasted (one post per `---` separator); immutable after insert |
| `created_at_utc` | datetime | |
| `analysis_json` | text | JSON. Schema: `{"posting_patterns": {"cadence": str, "topics": [str], "common_hooks": [str]}, "positioning": {"primary_audience": str, "value_proposition": str, "voice_markers": [str]}, "reply_strategy": {"best_entry_topics": [str], "tone_to_match": str, "what_to_avoid": [str]}, "niche_alignment_with_daniel": {"overlap_score": 0-3, "rationale": str}}` |
| `model_used` | text | |
| `tokens_used` | integer | |
| `session_id` | text nullable | groups research from same exploration session |
| `linked_reply_target_id` | int nullable | FK to `reply_targets.id` if this research produced a queued reply target; ON DELETE SET NULL |
| `notes` | text nullable | Daniel's annotations |

Indexes:

```text
index(target_handle, created_at_utc desc)
unique(target_handle, created_at_utc)
```

Notes:
- A handle can have multiple reports over time — each is a point-in-time snapshot. Daniel can compare consecutive reports for the same handle to see how an account's positioning has shifted.
- `target_recent_posts_text` is the only external content the analysis sees — wrapped in `--- BEGIN_UNTRUSTED_DATA ... ---` markers per the §28.2 prompt-injection-defense convention.
- §28.20 replier-pool answers *who's worth replying to within this thread*. Account research answers *should I be in this account's orbit at all, and how*. Different questions, complementary tables.

---

### `profile_audits`

Quarterly (or on-demand) comprehensive AI review of Daniel's X profile — bio + pinned post + recent posts + voice profile + niche definition — read as a unified surface. See §28.25.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `audited_at_utc` | datetime | |
| `bio_snapshot` | text | bio at audit time, manually entered |
| `pinned_post_id` | int nullable | FK to `posts.id` if the pinned post is tracked; else `pinned_post_text` carries it |
| `pinned_post_text` | text nullable | text of pinned post at audit time; ground truth even if `posts` row drifts later |
| `recent_posts_window_days` | integer | how many days of recent posts fed the audit (default `profile_audit_recent_posts_window_days = 30`) |
| `recent_post_ids_json` | text | JSON array of `post_id` values that fed the audit |
| `active_voice_profile_id` | int nullable | FK to `voice_profiles.id` at audit time |
| `niche_problem_snapshot` | text nullable | `niche_problem` setting at audit time |
| `niche_person_snapshot` | text nullable | `niche_person` setting at audit time |
| `audit_json` | text | JSON. Schema: `{"overall_consistency_score": 0-3, "bio_alignment": {"score": 0-3, "gaps": [str], "suggestions": [str]}, "pinned_post_alignment": {"score": 0-3, "gaps": [str], "suggestions": [str]}, "recent_posts_themes": [str], "voice_consistency_with_profile": {"score": 0-3, "drift_observations": [str]}, "niche_coherence": {"score": 0-3, "overall_assessment": str}, "top_three_actions": [str]}` |
| `model_used` | text | |
| `tokens_used` | integer | |
| `superseded_by_audit_id` | int nullable | back-reference once a later audit is run |
| `daniel_notes` | text nullable | Daniel's post-audit notes — what he's acting on, what he's deferring |

Indexes:

```text
index(audited_at_utc desc)
```

Notes:
- No `is_active` flag. The "current" audit is implicitly the most recent row. Audits are append-only history.
- Cadence is Daniel-decided: Settings → Growth Agent → Profile Audit panel surfaces "last audit was N days ago"; encourages quarterly but doesn't nag.
- The audit reads `voice_profiles.is_active = true` row + settings + `recent_post_ids_json` — never touches `stir_testers` or `stir_conversion_events.qualitative_feedback`.

---

### `campaigns`

Multi-week themed pushes. A campaign is a hypothesis + a date range + a set of planned items. Distinct from `experiments` (which is hypothesis-only, no item planning) and from `weekly_reviews` (which is retrospective, not prospective). See §28.26.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `name` | text | short label — e.g. "founder-zero push", "first 5 downloads week", "neuro-oncology long-arc establishment" |
| `theme` | text | one-paragraph description of what this campaign is about |
| `hypothesis` | text | what Daniel believes the campaign will demonstrate — referenced at retro time |
| `start_date` | date | |
| `end_date` | date | |
| `status` | enum | `planning`, `active`, `completed`, `abandoned` |
| `success_criteria_json` | text | JSON. `{"distribution": [{"metric": str, "target": str, "actual": str|null}], "validation": [{"metric": str, "target": str, "actual": str|null}]}`. Targets set at planning; actuals filled at completion. |
| `parent_experiment_id` | int nullable | FK to `experiments.id` if this campaign is the execution arm of a registered experiment; ON DELETE SET NULL |
| `pillar` | text nullable | if the campaign is single-pillar, name it; multi-pillar campaigns leave NULL |
| `content_type` | text nullable | if the campaign is single-content-type per §28.17, name it; multi-type campaigns leave NULL |
| `notes` | text nullable | |
| `created_at_utc` | datetime | |
| `completed_at_utc` | datetime nullable | when status transitioned to `completed` or `abandoned` |

Indexes:

```text
index(status, start_date)
index(start_date, end_date)
index(parent_experiment_id) where parent_experiment_id is not null
```

Notes:
- A campaign is `active` if `start_date <= today <= end_date` AND `status = 'active'`. The Campaigns view (§14.12) highlights active campaigns prominently and surfaces "starting soon" / "ending soon" badges.
- `success_criteria_json` is structured to enforce the §1 dual-stream discipline: at least one distribution metric AND at least one validation metric per campaign. Schema validation in `app/agent/campaigns.py` refuses to save a campaign that has zero of either.
- Hypothesis is referenced at retro time by the agent's `analyze_campaign_progress` tool (§28.26) — without a written hypothesis, the retro can't say whether the campaign succeeded.

---

### `campaign_items`

Items planned (or shipped) under a campaign. Generic enough to carry posts, replies, events (e.g. a launch milestone), and reminders.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `campaign_id` | integer fk | ON DELETE CASCADE |
| `item_type` | enum | `post`, `reply`, `event`, `milestone`, `reminder` |
| `planned_for_date` | date nullable | when Daniel intends to ship this item; nullable for unscheduled-but-tracked items |
| `post_id` | int nullable | FK to `posts.id` once shipped (or pre-shipped for drafts); ON DELETE SET NULL |
| `agent_draft_id` | int nullable | FK to `agent_drafts.id` if this item is tied to a specific draft; ON DELETE SET NULL |
| `reply_target_id` | int nullable | FK to `reply_targets.id` if `item_type = reply`; ON DELETE SET NULL |
| `planned_text` | text nullable | pre-draft prose Daniel jotted; promoted to a real draft later via "Send to drafts" affordance |
| `status` | enum | `planned`, `drafted`, `shipped`, `skipped` |
| `notes` | text nullable | |
| `sort_order` | integer | display order within the campaign; lower = earlier |
| `created_at_utc` | datetime | |
| `completed_at_utc` | datetime nullable | when status transitioned to `shipped` or `skipped` |

Indexes:

```text
index(campaign_id, sort_order)
index(planned_for_date) where planned_for_date is not null
index(status)
```

Notes:
- `status` transitions: `planned → drafted` (when `agent_draft_id` is populated), `drafted → shipped` (when `post_id` is populated AND `posts.published_to_x_at IS NOT NULL`), `planned → skipped` (Daniel-decided).
- An item can be shipped without ever being a draft (Daniel writes manually, marks shipped, links to `post_id`). The state machine permits `planned → shipped` directly.
- Bidirectional linkage: `posts` rows don't have a direct `campaign_item_id` FK — the query goes through `campaign_items.post_id`. This avoids polluting `posts` with campaign-specific columns.

---

### `monthly_reviews`

Monthly retro mirror of `weekly_reviews`. Same epistemic discipline: counterfactual-note required before export, agent-drafted sections carry `<confidence>` tags per §28.14, blocked-export rules. See §28.27.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `iso_month` | text unique | `YYYY-MM` (e.g. `2026-05`) |
| `created_at_utc` | datetime | |
| `summary` | text nullable | one-paragraph month summary |
| `key_movements` | text nullable | what moved — followers, lanes, content types, validation events |
| `what_got_stuck` | text nullable | |
| `best_post_id` | int nullable | FK to `posts.id`; ON DELETE SET NULL |
| `worst_post_id` | int nullable | FK to `posts.id`; ON DELETE SET NULL |
| `strongest_pillar` | text nullable | |
| `weakest_pillar` | text nullable | |
| `strongest_content_type` | text nullable | per §28.17 axis |
| `weakest_content_type` | text nullable | |
| `follower_delta` | integer nullable | computed at retro time from `v_account_daily` |
| `stir_validation_summary` | text nullable | |
| `campaigns_completed_json` | text nullable | JSON array of `campaign_id`s that completed in this month, with their `success_criteria_json` actuals |
| `next_month_experiment` | text nullable | |
| `counterfactual_note` | text | REQUIRED before export (same rule as `weekly_reviews`) |
| `lesson` | text nullable | one-sentence lesson |
| `confidence_label` | enum nullable | `fact \| inference \| speculation \| mixed` — dominant label of the agent-drafted sections per §28.14. Export of a `speculation`-labeled review is blocked until acknowledged. |
| `exported_at_utc` | datetime nullable | when this review was last exported |
| `daniel_notes` | text nullable | |

Indexes:

```text
unique(iso_month)
index(created_at_utc desc)
```

Notes:
- Same export-blocked rule as `weekly_reviews`: if `counterfactual_note` is empty OR `confidence_label = 'speculation'`, export refuses with the canonical message.
- `iso_month` is the canonical identifier. The §14.6 Weekly Review view extension (§28.27) lets Daniel toggle weekly ↔ monthly cadence; the schema supports both side-by-side.
- The retro can reference `campaigns_completed_json` to discuss which campaigns landed; the agent's `draft_monthly_review_section` tool consumes this when drafting.

---

### `saved_inspiration_posts`

External X posts Daniel saved as inspiration — content he liked, hooks that worked, structures he wants to study. Distinct from `reply_targets` (those are posts to *engage with*); inspiration is posts to *learn from*. See §28.29.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `source_url` | text nullable | X post URL if known |
| `source_author` | text nullable | handle |
| `source_post_text` | text | the post body Daniel pasted; immutable after insert |
| `source_text_hash` | text | sha256(source_post_text) — for the plagiarism guard |
| `tags_json` | text nullable | JSON array of Daniel's tags (e.g. `["hook", "list-format", "neuro"]`) |
| `saved_at_utc` | datetime | |
| `notes` | text nullable | why Daniel saved it |
| `status` | enum | `active`, `archived` |

Indexes:

```text
unique(source_text_hash)
index(saved_at_utc desc)
index(source_author)
```

Notes:
- `unique(source_text_hash)` prevents accidental duplicate saves of the same text. Different paraphrases hash differently — that's expected, but exact dupes are blocked.
- The inspiration library is paste-driven. No scraping. Daniel pastes from X; the row is created.
- `status = 'archived'` keeps the row but excludes from the §14.13 default view.

---

### `inspiration_transforms`

The output of running a transform mode against a `saved_inspiration_posts` row. Each transform is one Claude invocation producing an `output_text` plus a plagiarism risk read. See §28.29 for the transform-mode catalog.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `saved_inspiration_id` | integer fk | ON DELETE CASCADE |
| `transform_mode` | enum | `structure`, `hook_pattern`, `counterpoint`, `original_version`, `voice_profile_version`, `expand`, `compress`. New modes added by extending the enum AND `app/agent/inspiration.py::TRANSFORM_MODES` together. |
| `output_text` | text | the transformed text the agent produced |
| `output_text_hash` | text | sha256(output_text) — for plagiarism guard recomputation |
| `jaccard_similarity` | real | 0-1, computed against `source_post_text` tokens. Deterministic Jaccard set similarity. |
| `longest_shared_ngram_length` | integer | longest contiguous n-gram (in words) shared between source and output. Deterministic. |
| `ai_reported_risk_label` | enum | `low`, `medium`, `high` — what the model itself reported in structured output |
| `plagiarism_risk_label` | enum | `low`, `medium`, `high` — FINAL label = `max(ai_reported_risk_label, deterministic_label)` per §28.29. AI cannot underreport when token overlap is high. |
| `model_used` | text | |
| `tokens_used` | integer | |
| `created_at_utc` | datetime | |
| `used_for_post_id` | int nullable | FK to `posts.id` if this transform was later promoted to a draft and shipped; ON DELETE SET NULL |
| `notes` | text nullable | |

Indexes:

```text
index(saved_inspiration_id, created_at_utc desc)
index(plagiarism_risk_label)
```

Notes:
- The plagiarism guard's `plagiarism_risk_label` is the COMBINATION of deterministic + AI-reported risk. The deterministic floor is what makes this trustworthy — an LLM can't undersell high token overlap because the Jaccard score is computed in Python.
- A `high` plagiarism_risk_label BLOCKS the "Send to drafts" affordance in §14.13 until Daniel checks an "I've reviewed and this is fine" box (audit-logged with the override reason). `medium` shows a yellow warning. `low` ships freely.
- Multiple transforms per source row are normal — Daniel may run `structure` + `hook_pattern` + `voice_profile_version` against the same source to see different angles.

---

### `audit_logs`

Comprehensive append-only log of state-changing events. Distinct from `agent_tool_calls` (which logs every agent tool invocation, including read-only) — `audit_logs` is the canonical record of what *changed*. See §28.30.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `occurred_at_utc` | datetime | |
| `event_category` | enum | `auth`, `x_op`, `publish`, `settings`, `export`, `data`, `admin`, `migration` |
| `event_type` | text | specific event within the category — e.g. `x_oauth_connected`, `publish_succeeded`, `settings_changed_niche_problem`, `export_csv_posts`, `data_deleted_post`, `migration_applied_015` |
| `actor` | text | always `'daniel'` for this single-user app; column present for forward-compat / audit-tool consistency |
| `target_type` | text nullable | e.g. `post`, `voice_profile`, `setting`, `campaign`, `agent_draft` |
| `target_id` | text nullable | id of the target as a string (because settings and views use non-int keys); NULL when the event doesn't reference a single row |
| `details_json` | text nullable | JSON with event-specific context. For settings changes: `{"setting_key": str, "old_value": str, "new_value": str}`. For publish: `{"post_id": int, "x_post_id": str}`. For data deletion: `{"snapshot_of_deleted_row": dict}` so deletes are recoverable from the audit log alone. |
| `success` | boolean | false on failed attempts (e.g. publish failure, export failure) |
| `error_message` | text nullable | populated when `success = false` |

Indexes:

```text
index(occurred_at_utc desc)
index(event_category, occurred_at_utc desc)
index(target_type, target_id)
```

Notes:
- Append-only. No UPDATE, no DELETE — even pruning is a Daniel-action that itself audit-logs as `event_category = 'admin', event_type = 'audit_logs_pruned'`.
- For settings changes, the diff is structured in `details_json` so a future "what changed in my niche definition over time?" view is just a query.
- For data deletion, `details_json.snapshot_of_deleted_row` preserves the row contents so the audit log itself is a recovery option (no separate soft-delete needed for this purpose).
- Comprehensive — covers `auth` events (OAuth connect/disconnect), `x_op` events (publish attempts whether successful or not), `settings` events (niche edits, voice profile regenerations, lore changes), `export` events (CSV/Markdown/JSON exports), `data` events (deletions, corrections), `admin` events (backup runs, vacuum runs, audit-log prunes), `migration` events (each applied migration logs one row).

---

### `blogs`

Long-form posts. Distinct production lifecycle from X posts: idea → outline → draft → edit → ready → exported → published_externally → archived. See §28.31. Each row is one blog; versioning lives in `blog_versions`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `slug` | text unique | URL-safe slug for export; auto-generated from `title` on first save, editable. Forbidden chars rejected at write time. |
| `title` | text | |
| `subtitle` | text nullable | |
| `current_body_markdown` | text | latest editor state; immutable past versions live in `blog_versions` |
| `status` | enum | `idea`, `outlining`, `drafting`, `editing`, `ready`, `exported`, `published_externally`, `archived` |
| `pillar` | text nullable | reuses §10 `post_classifications.pillar` taxonomy — blogs are pillar-classified just like X posts |
| `audience` | text nullable | reuses `post_classifications.audience` |
| `outline_markdown` | text nullable | the outline as a separate artifact — preserved through drafting so Daniel can compare draft to outline |
| `seo_title` | text nullable | for export metadata |
| `seo_description` | text nullable | for export metadata |
| `seo_tags_json` | text nullable | JSON array of strings |
| `external_url` | text nullable | populated once Daniel marks `published_externally`; the URL where the blog actually lives |
| `external_published_at` | datetime nullable | |
| `agent_assisted` | boolean default false | true when any version was AI-drafted; informational, used by §28.31 stats |
| `voice_profile_id_at_draft` | int nullable | FK to `voice_profiles.id`; the active voice profile when drafting last ran. ON DELETE SET NULL. |
| `niche_problem_snapshot` | text nullable | copy of `niche_problem` at first agent-draft event; freezes the identity context the blog was authored under |
| `niche_person_snapshot` | text nullable | same for `niche_person` |
| `target_length_words` | integer nullable | Daniel's intended length; informational, not a hard gate |
| `actual_length_words` | integer | computed from `current_body_markdown` on each save |
| `notes` | text nullable | Daniel's working notes, distinct from the blog body itself |
| `created_at_utc` | datetime | |
| `updated_at_utc` | datetime | updated on every save |

Indexes:

```text
unique(slug)
index(status, updated_at_utc desc)
index(pillar) where pillar is not null
index(external_published_at) where external_published_at is not null
```

Notes:
- `current_body_markdown` is the *live* state — what the editor shows. Saves to this column also append a row to `blog_versions` so history is preserved. The two writes happen in one transaction.
- The status enum is a state machine; transitions are enforced in `app/agent/blogs.py::transition_status` (e.g., can't jump `idea → published_externally` without passing through `ready` first). `archived` is reachable from any non-`idea` state.
- A blog with `status = 'published_externally'` requires `external_url` populated.
- Blogs read the same niche definition + voice profile + personality lore the X agent reads. Identity is unified across short-form and long-form by design.

---

### `blog_versions`

Immutable timeline of every blog save. Inserts only — no UPDATE, no DELETE. The "current" version is whichever row has `is_current_for_blog = true` (exactly one per `blog_id`, enforced by partial unique index). Daniel can revert by setting an older version's `is_current_for_blog = true` in a single transaction that demotes the prior current row.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `blog_id` | integer fk | ON DELETE CASCADE |
| `version_number` | integer | monotonically increasing per `blog_id`; starts at 1 |
| `body_markdown` | text | the body at this version |
| `body_text_hash` | text | sha256(body_markdown) — for detecting accidental no-op saves |
| `title_at_version` | text | snapshot of `blogs.title` at this version |
| `outline_markdown_at_version` | text nullable | snapshot of `blogs.outline_markdown` at this version |
| `status_at_version` | text | snapshot of `blogs.status` at this version |
| `created_by` | enum | `daniel`, `agent` — was this version produced by manual edit or an agent draft? |
| `agent_message_id` | int nullable | FK to `agent_messages.id` if `created_by = 'agent'`; ON DELETE SET NULL |
| `agent_action` | enum nullable | `outline`, `draft`, `edit_suggestion_applied`, `seo_metadata`; populated when `created_by = 'agent'` |
| `daniel_revision_note` | text nullable | Daniel's optional one-line "why this revision" |
| `confidence_label_at_version` | enum nullable | `fact \| inference \| speculation \| mixed` — dominant label of the agent-emitted version per §28.14. NULL for manual edits. |
| `is_current_for_blog` | boolean | exactly one true per `blog_id` (partial unique index); revert is atomic in a single transaction |
| `created_at_utc` | datetime | |

Indexes:

```text
unique(blog_id, version_number)
unique(blog_id) where is_current_for_blog = true
index(blog_id, created_at_utc desc)
```

Notes:
- A no-op save (where `body_text_hash` matches the current version's hash AND `outline_markdown` AND `title` AND `status` are unchanged) does NOT create a new version. This keeps the history meaningful — every row in `blog_versions` represents a real change.
- Reverting to an older version creates a NEW version (with the older body but a new `version_number` and a `daniel_revision_note = "reverted to version N"`). Reverting is forward-only history; the older row's `is_current_for_blog` doesn't flip back.
- `confidence_label_at_version` lets Daniel see at a glance which agent drafts were grounded vs. speculative — relevant when reverting between agent-drafted versions.

---

### `blog_exports`

One row per export operation. Blogs are exported to disk; the row records what was exported, in what format, to what path, and what the resulting file's content hash is. See §28.33.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `blog_id` | integer fk | ON DELETE CASCADE |
| `blog_version_id` | integer fk | the specific version that was exported; ON DELETE SET NULL |
| `format` | enum | `markdown`, `html`, `json`, `mdx` |
| `target_path` | text | absolute path on disk where the export landed |
| `file_size_bytes` | integer | recorded at export time |
| `content_sha256` | text | sha256 of the exported file's contents — for detecting later disk-side tampering or accidental overwrite |
| `seo_metadata_included` | boolean | true when the export embedded `blogs.seo_title` / `seo_description` / `seo_tags_json` (frontmatter for Markdown/MDX, `<head>` tags for HTML, top-level JSON keys) |
| `repurposing_links_included` | boolean | true when the export included a "Repurposing notes" footer summarizing linked X threads/posts (see `blog_to_post_links`) |
| `exported_at_utc` | datetime | |
| `daniel_notes` | text nullable | e.g. "for Substack" / "for personal site mirror" |

Indexes:

```text
index(blog_id, exported_at_utc desc)
index(format)
index(exported_at_utc desc)
```

Notes:
- Exports are append-only history. Re-exporting overwrites the file on disk but inserts a new `blog_exports` row.
- `content_sha256` is the export's audit anchor — if Daniel suspects a file was overwritten or tampered with, the row's hash is the source of truth.
- Export operations also write an `audit_logs` row per §28.30 (`event_category = 'export', event_type = 'blog_export_markdown'` etc.) so the audit log carries the full record alongside the typed `blog_exports` table.

---

### `blog_to_post_links`

Bidirectional linkage between blogs and X posts when one is the repurposed form of the other. See §28.34.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer pk | |
| `blog_id` | integer fk | ON DELETE CASCADE |
| `post_id` | integer fk | ON DELETE CASCADE |
| `direction` | enum | `blog_to_post` (blog written first, X post derived), `post_to_blog` (X post written first, blog expanded from), `parallel` (both authored at the same time from a shared idea) |
| `relationship_kind` | enum | `thread_root`, `quote_excerpt`, `summary_post`, `teaser_with_link`, `derived_outline`, `companion_post` |
| `notes` | text nullable | |
| `created_at_utc` | datetime | |
| `created_by` | enum | `daniel`, `agent` — who established the link? Agent-suggested links require Daniel confirmation before insert. |
| `agent_message_id` | int nullable | FK to `agent_messages.id` if agent-suggested; ON DELETE SET NULL |

Indexes:

```text
unique(blog_id, post_id, direction)
index(blog_id)
index(post_id)
```

Notes:
- A blog can link to multiple posts (a 6-post X thread derived from one blog → 6 rows). A post can link to multiple blogs (rare but possible — e.g. a single X post that summarizes two related essays).
- `direction` is *content provenance*, not workflow ordering — `blog_to_post` means "this X post's content comes from this blog," not "the blog was created first in the database."
- The §28.29 inspiration plagiarism guard runs on agent-suggested repurposing transforms (blog → X thread or X post → blog) — high overlap between the source and the derived form is expected, but it gets surfaced and audit-logged.

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

### `v_content_type_performance`

Performance sliced by `content_type` (V/G/P/P) per §28.16. Mirrors `v_lane_performance` so the same graduated-confidence discipline applies — pillar is *topic*, content_type is *purpose*, and both axes get the same epistemic treatment.

Fields:

```text
content_type
post_count
days_covered
median_impressions
iqr_impressions_low
iqr_impressions_high
median_engagement_rate
iqr_engagement_rate_low
iqr_engagement_rate_high
total_bookmarks
total_replies
stir_signal_count
confidence_label
```

Confidence-label logic is identical to `v_lane_performance` above — same thresholds, same rules. Rows with `content_type = 'unspecified'` are EXCLUDED from this view (they're not a meaningful category, just an unclassified backlog; the view is for active learning).

A second pivot — `v_content_type_x_pillar_performance` — is V1.1+ deferred (12 cells × 4 content types = 48 cells, density won't support it at MVP volume; revisit at 500+ shipped posts).

---

### `v_follower_velocity`

Projection math derived from `v_account_daily`. Anchors §28.19 and the §14.3 velocity panel. The view does NOT chase precision — when velocity is in the noise floor (per §13), projection columns return NULL rather than fabricated dates.

Fields:

```text
snapshot_date
followers_count
velocity_7d_per_day                       # already in v_account_daily; re-exposed for convenience
velocity_30d_per_day
current_milestone_target                  # from milestones; nearest unmet distribution rung
distance_to_current_milestone
projected_milestone_hit_date_at_7d_pace   # NULL when |delta_7d| < noise_floor OR velocity_7d_per_day <= 0
projected_milestone_hit_date_at_30d_pace  # NULL on same conditions
days_until_milestone_at_7d_pace           # NULL on same conditions
days_until_milestone_at_30d_pace          # NULL on same conditions
```

A parametric helper `daily_followers_needed_to_hit_milestone_by_date(target_date)` lives in `app/db.py` (not the view) — pure SQLite function call: `ceil((current_milestone_target - followers_count) / max((julianday(target_date) - julianday('now')), 1))`. Returns NULL if the milestone is already met or the date is in the past.

**Hard rule (carried from §13):** all projection columns are suppressed in the UI when `abs(delta_7d) < noise_floor`. The UI shows "trend not yet measurable — projections suppressed" in that state. Do not display a precise-looking date when the input is noise.

---

### `v_campaign_progress`

Per-campaign rollup of `campaign_items`. Powers the §14.12 Campaigns view's at-a-glance progress bars and the `analyze_campaign_progress` agent tool (§28.26).

Fields:

```text
campaign_id
campaign_name
status
start_date
end_date
days_until_start                    # negative when campaign already started
days_until_end                      # negative when campaign already ended
items_total
items_planned
items_drafted
items_shipped
items_skipped
percent_shipped                     # items_shipped / items_total; NULL when items_total = 0
percent_planned_shipped             # items_shipped / (items_planned + items_drafted + items_shipped); excludes skipped
latest_shipped_post_id              # most recent post_id with status='shipped' for this campaign
latest_shipped_at_utc
```

Notes:
- `percent_shipped` and `percent_planned_shipped` are NULL when `items_total = 0` (a campaign with no items planned yet — common at `status = 'planning'`). UI handles the NULL state with "no items planned yet."
- The view is read-only; campaign progress changes are driven by `campaign_items.status` transitions which fire from the manual-mode "Mark posted" click-handler + agent-draft promotion paths.

---

### `v_blog_pipeline`

Per-blog pipeline state for §14.14 Blogs index. Rolls up `blogs` + `blog_versions` + `blog_exports` into one row per blog.

Fields:

```text
blog_id
title
slug
status
pillar
audience
current_version_number
total_version_count
last_edited_at_utc                   # max(blog_versions.created_at_utc)
last_edited_by                       # daniel | agent (from latest blog_versions row)
days_since_last_edit
agent_assisted
latest_confidence_label              # last agent-version confidence_label_at_version
actual_length_words
target_length_words
length_gap_words                     # actual - target; NULL when target_length_words is NULL
export_count
last_exported_at_utc
last_export_format
external_url
external_published_at
days_in_current_status
```

Notes:
- `days_in_current_status` is computed from the most recent `blog_versions` row whose `status_at_version` equals the blog's current `status` — i.e., when did this blog enter its current state? Stale states (a blog stuck in `drafting` for 90 days) become visible.
- `latest_confidence_label` is informational; a `speculation`-labeled latest version doesn't BLOCK anything by itself, but the editor surfaces it as a yellow chip prompting "do you want to revise before exporting?"

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

   * Preserve raw API responses where feasible. (Empty until Phase 7.)

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

# 14.6 Weekly Review (+ Monthly Review tab)

### Purpose

Turn raw activity into learning. The view supports two cadences — **Weekly** (default, `weekly_reviews` table) and **Monthly** (`monthly_reviews` table). Both share the same UI shell with cadence-aware questions and auto-filled fields; toggle via the cadence selector at the top.

Monthly reviews are the longer-arc counterpart to weekly: they reference completed campaigns from the period (per §28.27), use month-granularity follower deltas, and surface the strongest/weakest **content type** alongside the strongest/weakest **pillar**. Same export-blocked discipline as weekly — `counterfactual_note` required, `confidence_label = speculation` blocks export until acknowledged.

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

   * X API mode (`data_collection_mode`):

     * **manual** (MVP default until Phase 7 ships)
     * **`api`** (default once Phase 7 migration 018 applies — xurl-backed reads)
   * X API writes (`publish_via_api_enabled`, default TRUE after Phase 8):

     * TRUE — §28.10 publish-flow calls `POST /2/tweets`.
     * FALSE — §28.10 takes the manual-clipboard branch (always-available fallback).
   * Grok discovery (`grok_api_enabled`, default TRUE after Phase 9):

     * TRUE — Grok sweep job runs at `grok_discovery_sweep_interval_minutes` cadence (default 120).
     * FALSE — Grok kill switch; manual + X API search paths still work.
   * Grok query list panel (Phase 9): CRUD over `grok_query_list_json` (one query per line), "Run sweep now" button, "Recent Grok failures (last 7 days)" from `grok_api_responses`.
   * X API failures panel (Phase 7): "Recent X API failures (last 7 days)" from `raw_api_responses` + last-refresh timestamp per scheduled job (see §17).
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

10. **Niche definition** — see §28.16

    * `niche_problem` (text) — one sentence: the problem you solve
    * `niche_person` (text) — one sentence: who you solve it for
    * "Test against bio" affordance — paste current X bio, agent critiques alignment with the niche definition (read-only — never edits the X bio itself)
    * Empty values BLOCK agent drafting (§28.2 rule #15); banner explains why and links to this panel

11. **Personality lore** — see §28.21

    * List of active lore rows from `personality_lore` (theme, description, invocation_count, last_invoked_at)
    * Add new lore (Daniel-only; agent has no write access)
    * Toggle `is_active` per row
    * Reorder by priority
    * "Over-relied on" yellow banner per row when `invocation_count > personality_lore_overuse_threshold` AND `last_invoked_at_utc > now() - 30 days`

12. **Profile audit** — see §28.25

    * "Last audit: N days ago" or "No audits yet"
    * "Run profile audit now" button — opens form prefilled with current bio + niche + active voice profile; Daniel pastes pinned-post text + (optional) recent-post window override
    * Past audits table: `audited_at_utc`, `overall_consistency_score`, top three actions, expand → full `audit_json`
    * Compare-to-previous diff view when ≥2 audits exist
    * `coach_refuse_without_evidence` toggle (default `true`) — also controls Coach view (§14.10)

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

# 14.9 Brain Dump — NEW

### Purpose

Capture-first surface, distinct from §14.8 Agent Chat (conversation-first). Daniel pastes raw thinking — half-formed ideas, observations, frustrations, fragments — without having to formulate a question. The agent processes the dump into clarifying questions + structured candidate drafts. Different UX mode: the user doesn't have to know what they want yet.

The Brain Dump's job is to *capture before evaluation*. Filtering, ranking, and drafting happen after the dump is on the page. This is the only XGrowth surface that accepts raw text without a structural commitment first.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Brain Dump                                  [+ new dump]    │
│ Last processed: 12 min ago     Pending: 1 unprocessed dump  │
└─────────────────────────────────────────────────────────────┘

[ Sidebar: past dumps list, newest first ]    [ Main: dump editor + results ]

Raw text:
┌──────────────────────────────────────────────────────────┐
│ [textarea — multi-line, no character limit]              │
│                                                            │
│ kitchen-scanner missed the difference between ginger     │
│ and soap again. third time this week. wondering if I     │
│ should make Cook Mode forgive scanner errors with a     │
│ "is this what you meant?" pass...                        │
└──────────────────────────────────────────────────────────┘
                                          [Process this dump]

After processing:

▼ Clarifying questions (3)
  - Is the "is this what you meant?" pass a new feature or a fallback?
  - How often does this happen vs. successful scans? (frame for value content)
  - Want to share the actual ginger→soap example, or just the pattern?

▼ Candidate drafts (4)
  1. [stir × icp × personality]  Three ginger→soap misreads this week...
  2. [build × icp × value]         Cook Mode is going to get a confirm pass...
  3. [self × other × personality]  Building in public means logging the AI's stupidest moments...
  4. [stir × icp × proof]          Day 47: kitchen scanner accuracy log...

[ Send draft #1 to drafts ]  [ Discard ]  [ Edit & save ]
```

### Required components

1. **Textarea + Process button.** No character limit. Submission creates a `brain_dumps` row with `status = unprocessed` and immediately fires the processing call. The textarea is bound to a `st.session_state` key scoped to the new-dump editor; cleared on successful save.

2. **Past dumps sidebar.** Newest first. Each entry shows the first line of `raw_text` (truncated), the `status`, and `created_at_utc` relative time. Click to load into the main panel.

3. **Processing UI.** Shows "Processing…" with a spinner while the agent call is in flight. On success, renders the two collapsible sections (clarifying questions + candidate drafts). On failure, renders a red banner with the error and a Retry button.

4. **Send to drafts.** Each candidate draft has its own "Send to drafts" button. Clicking calls `_save_draft_post` (or `_save_draft_reply` if the candidate carries `target_post_url`) with the candidate's metadata. The candidate moves to a "sent" state in the dump's UI; the original `brain_dumps` row is unchanged (audit trail is preserved).

5. **Annotation.** A `notes` textarea lets Daniel record what he did with the dump after the fact. Persists to `brain_dumps.notes`.

### Acceptance criteria

- Pasting raw text + clicking Process produces 1-5 candidate drafts within 30 seconds.
- The dump's `raw_text` is never edited after insert; refining the input creates a new dump.
- Candidate drafts inherit Daniel's active niche + voice profile + content-type axis (the agent sees the same system prompt).
- Sending a candidate to drafts runs the full Phase 5.8 pipeline (IWH, dark-pattern lint, content-type validation, pre-publish scorer, repetition guard).
- Failed dumps stay in the list with a Retry button; failures don't lose the raw text.

---

# 14.10 Coach — NEW

### Purpose

Dedicated *advice* surface. Conversational like §14.8 Agent Chat, but with a hard discipline: every analytical claim the coach makes is filtered through the §28.23 citation allowlist. Citations that don't resolve to a real `(record_type, record_id)` row in XGrowth's DB are *stripped* from the response, with the strip count surfaced under the message.

§14.8 Agent Chat is open-ended — the agent can speculate, brainstorm, generate. The Coach view explicitly constrains itself to *grounded* advice. Different use case: you bring a strategic question, the coach answers with citations or refuses to answer.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Coach                                         [+ new ask]   │
│ Citation discipline: ON — non-allowlisted citations stripped │
└─────────────────────────────────────────────────────────────┘

[ Sidebar: past coach sessions ]    [ Main: Q&A ]

User: What's the strongest pattern in my last 30 days?

Coach: Your `build × icp × value` posts are sitting in the
"stronger" confidence label of v_lane_performance over the
30-day window 〔post 142, post 156, post 161〕<confidence>fact</confidence>.
The median engagement rate for that lane is 4.1% vs. your overall
median of 1.7% 〔v_lane_performance row build/icp/value〕
<confidence>fact</confidence>. Three things are doing the work:
specificity (you cite the exact kitchen-scanner accuracy %),
behind-the-scenes framing (real screenshots), and a tight ending
ask 〔post 142, post 156〕<confidence>inference</confidence>.

[1 citation removed: agent claimed post 998 supported a point;
post 998 doesn't exist in your DB.]

──────────────────────────────────────────────────────────────
[ Compose your next question…                              ]
```

### Required components

1. **Citation chips inline with claims.** Each citation rendered as a clickable chip; click opens the cited record in a side panel (post text + metrics for a `post` citation, view contents for a view-row citation).

2. **Stripped-citation banner.** Under every coach message, if any citations were stripped, render a yellow note: "N citation(s) removed: <reason>." The reason is logged in `agent_tool_calls.notes` of the coach's underlying tool call.

3. **Confidence label chips.** Same as §14.8 (green/blue/yellow/gray for fact/inference/speculation/mixed).

4. **"Refuse if no evidence" mode.** Settings → Growth Agent → Coach gains a `coach_refuse_without_evidence` toggle (default `true`). When ON, the coach refuses to answer at all if it can't cite at least one real DB row to ground the answer — emits "I don't have data to answer this honestly" instead of a speculation-tagged guess.

5. **Past sessions sidebar.** Mirror of §14.8.

### Acceptance criteria

- Citations link to real DB rows; broken citations are stripped before display.
- Strip-count banner appears whenever ≥1 citation was removed.
- `coach_refuse_without_evidence = true` produces refusals instead of un-cited claims.
- The coach uses the SAME conversation infra as §14.8 (`agent_messages`, `agent_tool_calls`) — the discipline is a post-filter, not a separate model. `evidence_citations_json` persists the surviving citations on the `agent_messages` row.
- The coach NEVER reads `stir_testers` or `stir_conversion_events.qualitative_feedback` (existing §28 read-scope rule applies).

---

# 14.11 Content Calendar — NEW

### Purpose

Visual upcoming + recent posts in a calendar grid. The existing §14.1 Today and §14.2 Next Rep views answer "what should I do *now*?" The Content Calendar answers "what does my distribution surface look like over the next 2 weeks and the past 2 weeks?" Distinct cognitive mode — planning vs. doing.

Pairs with §19 Should-ship item 11 ("Scheduled publish drafts") and §14.12 Campaigns — campaign items with `planned_for_date` populate the calendar automatically.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Content Calendar          [< prev week]  Week of May 18-24  [next week >] │
│ View: [Week] [Two weeks] [Month]    Filter: [all]    [+ schedule slot] │
└─────────────────────────────────────────────────────────────┘

         Mon 18    Tue 19    Wed 20    Thu 21    Fri 22    Sat 23    Sun 24
 ┌─────┬─────────┬─────────┬─────────┬─────────┬─────────┬─────────┬─────────┐
 │ AM  │ stir    │         │ build   │         │ stir    │         │ self    │
 │     │ value   │         │ proof   │         │ growth  │         │ pers.   │
 │     │ POSTED  │         │ DRAFTED │         │ PLANNED │         │ PLANNED │
 ├─────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┤
 │ PM  │         │ self    │         │ build   │         │         │         │
 │     │         │ pers.   │         │ value   │         │         │         │
 │     │         │ POSTED  │         │ POSTED  │         │         │         │
 └─────┴─────────┴─────────┴─────────┴─────────┴─────────┴─────────┴─────────┘

[Active campaigns running through this window:]
- "first 5 downloads push" (May 14 - May 28) — 3 items shipped, 2 planned
- "neuro-oncology long arc" (May 1 - Jul 1) — 1 item this window
```

### Required components

1. **Week / two-week / month toggle.** Default Week. Persisted in `st.session_state` per Daniel.
2. **Cell content.** Each cell shows `pillar × content_type × status` for any posts/drafts/planned items in that AM/PM slot. Status chip color matches the rest of the dashboard's status conventions (POSTED = filled bone, DRAFTED = outline, PLANNED = dashed outline, SKIPPED = struck-through gray).
3. **Click-through.** Click a cell → opens the source row (a `posts` row for POSTED/DRAFTED, a `campaign_items` row for PLANNED).
4. **+ schedule slot.** Adds a new `campaign_items` row (with `item_type = 'post'`, `status = 'planned'`, `planned_for_date` = the selected day) OR a standalone `posts` row with `manual_confirmation_status = 'draft'` and a `created_in_app_at` future date — Daniel picks. The "Schedule" toggle uses §19 item 11 (scheduled publish drafts) once Phase 5.11 wires it.
5. **Active campaigns strip.** Lists currently-active campaigns whose `[start_date, end_date]` overlaps the visible window. Click → opens the campaign in §14.12.
6. **Filter dropdown.** All / pillar / content_type / campaign. Filters which items render in cells.

### Acceptance criteria

- Cells render correctly for all four item provenances: shipped posts, agent drafts, manual drafts, campaign-planned items.
- "+ schedule slot" opens an inline form, not a modal, so Daniel doesn't lose calendar context.
- Switching weeks via prev/next keeps the AM/PM grid stable (no layout shift).
- AM = before noon local time, PM = noon onward. Times come from `created_at_utc` for shipped, `planned_for_date` for planned (with a configurable default-AM rule).
- Calendar respects active filters across all visible weeks (e.g. filter by "build" pillar persists across week navigation).

---

# 14.12 Campaigns — NEW

### Purpose

Plan and track multi-week themed pushes. A campaign is a hypothesis + a date range + a set of planned items + success criteria. This is the strategic layer between §14.6 Weekly Review (retrospective, one week) and the milestone ladders (long-arc). Campaigns are typically 2–8 weeks; the schema doesn't enforce duration.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Campaigns                                  [+ new campaign] │
│ Active: 2    Planning: 1    Completed: 5    Abandoned: 1   │
└─────────────────────────────────────────────────────────────┘

Active campaigns
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▼ "first 5 downloads push"               May 14 - May 28  ●●●●●●●○○○ 60%
  Hypothesis: a one-week explicit ask, threaded daily, will produce ≥5 downloads
  Success: 5 downloads / 1 working-parent tester / ≥3 quality replies
  Items: 7 shipped, 3 planned, 0 skipped         [Open campaign]

▼ "neuro-oncology long arc"              May 1 - Jul 1   ●○○○○○○○○○ 12%
  ...

Planning
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▷ "founder zero push"                     starts Jun 1
  ...

Completed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▷ "build-in-public week"                  Apr 7 - Apr 14    ●●●●●●●●●● 100%
  [Show retrospective ↓]
```

### Required components

1. **Status sections.** Active, Planning, Completed, Abandoned. Active campaigns expanded by default; others collapsed.
2. **Campaign detail expansion.** Hypothesis, success criteria (with actuals populated for completed campaigns), item list (with status chips), notes.
3. **"+ new campaign" form.** Required fields: name, theme, hypothesis, start_date, end_date, success_criteria (must include ≥1 distribution metric AND ≥1 validation metric — schema validation rejects otherwise).
4. **Item management.** Per-campaign, add/edit/remove items. Each item has `item_type`, `planned_for_date`, optional `planned_text` or link to `agent_draft_id` / `post_id` / `reply_target_id`. Drag-to-reorder updates `sort_order`.
5. **Agent integration.** Per-campaign "Ask the agent for ideas" button → opens §14.8 Agent Chat with a prefilled prompt: "Given this campaign's hypothesis + success criteria, suggest 3 items to add." The agent reads the campaign via the new `analyze_campaign_progress` tool (§28.26) and proposes items as `campaign_items` rows with `status = 'planned'`.
6. **Retrospective on completion.** When a campaign transitions to `completed`, an inline retrospective form opens: success_criteria actuals + lesson + counterfactual_note. The lesson lands in `weekly_reviews` or `monthly_reviews` (Daniel picks) as a campaign-scoped insight.

### Acceptance criteria

- Cannot save a campaign without ≥1 distribution metric AND ≥1 validation metric in `success_criteria_json` (schema validation in `app/agent/campaigns.py`).
- Transitioning to `completed` blocks until success_criteria actuals + lesson + counterfactual_note are filled.
- "Active" status auto-derives from `start_date <= today <= end_date` AND `status = 'active'` (not just `status` alone).
- Per-campaign progress bar uses `v_campaign_progress.percent_shipped`; NULL when no items planned shows "no items planned yet."
- Items can be linked to existing posts/drafts/reply-targets — no duplicate state; the campaign is a *grouping*, not a parallel content table.

---

# 14.13 Inspiration Library — NEW

### Purpose

A capture-then-remix workflow for external X content. Daniel saves posts he liked (paste-driven) and runs transform modes against them — `structure`, `hook_pattern`, `counterpoint`, `original_version`, `voice_profile_version`, `expand`, `compress`. Each transform produces text + a deterministic plagiarism risk read. The output flows into the regular drafts pipeline if Daniel chooses.

Distinct from §14.9 Brain Dump (which captures Daniel's own raw thinking) and §29.7 Reply Target Queue (which captures posts to *engage with*). Inspiration is posts to *learn from* — pattern, hook, structure, counter-argument.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Inspiration Library                       [+ save inspiration] │
│ 47 saved    7 transforms this week    1 flagged high-risk   │
└─────────────────────────────────────────────────────────────┘

[ Sidebar: saved inspirations list, newest first, with tag filter ]
[ Main: selected inspiration's source + transforms ]

Source post (saved Apr 30):
@some_account · "Three failed dinner attempts before 7pm taught me more
about UX than any course."

Tags: [hook] [self-deprecation] [concrete-numbers]

Transforms:
▼ structure                                      Risk: low
  An opener that names a specific small failure + a learning frame.
  Pattern: "[N] [concrete failure events] taught me more about
  [topic] than [conventional source]."
  [Send to drafts]

▼ counterpoint                                   Risk: low
  Failure stories that don't tie to a takeaway are still worth
  posting — not every post needs to be a lesson...
  [Send to drafts]

▼ voice_profile_version                          Risk: medium
  Three ginger→soap misreads this week taught me more about
  kitchen-scanner UX than any spec document.
  ⚠ medium risk — n-gram overlap with source. Review carefully.
  [Send to drafts]
```

### Required components

1. **+ save inspiration form.** `source_url` (optional), `source_author` (optional), `source_post_text` (required), `tags` (comma-separated), `notes`. Saving inserts to `saved_inspiration_posts`; duplicate `source_text_hash` is rejected.
2. **Inspiration sidebar.** Newest first. Tag filter (multi-select). Status filter (`active` / `archived`).
3. **Transform panel.** Buttons for each transform mode. Click → runs `app/agent/inspiration.py::transform(saved_inspiration_id, mode)`. Each transform persists as a row in `inspiration_transforms`. Display all prior transforms for the selected inspiration.
4. **Plagiarism risk chip.** Each transform shows `plagiarism_risk_label` (green=low, yellow=medium, red=high). For `medium`, a one-line warning. For `high`, the "Send to drafts" button is DISABLED until Daniel checks an "I've reviewed this and the overlap is acceptable" box; checking that box logs the override to `audit_logs`.
5. **Send to drafts.** Inherits niche + content type + voice profile context from the active settings; calls `_save_draft_post` with the transformed text. Full Phase 5.8 pipeline downstream.
6. **Archive / unarchive.** Per-row affordance; archived inspirations hidden by default but searchable.

### Acceptance criteria

- Duplicate text saves are rejected by `unique(source_text_hash)`; UI shows "you already saved this on YYYY-MM-DD, here's the existing entry."
- Transform modes match `app/agent/inspiration.py::TRANSFORM_MODES` exactly; new modes require schema enum extension + code update together.
- `plagiarism_risk_label` is the MAX of deterministic Jaccard/n-gram + AI-reported; the AI cannot underreport. This is non-negotiable and unit-tested.
- High-risk transforms block "Send to drafts" until override; override is audit-logged with reason.
- Transforms are independent rows — multiple transforms per source are normal and explicitly supported.
- The library NEVER reads `stir_testers` / `stir_conversion_events.qualitative_feedback`.

---

# 14.14 Blogs — NEW

### Purpose

Index view for all blogs in any status. Lists blog rows with their pipeline state, length vs. target, last-edited info, and export history. The entry point into long-form authoring; the actual writing happens in §14.15 Blog Editor.

Pillar identity is unified: the agent's niche definition, voice profile, voice samples, and personality lore all feed blog drafting exactly as they feed X drafting. The point of putting blogs in XGrowth instead of a separate tool is precisely this unified identity surface.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Blogs                                       [+ new blog]    │
│ Idea: 3   Outlining: 2   Drafting: 4   Editing: 1   Ready: 2 │
│ Exported: 7   Published: 4   Archived: 2                    │
└─────────────────────────────────────────────────────────────┘

Filter:  [all] [idea] [outlining] [drafting] [editing] [ready] [exported]
Sort:    [last edited] [stale longest] [length-gap] [pillar]

┌──────────────────────────────────────────────────────────────┐
│ ▶ Kitchen scanner UX from three failed dinner attempts       │
│   stir × icp   drafting   1,247 / 1,800 words   −553         │
│   v.4   ●agent  edited 2h ago   ⚠ speculation                │
│   [Open]                                                     │
├──────────────────────────────────────────────────────────────┤
│ ▶ Why I'm building Stir before neuro-oncology                │
│   self × icp   ready    2,011 / 2,000 words   +11            │
│   v.7   ●daniel  edited 3d ago   ● fact                      │
│   [Open]  [Export ↓]                                         │
└──────────────────────────────────────────────────────────────┘
```

### Required components

1. **Status counters strip.** All eight statuses with counts. Click a count to filter the list.
2. **Filter dropdown + sort selector.** Filter is multi-select status; sort options are last-edited, stale-longest (`days_in_current_status` desc), length-gap (largest gap first), pillar.
3. **Blog row.** Shows title, pillar × audience chips, status badge, length vs. target, current version + author chip (daniel/agent), last-edited relative time, latest confidence chip. Click "Open" → §14.15 Blog Editor.
4. **"+ new blog" form.** Inline form (not modal): title (required), pillar (optional), audience (optional), target_length_words (optional). Creates blog with `status = 'idea'`; navigates to §14.15.
5. **Per-row Export.** Only visible for `status IN ('ready', 'exported', 'published_externally')`. Opens the export dialog inline.
6. **Stale-state highlight.** Rows with `days_in_current_status > blog_stale_status_warning_days` (default 21) get a yellow keyline indicating the blog's been sitting in its current state too long.

### Acceptance criteria

- Filter and sort persist in `st.session_state['blogs_filter']` and `['blogs_sort']` across navigation.
- Status counters compute from `v_blog_pipeline` group-by; the strip totals match the filter results.
- Length-gap sort puts the largest absolute gap first (positive or negative); ties broken by last-edited.
- Stale-state highlight uses the configurable threshold, not a hardcoded number.
- "+ new blog" creates the blog AND navigates atomically — no half-created rows if navigation fails.

---

# 14.15 Blog Editor — NEW

### Purpose

The actual writing surface. One blog open at a time. Three panels: outline (left), body editor (center), agent + metadata + version history (right). Status transitions, agent draft/edit/SEO actions, and exports all happen here.

### Layout

```text
┌─────────────────────────────────────────────────────────────┐
│ Blog Editor   ← Back to Blogs                              │
│ Title: Kitchen scanner UX from three failed dinner attempts │
│ Status: [drafting ▼]   1,247 words   v.4   2h ago          │
└─────────────────────────────────────────────────────────────┘

┌─────────────┬─────────────────────────────────┬──────────────┐
│ Outline     │ Body                            │ Agent panel  │
│             │                                 │              │
│ ## Hook     │ Three failed dinner attempts    │ Voice profile│
│ Three fails │ before 7pm taught me more       │ active: ✓    │
│             │ about Cook Mode UX than any...  │              │
│ ## Frame    │                                 │ Niche: ✓     │
│ Scanner err │ ## The pattern                  │ Lore: 3 act. │
│             │                                 │              │
│ ## Pattern  │ ...                             │ [Outline]    │
│ misreads    │                                 │ [Draft]      │
│             │                                 │ [Suggest    │
│ ## Lesson   │                                 │  edits]      │
│ confirm pass│                                 │ [SEO]        │
│             │                                 │              │
│             │                                 │ Versions     │
│             │                                 │ ▼ v.4 ●agent │
│             │                                 │ ▷ v.3 ●daniel│
│             │                                 │ ▷ v.2 ●agent │
│             │                                 │ ▷ v.1 ●daniel│
│             │                                 │              │
│             │                                 │ Linked to:   │
│             │                                 │ ◆ post 142   │
│             │                                 │ ◆ post 156   │
└─────────────┴─────────────────────────────────┴──────────────┘

  [Save]  [Discard changes]   [Export ▾]   [Repurpose to X ▾]
```

### Required components

1. **Outline panel (left).** Editable Markdown. Saving the outline writes to `blogs.outline_markdown` and appends a `blog_versions` row with `status_at_version = current status` and `agent_action = NULL` if Daniel-edited.

2. **Body editor (center).** Editable Markdown. Saving writes `blogs.current_body_markdown` and appends a `blog_versions` row in the same transaction. No-op saves (hash unchanged) skip the version-append.

3. **Status selector.** Dropdown showing the current status; changing it triggers `app/agent/blogs.py::transition_status(blog_id, new_status)` which validates the transition is legal. Illegal transitions show an inline error; legal ones land immediately and write a version row.

4. **Agent panel (right) — actions.**
    - **Outline** — calls `outline_blog` tool (§28.32). Prefills outline panel with the agent's structured outline; Daniel can edit before saving. Agent run produces a `blog_versions` row with `created_by = 'agent', agent_action = 'outline'`.
    - **Draft** — calls `draft_blog` tool with the current outline as input. Prefills the body panel with the agent's draft; Daniel reviews/edits before saving. Agent run produces a `blog_versions` row with `agent_action = 'draft'`.
    - **Suggest edits** — calls `suggest_blog_edits` tool with the current body. Returns a structured list of inline edit suggestions (per-paragraph). Each suggestion has Accept / Reject / Modify buttons; accepted edits write to the body atomically.
    - **SEO** — calls `generate_blog_seo_metadata` tool. Populates `seo_title`, `seo_description`, `seo_tags_json`.
    - All four actions respect §28.6 cost cap and emit `<confidence>` tags parsed per §28.14.

5. **Agent panel — identity readout.**
    - Active voice profile (id + last-regenerated date).
    - Active niche (problem + person, truncated).
    - Active personality lore count.
    - All three are live-bound to settings; changing voice profile or niche in Settings updates the readout on the next rerun.

6. **Agent panel — version history.**
    - List of `blog_versions` rows for this blog, newest first. Each row shows `version_number`, `created_by` chip, relative time, `agent_action` if any, `confidence_label_at_version` if any.
    - Click a version → side-by-side diff with current.
    - "Revert to this version" button → creates a new version row with the older body and `daniel_revision_note = "reverted to v{n}"`; current pointer moves to the new row.

7. **Agent panel — linked posts.**
    - List of `blog_to_post_links` rows for this blog. Each shows direction + relationship_kind + post text excerpt.
    - "Add link" → inline form picks a `posts.id` and `relationship_kind`.

8. **Footer actions.**
    - **Save** — atomic write of `current_body_markdown` + `outline_markdown` + new `blog_versions` row. Disabled when no changes.
    - **Discard changes** — reloads from DB.
    - **Export ▾** — opens export dialog with Markdown / HTML / JSON / MDX choice + target-path picker + "include SEO metadata" + "include repurposing notes" toggles. On confirm, writes the file to disk, inserts a `blog_exports` row, writes an `audit_logs` row, transitions the blog to `exported` if it was `ready`.
    - **Repurpose to X ▾** — opens a sub-menu: "Thread from sections" / "Single post summary" / "Teaser + link" — each calls `repurpose_blog_to_x` (§28.34) and routes the output into the regular drafts pipeline.

### Acceptance criteria

- No-op saves do NOT create a new `blog_versions` row.
- Status transition validation rejects illegal transitions with an inline error; legal transitions write a version row.
- Reverting to an older version creates a forward-moving version row (does not retroactively flip `is_current_for_blog`).
- All four agent actions emit `<confidence>` tags; the parsed dominant label persists on the resulting `blog_versions.confidence_label_at_version`.
- Exports write both the file AND the `blog_exports` row AND the `audit_logs` row in the same transaction-or-fail boundary; partial states are not possible.
- Repurpose-to-X outputs flow through the full Phase 5.8 drafts pipeline (IWH, dark-pattern lint, content-type validation, pre-publish scorer, repetition guard, AND the plagiarism guard per §28.29).
- Identity readout is correct after a voice-profile regeneration or niche edit — no cached state.

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

13. **Niche & Content-Type Calibration Pack (Phase 5.9 — see §28.16 through §28.21):**

    * Structured niche definition (`niche_problem`, `niche_person` settings) with system-prompt Section 1 splice + Settings → Growth Agent → Niche panel + "test against bio" affordance (§28.16).
    * Content type axis (`posts.content_type` + `agent_drafts.content_type` + `v_content_type_performance` view + `get_content_type_gaps` agent tool + Today/Next Rep content-type recommendation + Content Performance content-type tab) (§28.17).
    * Reply-quality lint (`agent_drafts.reply_quality_lint_passed` + extension to `app/agent/lint.py` with the "forced / AI / selfishly self-promoting" detector) (§28.18).
    * Follower-velocity projection (`v_follower_velocity` view + §14.3 Progress velocity panel + `get_velocity_projection` agent tool) (§28.19).
    * Replier-pool candidate discovery (extended `reply_targets.source` enum value `replier_under_thread` + new `score_replier_pool` agent tool + §29.7 Reply Target Queue "Add replier pool" affordance) (§28.20).
    * Personality lore registry (`personality_lore` table + system-prompt Section 5 splice + Settings → Growth Agent → Personality lore panel) (§28.21).

14. **Strategic Analysis Pack (Phase 5.10 — see §28.22 through §28.25):**

    * Brain Dump capture-first view (`brain_dumps` table + new §14.9 Brain Dump view + `app/agent/brain_dump.py` + new agent tool `process_brain_dump`) (§28.22).
    * Coach with citation allowlist (extension to `agent_messages.evidence_citations_json` + new §14.10 Coach view + `app/agent/coach.py` citation-allowlist post-filter + `coach_refuse_without_evidence` setting) (§28.23).
    * Account Researcher (`account_research_reports` table + new agent tool `analyze_account` + Account Researcher tab in §29.7 Reply Target Queue + linkage to `reply_targets`) (§28.24).
    * Profile Audit (`profile_audits` table + new agent tool `audit_profile` + Settings → Growth Agent → Profile Audit panel + compare-to-previous diff view) (§28.25).

15. **Growth Layer + Quality-of-Life Pack (Phase 5.11 — see §28.26 through §28.30):**

    * Campaigns + campaign items (`campaigns` + `campaign_items` tables + `v_campaign_progress` view + new §14.12 Campaigns view + new tool `analyze_campaign_progress`) (§28.26).
    * Monthly AI reviews (`monthly_reviews` table + §14.6 Weekly Review cadence toggle + new tool `draft_monthly_review_section`) (§28.27).
    * Content Calendar (new §14.11 view + integration with `campaign_items.planned_for_date` + scheduled-drafts surface) (§28.28).
    * Inspiration library (`saved_inspiration_posts` + `inspiration_transforms` tables + new §14.13 Inspiration Library view + `app/agent/inspiration.py` with seven transform modes + deterministic plagiarism guard + new tools `transform_inspiration` and `score_inspiration_plagiarism_risk`) (§28.29).
    * Comprehensive audit logs (`audit_logs` table + write-through from every state-changing path + Settings → Audit log viewer) (§28.30).

16. **Long-form blogs (Phase 6 — see §28.31 through §28.34):**

    * Blog production tables (`blogs` + `blog_versions` + `blog_exports` + `blog_to_post_links`) and `v_blog_pipeline` view (§28.31).
    * Two new views: §14.14 Blogs index + §14.15 Blog Editor (3-panel layout: outline / body / agent + version history).
    * Blog drafting agent tools: `#25 outline_blog`, `#26 draft_blog`, `#27 suggest_blog_edits`, `#28 generate_blog_seo_metadata` (§28.32).
    * Exports: Markdown / HTML / JSON / MDX with optional SEO frontmatter and optional repurposing-notes footer (§28.33).
    * Bidirectional X ↔ blog repurposing via `#29 repurpose_blog_to_x` and `#30 repurpose_x_to_blog_idea`; outputs flow through full Phase 5.8 drafts pipeline AND the §28.29 plagiarism guard (§28.34).
    * Unified identity: same niche definition + voice profile + voice samples + personality lore feed blog drafting as feed X drafting. The point of this phase.

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

### Phase 5.9 — Niche & Content-Type Calibration Pack (see §28.16 through §28.21 for full spec)

Six features distilled from a tactical X-growth video (Jacob Edmunds, May 2026) cross-referenced against XGrowth's existing thesis. Adds structured niche identity, an orthogonal V/G/P/P content axis, a reply-quality lint, a follower-velocity projection, a replier-pool candidate-discovery path, and a personality-lore registry. All six are additive; none changes existing contracts. Build order below; later features depend on earlier ones (notably the content_type axis feeds the content-type recommendation, which feeds the §14.1 Today panel).

**Migration:**

* [ ] Migration `migrations/012_niche_content_type.sql`:

  * [ ] Add `posts.content_type` enum column with CHECK constraint `IN ('value', 'growth', 'personality', 'proof', 'unspecified')`. Default `'unspecified'`. Backfill all existing rows to `'unspecified'` — DO NOT retro-classify.
  * [ ] Add `agent_drafts.content_type` enum column with same CHECK constraint, but NO `'unspecified'` default — new rows are NOT NULL via orchestrator enforcement (CHECK allows the value but orchestrator refuses to save it).
  * [ ] Add `agent_drafts.reply_quality_lint_passed` boolean nullable.
  * [ ] Create `personality_lore` table per §10 schema.
  * [ ] Add or extend `reply_targets.source` to support the value `'replier_under_thread'` alongside any existing source values. If `source` already exists as a CHECK or enum, ALTER to add the new value; if it doesn't exist (Phase 5.6 baseline schema), add the column with CHECK `IN ('paste_url', 'agent_curated_account', 'replier_under_thread')` default `'paste_url'`. Migration MUST inspect the prior schema before applying.
  * [ ] Add index `(content_type)` on `posts`.
  * [ ] Create computed view `v_content_type_performance` per §11.
  * [ ] Create computed view `v_follower_velocity` per §11.
  * [ ] Add settings rows: `niche_problem = ''`, `niche_person = ''`, `reply_quality_lint_enabled = true`, `personality_lore_overuse_threshold = 8`, `content_type_recommendation_window_days = 7`, `velocity_projection_noise_floor_followers = 10`. Documented `note` per row; do NOT silently reuse §13 noise-floor settings — copy or alias the value explicitly so the Velocity view's suppression rule is auditable from one place.

**Structured niche definition (§28.16):**

* [ ] Implement Settings → Growth Agent → Niche panel:

  * [ ] Two text fields (`niche_problem`, `niche_person`) bound to settings rows.
  * [ ] "Test against bio" affordance: textarea for current X bio, "Critique alignment" button that calls a one-shot Haiku invocation (`config/niche_alignment_prompt.md`) returning a structured `{aligned: bool, gaps: [str], suggestions: [str]}` JSON. Never edits the X bio itself — read-only critique.
  * [ ] Empty-state CTA: when either field is empty, show "your agent is in low-power mode — define your niche to unlock drafting" with examples from the video ("the problem I solve: how to grow on X; the person I solve it for: educational creators").
* [ ] Extend `app/agent/prompt_builder.py`:

  * [ ] Splice into §28.3 Section 1 (Identity) as a load-bearing line after the existing identity prose: `"You help **{niche_person}** with **{niche_problem}**."` (verbatim, with settings values substituted).
  * [ ] If either setting is empty, splice `"(niche not yet defined — drafting is disabled until Daniel fills Settings → Growth Agent → Niche)"` and set a build-time flag that the orchestrator reads to refuse all `save_draft_*` calls (§28.2 rule #15 enforcement).
  * [ ] Pre-commit drift check extended to verify the splice executes.
* [ ] Add §28.2 rule #15 enforcement in `app/agent/session.py`: orchestrator refuses `save_draft_post` / `save_draft_reply` when either niche setting is empty; emits "niche must be defined" message back to the conversation.

**Content type axis (§28.17):**

* [ ] Implement `app/agent/content_types.py`:

  * [ ] `CONTENT_TYPES = ('value', 'growth', 'personality', 'proof')` constant.
  * [ ] Docstring definitions per the §28.17 table (mirror Jacob Edmunds's V/G/P/P definitions verbatim where they're load-bearing; rephrase in XGrowth voice where they'd otherwise import hype).
  * [ ] `get_content_type_gaps(window_days: int = 7) -> dict` — returns counts per content type for the window; tool wrapper in `app/agent/tools.py`.
* [ ] Extend §28.4 tool table:

  * [ ] `save_draft_post` and `save_draft_reply` gain REQUIRED `content_type` parameter (`value | growth | personality | proof`). Orchestrator validates against the enum AND rejects `unspecified` from the agent.
  * [ ] New tool `#12 get_content_type_gaps(window_days)` — read-only.
* [ ] Wire `posts.content_type` into the manual-mode "Mark posted" click-handler (so manual posts get classified too).
* [ ] Update `config/agent_system_prompt.md` Section 6 (Current taxonomy) to include the content_type axis with the four-type definition table.
* [ ] UI surfaces:

  * [ ] §14.1 Today — new "today's content type recommendation" line: derived from `get_content_type_gaps` over `content_type_recommendation_window_days`; shows the under-represented type with one-line rationale ("you've shipped 5 value posts this week, 0 personality").
  * [ ] §14.4 Content Performance — new tab "Content type" showing `v_content_type_performance` with the same graduated-confidence treatment as `v_lane_performance`.
  * [ ] Content Performance per-post filter gains `content_type` dropdown.
* [ ] Unit tests: golden-input `get_content_type_gaps` over a seeded `posts` table; orchestrator-refusal test for missing `content_type` on save.

**Reply-quality lint (§28.18):**

* [ ] Extend `app/agent/lint.py` with `reply_quality_lint(text: str, target_post_text: str) -> tuple[bool, str]` — Haiku invocation with the one-shot prompt: *"Does this reply sound forced, AI-generated, or selfishly self-promoting (would the original poster find it annoying)? Reply yes/no with one-line reasoning."* Returns `(passed: bool, reason: str)`.
* [ ] Wire into `_save_draft_reply` preflight, AFTER dark-pattern lint and BEFORE the agent_drafts insert. Failure counts as a failed IWH revision (same enforcement path as the dark-pattern lint — increments `iwh_attempt_index`).
* [ ] Gated by `reply_quality_lint_enabled` setting (default `true`); on `false`, the lint short-circuits to `(True, "lint disabled")` and writes that as the `agent_drafts.reply_quality_lint_passed` value.
* [ ] Tests:

  * [ ] Synthetic forced reply ("Great post! 🔥 Check out my stuff at...") → `passed = false`, IWH increment.
  * [ ] Genuine substantive reply addressing the target post → `passed = true`.
  * [ ] Toggle off → both inputs return `passed = true` regardless.

**Follower-velocity projection (§28.19):**

* [ ] Implement `app/agent/velocity.py`:

  * [ ] `get_velocity_projection() -> dict` reading `v_follower_velocity`. Tool wrapper in `app/agent/tools.py` as `#13 get_velocity_projection()`.
  * [ ] All projection columns suppressed (return None) when `abs(delta_7d) < velocity_projection_noise_floor_followers`. §13 discipline carried through.
* [ ] §14.3 Progress velocity panel: renders velocity_7d_per_day + projection date when not in noise floor; renders "trend not yet measurable — projections suppressed" when in noise floor.
* [ ] Date-target widget: Daniel picks a target date; UI shows "to hit `{current_milestone_target}` by `{target_date}` you need +`{daily_followers_needed_to_hit_milestone_by_date(target_date)}`/day." If milestone already met or target date in the past, widget shows a sensible inert state.
* [ ] Tests: noise-floor suppression path (zero projections); non-noise path returns plausible date; date helper returns expected math.

**Replier-pool candidate discovery (§28.20):**

* [ ] Implement `app/agent/replier_pool.py`:

  * [ ] `score_replier_pool(thread_url, replier_handles_or_excerpts, lookback_minutes=60) -> list[ReplyTargetCandidate]` — same 4-dim scoring as §29.3 plus a new dimension `thread_context_fit_score` (0-3, deterministic from the replier's text + Daniel's niche definition).
  * [ ] Tool wrapper in `app/agent/tools.py` as `#14 score_replier_pool(thread_url, replier_handles_or_excerpts_json, lookback_minutes)`.
* [ ] Reply Target Queue (§29.7) UI: new "Add replier pool" affordance — text input for thread URL, textarea for replier handles + excerpts, submit → `score_replier_pool` → results land in `reply_targets` with `source = 'replier_under_thread'`.
* [ ] Update `config/agent_system_prompt.md` Section 7 tool catalog with the new tool.
* [ ] Pre-commit drift check extended to verify `reply_targets.source` enum matches across spec / `tools.py` / system prompt.
* [ ] V1.1+ deferred: programmatic scan of top-N replies under a target post via X API. Spec out in §28.20 so the MVP paste flow isn't a dead end.

**Personality lore registry (§28.21):**

* [ ] Settings → Growth Agent → Personality lore panel:

  * [ ] List of active lore (theme, description, invocation_count, last_invoked_at).
  * [ ] Add lore form (theme, description, optional `example_posts_json`).
  * [ ] Toggle `is_active`, reorder by priority, edit theme/description.
  * [ ] "Over-relied on" yellow banner per row when `invocation_count > personality_lore_overuse_threshold` AND `last_invoked_at_utc > now() - 30 days`.
* [ ] Extend `app/agent/prompt_builder.py`:

  * [ ] Splice top-N active lore rows (default `personality_lore_splice_count = 5`, ordered by priority asc) into §28.3 Section 5 (Voice samples), AFTER the voice samples — render as a compact bullet list: `- {theme}: {description}` plus a one-line "(last invoked {relative_time})" suffix.
  * [ ] No splice if zero active rows (silent, no banner).
* [ ] Wire `invocation_count` and `last_invoked_at_utc` updates: when an agent draft is saved with `content_type = 'personality'`, the orchestrator scans the draft text against active lore `theme`s + `description` keywords. For each match, increment `invocation_count` and set `last_invoked_at_utc = now()`. Matches are fuzzy (case-insensitive substring of theme name in draft text OR exact `description` keyword tokens in draft text); over-counting is acceptable, under-counting is not.
* [ ] Agent CANNOT write to `personality_lore`. Verified by registry inspection at startup: no tool entry references the table.
* [ ] Tests: splice with zero / one / five lore rows; over-reliance banner triggers on count + recency; agent-write attempt fails (no tool exposes the table).

**QA across the pack:**

* [ ] All existing tests pass (target ≥210 by end of Phase 5.9).
* [ ] Ruff clean across all new modules.
* [ ] Boot smoke: Streamlit boots; Settings → Niche panel renders empty-state CTA when no niche defined; agent refuses to draft until set; once set, drafting works.
* [ ] End-to-end: from a clean state, set niche → save draft post with `content_type = personality` → assert orchestrator scans lore + updates counters; save draft reply against a forced-sounding text → assert reply-quality lint fails + IWH increment; check velocity panel suppresses projections at low delta; paste a replier pool → assert candidates land with `source = 'replier_under_thread'`.

**Documentation:**

* [ ] Update `docs/IMPLEMENTATION_STATUS.md` with Phase 5.9 features + any deferred behaviors (V1.1+ programmatic replier scan, V1.1+ `v_content_type_x_pillar_performance` cross-pivot).
* [ ] README addition: a short Phase 5.9 section describing the V/G/P/P axis and the niche definition as the agent's two new identity anchors.

### Phase 5.10 — Strategic Analysis Pack (see §28.22 through §28.25 for full spec)

Four features distilled from CreatorOS's strategic-analysis surfaces, ported into XGrowth's discipline. Brain Dump (capture-first), Coach (citation-allowlisted advice), Account Researcher (target-account analysis), Profile Audit (quarterly comprehensive review). The goal of this phase is to close the consolidation gap — after Phase 5.10, the workflows that previously required jumping to CreatorOS for "ideation," "advice," "research," or "review" all live in XGrowth.

**Migration:**

* [ ] Migration `migrations/013_strategic_analysis.sql`:

  * [ ] Create `brain_dumps` table per §10 schema.
  * [ ] Create `account_research_reports` table per §10 schema. FK `linked_reply_target_id` ON DELETE SET NULL.
  * [ ] Create `profile_audits` table per §10 schema. FK `pinned_post_id` ON DELETE SET NULL; FK `active_voice_profile_id` ON DELETE SET NULL.
  * [ ] Add column `agent_messages.evidence_citations_json` (text nullable) per §10. Backfill existing rows to NULL. (Note: `agent_messages.confidence_label` was already added in Phase 5.8 migration 011; this migration only adds `evidence_citations_json`.)
  * [ ] Add settings rows: `coach_refuse_without_evidence = true`, `coach_citation_strip_log_threshold = 3` (log a Settings-visible "strip rate high" banner when avg strips/message > this over the last 20 messages), `brain_dump_max_candidate_drafts = 5`, `profile_audit_recent_posts_window_days = 30`, `profile_audit_cadence_reminder_days = 90`. Documented `note` per row.

**Brain Dump (§28.22):**

* [ ] Implement `app/agent/brain_dump.py`:

  * [ ] `process(brain_dump_id) -> BrainDumpResult` — reads `raw_text`, calls Claude with `config/brain_dump_prompt.md` (returns structured `{clarifying_questions: [str], candidate_drafts: [{text, content_type, pillar, audience, cta, rationale}]}` JSON), writes results back to the row.
  * [ ] Idempotent retry: re-running on the same `brain_dump_id` overwrites previous results in-place (no duplicate rows).
  * [ ] Failure → `status = failed`; preserves `raw_text`; surfaces in UI with Retry button.
* [ ] Create `config/brain_dump_prompt.md` — single-shot structured-output prompt. Returns at most `brain_dump_max_candidate_drafts` drafts. Each draft carries full content-type axis metadata per §28.17.
* [ ] New agent tool `#18 process_brain_dump(brain_dump_id: int)` — exposed to the agent for chat-driven invocation ("process my last brain dump"). Click-handler path in §14.9 also calls the same backend function.
* [ ] Build §14.9 Brain Dump view per the spec: textarea + Process button, past dumps sidebar, processing UI, candidate-drafts panel with per-draft "Send to drafts" button (calls `_save_draft_post`/`_save_draft_reply` with candidate metadata; runs full Phase 5.8 pipeline downstream), annotation textarea.
* [ ] Wire `brain_dumps.status` transitions: `unprocessed` → `processing` → `processed`/`failed`. Status persisted on every transition.
* [ ] Tests: round-trip of a synthetic dump → assert structured candidate drafts returned, each with valid `content_type`; promotion to draft fires the full pipeline including IWH + content-type validation.

**Coach with citation allowlist (§28.23):**

* [ ] Implement `app/agent/coach.py`:

  * [ ] `extract_citations(message_text: str) -> list[Citation]` — regex-extract citations in the form `〔post 142〕`, `〔v_lane_performance row build/icp/value〕`, `〔experiment 4〕`, etc. (define the citation-format spec in the module docstring with examples).
  * [ ] `validate_against_allowlist(citations: list[Citation]) -> tuple[list[Citation], list[StrippedCitation]]` — for each citation, verify the referenced record exists in the DB. Supported `record_type`s: `post` (id check against `posts`), `view_row` (well-formed view+filter), `experiment` (id check), `weekly_review`, `monthly_review`, `agent_draft`. Unknown record types are stripped with reason `unsupported_record_type`.
  * [ ] `enforce(message_text: str) -> tuple[str, list[Citation], list[StrippedCitation]]` — orchestrates extraction + validation + (optionally) rewrites the message text to remove the stripped citation markers. Returns `(clean_text, surviving_citations, stripped_citations)`.
* [ ] Wire into `app/agent/session.py`: when the message originates from the Coach view (mode flag carried through the session), run `enforce(...)` on every assistant message before persistence. Persist surviving citations to `agent_messages.evidence_citations_json`; log stripped count to `agent_tool_calls.notes` of the parent tool call.
* [ ] Build §14.10 Coach view per the spec: same conversation infra as §14.8 Agent Chat with mode flag, citation chips inline, stripped-citation banner, confidence-label chips, refuse-without-evidence behavior.
* [ ] Extend `coach_refuse_without_evidence = true` enforcement: when ON, if `enforce(...)` returns zero surviving citations AND the message contains analytical claims (regex per `app/agent/confidence_patterns.py`), the orchestrator replaces the message with the canonical refusal `"I don't have data in your dashboard to answer this honestly. {gap_description}"` BEFORE persistence.
* [ ] Update `config/agent_system_prompt.md` with a new Section 9 (Coach mode) — gated by the mode flag, instructs the coach on the citation discipline.
* [ ] Tests: synthetic coach response with valid + invalid citations → assert invalid stripped + count logged; refuse-without-evidence toggle ON + no citations → refusal message; OFF + no citations → message passes through with whatever confidence labels the agent emitted.

**Account Researcher (§28.24):**

* [ ] Implement `app/agent/account_research.py`:

  * [ ] `analyze(target_handle, target_bio_text, target_recent_posts_text, daniel_niche_problem, daniel_niche_person) -> AccountResearchAnalysis` — single Claude call against `config/account_research_prompt.md`. Returns the structured `analysis_json` per §10 schema. External content wrapped in `--- BEGIN_UNTRUSTED_DATA ... ---` markers per the §28.2 prompt-injection-defense convention.
  * [ ] Persistence wrapper that writes to `account_research_reports`.
* [ ] Create `config/account_research_prompt.md` with the structured-output prompt + the `BEGIN_UNTRUSTED_DATA` wrap convention.
* [ ] New agent tool `#19 analyze_account(target_handle, target_bio_text, target_recent_posts_text)` — exposed for chat invocation ("research @target for me").
* [ ] Add Account Researcher surface adjacent to §29.7 Reply Target Queue:

  * [ ] Form: target handle, bio paste, recent posts paste (one per `---`).
  * [ ] Submit → `analyze_account` → results display with the full `analysis_json` schema rendered.
  * [ ] "Generate reply target from this research" button — creates a `reply_targets` row prefilled with the research's recommended entry topics, links via `account_research_reports.linked_reply_target_id`.
* [ ] Past research sidebar in the new tab — list by `target_handle`, newest first. Comparison view when ≥2 reports exist for the same handle (side-by-side diff of positioning + posting patterns over time).

> **Phase 5.10 implementation divergence (documented 2026-05-22 per P510R-18):** the Account Researcher ships as a sibling Streamlit page (`app/pages/13_Account_Researcher.py`) rather than an `st.tabs()` container inside the 600+-line `10_Reply_Target_Queue.py`. Same sidebar position, simpler routing, no large-page restructure. The §29.7 ↔ §28.24 link is fully preserved via the bidirectional `account_research_reports.linked_reply_target_id` column — "Generate reply target from this research" inserts a row into the Queue and stamps the back-reference, so Daniel can navigate in either direction. Future Phase 5.11+ work may revisit the consolidation; until then, treat the sibling-page shape as the canonical surface.
* [ ] Tests: round-trip a synthetic target → assert structured analysis returned; linkage to `reply_targets` creates valid row.

**Profile Audit (§28.25):**

* [ ] Implement `app/agent/profile_audit.py`:

  * [ ] `audit(bio_text, pinned_post_text, recent_post_ids, active_voice_profile_id, niche_problem, niche_person) -> ProfileAuditAnalysis` — single Claude call against `config/profile_audit_prompt.md`. Returns the structured `audit_json` per §10 schema.
  * [ ] Persistence wrapper that writes to `profile_audits` with all snapshot columns populated.
* [ ] Create `config/profile_audit_prompt.md` with the structured-output prompt. Read scope explicit: `posts.text` only for `recent_post_ids`; never `stir_testers` or `stir_conversion_events.qualitative_feedback`.
* [ ] New agent tool `#20 audit_profile(bio_text, pinned_post_text, recent_post_window_days)` — exposed for chat invocation ("audit my profile").
* [ ] Settings → Growth Agent → Profile Audit panel:

  * [ ] "Last audit: N days ago" header (or "No audits yet" empty state).
  * [ ] "Run profile audit now" button → opens form prefilled with current bio + niche + active voice profile; Daniel pastes pinned-post text + (optional) recent-post window override.
  * [ ] Past audits table: `audited_at_utc`, `overall_consistency_score`, top-three-actions excerpt; expand → full `audit_json` rendering.
  * [ ] Compare-to-previous diff view: when ≥2 audits exist, side-by-side of scores + actions to show what shifted.
  * [ ] Cadence reminder: when `now() - last_audit > profile_audit_cadence_reminder_days` (default 90), surface a yellow banner; doesn't auto-run.
* [ ] Tests: round-trip a synthetic profile state → assert structured audit returned with all required fields; diff view renders correctly between two seeded audits.

**QA across the pack:**

* [ ] All existing tests pass (target ≥225 by end of Phase 5.10).
* [ ] Ruff clean across all new modules.
* [ ] Boot smoke: §14.9 Brain Dump and §14.10 Coach views render; Settings → Profile Audit panel renders empty-state CTA.
* [ ] End-to-end: capture a brain dump → process → promote a candidate to a draft → assert full Phase 5.8 pipeline ran (IWH, content-type, scorer, repetition guard). Ask the Coach an analytical question → assert citation extraction + stripping + survivors persisted. Run an account researcher pass → assert report saved + reply-target link created. Run a profile audit → assert row saved + diff view works against a seeded prior audit.

**Documentation:**

* [ ] Update `docs/IMPLEMENTATION_STATUS.md` with Phase 5.10 features.
* [ ] README addition: short Phase 5.10 section — what Brain Dump, Coach, Account Researcher, and Profile Audit each do; how this closes the CreatorOS consolidation gap.

### Phase 5.11 — Growth Layer + Quality-of-Life Pack (see §28.26 through §28.30 for full spec)

Five features: Campaigns (with items + retrospective), Monthly AI reviews (alongside weekly), Content Calendar (visual planning grid), Inspiration Library (saved posts + 7 transform modes + deterministic plagiarism guard), and a comprehensive audit log table. Three new top-level views (§14.11, §14.12, §14.13), one extension to §14.6, six new tables, one new computed view. After Phase 5.11, the consolidation surface is wide enough that the only remaining CreatorOS capability is blogs (deferred to Phase 6 with explicit scope rewrite).

**Migration:**

* [ ] Migration `migrations/015_growth_layer_qol.sql`:

  * [ ] Create `campaigns` table per §10 schema. FK `parent_experiment_id` ON DELETE SET NULL.
  * [ ] Create `campaign_items` table per §10 schema. FK `campaign_id` ON DELETE CASCADE; other FKs ON DELETE SET NULL.
  * [ ] Create `monthly_reviews` table per §10 schema. Unique on `iso_month`.
  * [ ] Create `saved_inspiration_posts` table per §10 schema. Unique on `source_text_hash`.
  * [ ] Create `inspiration_transforms` table per §10 schema. FK `saved_inspiration_id` ON DELETE CASCADE.
  * [ ] Create `audit_logs` table per §10 schema. Append-only; no ALTER paths for UPDATE/DELETE in this migration.
  * [ ] Create `v_campaign_progress` view per §11.
  * [ ] Add settings rows: `inspiration_plagiarism_jaccard_high_threshold = 0.65`, `inspiration_plagiarism_jaccard_medium_threshold = 0.35`, `inspiration_plagiarism_ngram_high_threshold = 8`, `inspiration_plagiarism_ngram_medium_threshold = 5`, `monthly_review_auto_draft_enabled = false`, `audit_log_retention_days = 365`, `calendar_default_view = 'week'`. Documented `note` per row.
  * [ ] Log a `migration_applied_015` row to `audit_logs` as the migration's final step (audit log writes-through from day one). (Slot 014 was taken by `014_velocity_view_expose_noise_floor.sql` during Phase 5.9; spec corrected on 2026-05-22 before Phase 5.11 work began.)

**Campaigns (§28.26):**

* [ ] Implement `app/agent/campaigns.py`:

  * [ ] `create_campaign(name, theme, hypothesis, start_date, end_date, success_criteria_json)` with schema validation: ≥1 distribution metric AND ≥1 validation metric in `success_criteria_json`. Raises if either is missing.
  * [ ] `add_item(campaign_id, item_type, planned_for_date, **fk_kwargs)` — supports all five item types.
  * [ ] `transition_item_status(item_id, new_status)` — enforces valid transitions (`planned → drafted → shipped` etc.); writes `completed_at_utc` on terminal states.
  * [ ] `complete_campaign(campaign_id, success_criteria_actuals, lesson, counterfactual_note)` — blocks if any actuals missing; copies the lesson to the user's current weekly or monthly review (Daniel picks at completion time).
  * [ ] `analyze_progress(campaign_id) -> dict` — reads `v_campaign_progress` + item statuses + linked posts' metrics; returns structured analysis for the agent.
* [ ] New agent tool `#21 analyze_campaign_progress(campaign_id)` — read-only, used by the "Ask the agent for ideas" affordance.
* [ ] Build §14.12 Campaigns view per the spec: status sections, expandable campaign details, "+ new campaign" form with success-criteria validation, item management (add/edit/remove/reorder), "Ask the agent for ideas" integration, retrospective form on completion.
* [ ] Wire `campaign_items.status` transitions into existing draft/publish handlers: when an `agent_drafts` row is created and an item links to it, set the item to `drafted`; when a `posts` row's `published_to_x_at` populates, set linked items to `shipped`.
* [ ] Tests: cannot save campaign without dual-stream success criteria; transition `completed` requires all retro fields; `v_campaign_progress` math is correct on a seeded campaign.

**Monthly AI reviews (§28.27):**

* [ ] Implement `app/agent/monthly_review.py`:

  * [ ] `compute_auto_filled_fields(iso_month) -> dict` — mirror of weekly auto-fill, with `strongest_content_type` / `weakest_content_type` added per §28.17, and `campaigns_completed_json` populated.
  * [ ] Same export-blocked rule: `counterfactual_note` required, `confidence_label = speculation` blocks export.
* [ ] Extend §14.6 view with cadence toggle (Weekly / Monthly). Each cadence reads/writes its own table; UI shell shared.
* [ ] New agent tool `#22 draft_monthly_review_section(section_name, iso_month)` — mirror of the weekly tool. Emits `<confidence>` tags per §28.14.
* [ ] Tests: cadence toggle persists view state; monthly auto-fill correctly computes campaigns completed in the month; export blocker fires on speculation label.

**Content Calendar (§28.28):**

* [ ] Implement `app/agent/calendar.py`:

  * [ ] `get_calendar_window(start_date, end_date) -> list[CalendarCell]` — returns shipped posts + drafted posts + planned campaign items in the window, AM/PM-bucketed.
  * [ ] Filter support (pillar, content_type, campaign).
* [ ] Build §14.11 Content Calendar view per the spec: week / two-week / month toggle, AM/PM grid, click-through to source rows, "+ schedule slot" inline form, active-campaigns strip.
* [ ] Wire the "+ schedule slot" to create either a `campaign_items` row (if Daniel picks "campaign-scoped") or a standalone draft (if "ad-hoc").
* [ ] Integration with §19 item 11 (Scheduled publish drafts): a scheduled draft renders in the calendar at its scheduled time.
* [ ] Tests: cells render correctly for all four provenances; week navigation keeps the AM/PM grid stable; filters persist across navigation.

**Inspiration Library (§28.29):**

* [ ] Implement `app/agent/inspiration.py`:

  * [ ] `TRANSFORM_MODES = ('structure', 'hook_pattern', 'counterpoint', 'original_version', 'voice_profile_version', 'expand', 'compress')`. Module docstring with one-paragraph definition each.
  * [ ] `transform(saved_inspiration_id, mode) -> InspirationTransformRow` — single Claude call against `config/inspiration_transform_prompt.md` (mode-parameterized). Returns `output_text` + `ai_reported_risk_label`.
  * [ ] `compute_plagiarism_risk(source_text, output_text) -> dict` — pure Python. Returns `{jaccard_similarity, longest_shared_ngram_length, deterministic_risk_label}`. Deterministic, unit-testable.
  * [ ] `final_risk(ai_label, deterministic_label) -> str` — returns `max(ai_label, deterministic_label)` using the ordering `low < medium < high`. AI cannot underreport.
* [ ] Create `config/inspiration_transform_prompt.md` — mode-parameterized structured-output prompt. External content wrapped per §28.2 convention.
* [ ] New agent tools `#23 transform_inspiration(saved_inspiration_id, mode)` and `#24 score_inspiration_plagiarism_risk(source_text, output_text)` (the second exposed as a pure read-only sanity-check tool the agent can call independently).
* [ ] Build §14.13 Inspiration Library view per the spec: + save inspiration form, sidebar with tag filter, transform panel with per-mode buttons, plagiarism risk chips, high-risk gating with override + audit log, "Send to drafts" inheriting context.
* [ ] Tests: deterministic plagiarism is correctly computed (golden inputs); final risk is correctly the max; high-risk override is audit-logged; duplicate save is rejected.

**Audit logs (§28.30):**

* [ ] Implement `app/agent/audit_log.py`:

  * [ ] `log(event_category, event_type, target_type=None, target_id=None, details=None, success=True, error_message=None)` — single canonical write-through.
  * [ ] `query(category=None, since=None, target=None) -> list[AuditRow]` — for the Settings viewer.
* [ ] Wire `audit_log.log(...)` from every state-changing path:

  * [ ] OAuth connect/disconnect (Phase 5.5 X client).
  * [ ] Every publish attempt (in addition to existing `agent_tool_calls` row — `audit_logs` is the canonical state-change record).
  * [ ] Every settings change (intercept the settings UPDATE path).
  * [ ] Every export (CSV, Markdown, JSON).
  * [ ] Every data deletion / correction (preserve `snapshot_of_deleted_row` in `details_json`).
  * [ ] Every backup run (existing `scripts/backup_db.py`).
  * [ ] Every migration applied.
  * [ ] Every inspiration plagiarism override.
* [ ] Add Settings → Audit log viewer panel: filter by category + date range + target; expand row for full `details_json`.
* [ ] Implement retention: when `audit_log_retention_days > 0`, a daily job (in `scripts/backup_db.py` or a new `scripts/prune_audit_log.py`) deletes rows older than the retention window; the deletion itself audit-logs as `event_category = 'admin', event_type = 'audit_logs_pruned'` with the pruned count in `details_json`.
* [ ] Tests: every state-change path produces an audit row; pruning works correctly and self-audits; the Settings viewer renders correctly with filters.

**QA across the pack:**

* [ ] All existing tests pass (target ≥250 by end of Phase 5.11).
* [ ] Ruff clean across all new modules.
* [ ] Boot smoke: §14.11 Calendar, §14.12 Campaigns, §14.13 Inspiration Library all render; §14.6 cadence toggle works; Settings → Audit log viewer renders with seeded events.
* [ ] End-to-end: create a campaign with dual-stream success criteria → add three items → ship two → assert `v_campaign_progress` reflects 67% shipped → complete the campaign → assert lesson lands in the chosen review. Save an inspiration → run all seven transforms → assert high-risk transform blocks "Send to drafts" until override → override → assert audit row. Run a settings change → assert `audit_logs` row with old/new values in `details_json`.

**Documentation:**

* [ ] Append a new phase block to `docs/index.html` for Phase 5.11 per the CLAUDE.md "Implementation status doc" instructions (do NOT append to `docs/IMPLEMENTATION_STATUS.md` — it's frozen at Phase 5.8).
* [ ] README addition: short Phase 5.11 section — what Campaigns, Monthly Reviews, Calendar, Inspiration Library, and Audit Logs each do; how this leaves only blogs (Phase 6) as the final consolidation step.

### Phase 6 — Long-form blogs (see §28.31 through §28.34 for full spec)

Adds long-form blog authoring as a first-class production surface. Five workstreams: schema (`blogs`/`blog_versions`/`blog_exports`/`blog_to_post_links` + `v_blog_pipeline`), §14.14 Blogs index, §14.15 Blog Editor (three-panel: outline / body / agent + version history), blog-drafting agent tools (outline, draft, edit suggestions, SEO metadata), exports (Markdown / HTML / JSON / MDX), bidirectional X↔blog repurposing. Unified identity layer: same niche + voice profile + voice samples + personality lore feed blog drafting as feed X drafting.

**Scope reminders (carried from §0 + §7.1 + §1):**

* Blogs are written locally, exported to disk, published externally on Daniel's blog platform. The app NEVER publishes a blog anywhere.
* No multi-user, no cloud sync, no blog-platform integrations (no Substack publish API, no Ghost API, no WordPress API). Refuse if asked.
* `external_url` and `external_published_at` are MANUAL fields — Daniel updates them after he publishes externally; the app doesn't fetch them.

**Migration:**

* [ ] Migration `migrations/016_blogs.sql` (slot 015 was consumed by `015_growth_layer_qol.sql` during Phase 5.11; spec corrected on 2026-05-22 before Phase 6 work began):

  * [ ] Create `blogs` table per §10 schema. Unique `slug` index. Status enum CHECK constraint `IN ('idea', 'outlining', 'drafting', 'editing', 'ready', 'exported', 'published_externally', 'archived')`. FK `voice_profile_id_at_draft` ON DELETE SET NULL.
  * [ ] Create `blog_versions` table per §10 schema. FK `blog_id` ON DELETE CASCADE. Unique `(blog_id, version_number)`. Partial unique index on `(blog_id) where is_current_for_blog = true`. FK `agent_message_id` ON DELETE SET NULL.
  * [ ] Create `blog_exports` table per §10 schema. FK `blog_id` ON DELETE CASCADE. FK `blog_version_id` ON DELETE SET NULL. Format CHECK `IN ('markdown', 'html', 'json', 'mdx')`.
  * [ ] Create `blog_to_post_links` table per §10 schema. Both FKs ON DELETE CASCADE. Unique `(blog_id, post_id, direction)`. FK `agent_message_id` ON DELETE SET NULL.
  * [ ] Create `v_blog_pipeline` view per §11.
  * [ ] Add settings rows: `blog_stale_status_warning_days = 21`, `blog_default_target_length_words = 1500`, `blog_export_default_directory = 'data/blog_exports/'`, `blog_repurposing_plagiarism_check_enabled = true`, `blog_agent_max_draft_iterations = 3` (informational; per §28.32 calibration). Documented `note` per row.
  * [ ] Log a `migration_applied_016` row to `audit_logs` per §28.30.

**Schema discipline (§28.31):**

* [ ] Implement `app/agent/blogs.py`:

  * [ ] `create_blog(title, pillar=None, audience=None, target_length_words=None) -> Blog` — generates `slug` from title (lowercase, kebab-case, ASCII-only, suffixed with `-{id}` on collision), inserts row with `status = 'idea'`, version 1 with `body_markdown = ''`.
  * [ ] `save_blog(blog_id, body_markdown, outline_markdown=None, title=None, status=None, created_by, agent_message_id=None, agent_action=None, daniel_revision_note=None) -> BlogVersion | None` — single transaction: writes `blogs.current_body_markdown` + appended fields, then appends `blog_versions` row, then demotes prior `is_current_for_blog` and promotes new. No-op detection: skip version row if `body_text_hash` AND `outline_markdown_at_version` AND `title_at_version` AND `status_at_version` all match the current version. Returns the new version row OR None on no-op.
  * [ ] `transition_status(blog_id, new_status) -> bool` — validates the transition against the state machine (defined in module docstring); writes a version row capturing the status change; rejects illegal transitions with a structured error.
  * [ ] `revert_to_version(blog_id, version_id, daniel_revision_note) -> BlogVersion` — creates a NEW version row with the older body but a new `version_number`; sets `is_current_for_blog`; logs `daniel_revision_note = 'reverted to version N'` AND the user's optional note.
* [ ] State machine (define in module docstring + enforce in `transition_status`):

  ```
  idea          → outlining | archived
  outlining     → drafting | idea | archived
  drafting      → editing | outlining | archived
  editing       → ready | drafting | archived
  ready         → exported | editing | archived
  exported      → published_externally | ready | archived
  published_externally → archived
  archived      → (terminal; no forward transitions)
  ```

* [ ] Tests: no-op save doesn't create a version row; illegal status transitions rejected; revert creates forward-moving history; concurrent saves (rare in single-user but possible mid-streaming) handle the version_number monotonic increment correctly.

**Blog drafting agent tools (§28.32):**

* [ ] Implement `app/agent/blog_drafting.py`:

  * [ ] `outline_blog(blog_id, daniel_notes=None) -> OutlineResult` — single Claude call against `config/blog_outline_prompt.md`. Reads `blogs.title`, `pillar`, `audience`, `notes`, active niche, voice profile, voice samples, personality lore. Returns structured outline (Markdown headings + one-sentence-per-section). Writes via `save_blog(... agent_action='outline')`.
  * [ ] `draft_blog(blog_id, target_length_words=None) -> DraftResult` — single Claude call against `config/blog_draft_prompt.md`. Reads outline + same identity context. Returns full draft body Markdown. Writes via `save_blog(... agent_action='draft')`.
  * [ ] `suggest_blog_edits(blog_id) -> EditSuggestions` — single Claude call against `config/blog_edit_suggestions_prompt.md`. Reads current body. Returns structured `[{paragraph_anchor: str, suggested_replacement: str, rationale: str, confidence_label: str}]`. Does NOT auto-apply; UI surfaces with Accept / Reject / Modify.
  * [ ] `generate_blog_seo_metadata(blog_id) -> SeoMetadata` — single Claude call against `config/blog_seo_prompt.md`. Returns `{seo_title, seo_description, seo_tags}`. Writes to `blogs.seo_title` etc. directly (no version row — SEO metadata is sidecar, not content).
* [ ] New agent tools `#25 outline_blog`, `#26 draft_blog`, `#27 suggest_blog_edits`, `#28 generate_blog_seo_metadata` — all in the registered agent tool table (NOT internal-only).
* [ ] All four tools emit `<confidence>` tags per §28.14; the orchestrator parses + persists the dominant label on `blog_versions.confidence_label_at_version`.
* [ ] Tests: round-trip a synthetic blog idea through outline → draft → edit suggestions → SEO; assert version rows created with correct `agent_action` and `confidence_label_at_version`; assert SEO writes don't create version rows.

**Blog editor (§14.15) wiring:**

* [ ] Three-panel layout: outline (left, editable Markdown), body (center, editable Markdown), agent panel (right). All three live-bound to `st.session_state` per Streamlit discipline.
* [ ] Identity readout in agent panel (voice profile, niche, lore count) — bound to a fresh DB read each rerun (no cached state).
* [ ] Four agent action buttons (Outline / Draft / Suggest edits / SEO) wired to the four tools above; each click runs the tool synchronously, surfaces the result, and on Accept writes via `save_blog(...)`.
* [ ] Version history list — clicking a version opens a side-by-side diff (use `difflib.unified_diff` or similar). Revert button creates a new version per `revert_to_version(...)`.
* [ ] Linked posts list — read from `blog_to_post_links`; "Add link" form picks a `posts.id` + `relationship_kind`.
* [ ] Status selector — dropdown bound to `transition_status(...)`; illegal transitions surface inline error.
* [ ] Footer actions: Save (disabled when no changes), Discard, Export ▾, Repurpose to X ▾.

**Blog exports (§28.33):**

* [ ] Implement `app/agent/blog_exports.py`:

  * [ ] `export(blog_id, format, target_path, include_seo_metadata=True, include_repurposing_links=False) -> BlogExport` — atomic: render content according to format, write file to `target_path`, compute `content_sha256`, insert `blog_exports` row, log to `audit_logs`. If any step fails, the entire op fails — no partial state.
  * [ ] Format renderers:
    * `markdown` — body Markdown with optional YAML frontmatter (when `include_seo_metadata = True`): `title`, `description`, `tags`, `slug`, `pillar`, `audience`, `created_at_utc`.
    * `html` — Markdown rendered to HTML via `markdown-it-py` or similar; wrapped in minimal `<html><head>` (with `<meta name="description">`, `<meta name="keywords">` when SEO included) `<body>`.
    * `json` — `{title, slug, status, body_markdown, body_html, seo: {...}, pillar, audience, created_at_utc, exported_at_utc, version_number}`.
    * `mdx` — same as Markdown but with MDX-compatible frontmatter (e.g. `export const meta = {...}`).
  * [ ] "Repurposing notes" footer — appended when `include_repurposing_links = True`. Compact rendering of `blog_to_post_links` rows: which X posts this blog is linked to, with permalinks.
* [ ] Export dialog UI in §14.15: format dropdown, target-path picker with `blog_export_default_directory` prefill, two checkboxes (SEO, repurposing notes), confirm button.
* [ ] Transition: blog with `status = 'ready'` exported → status becomes `exported`. Blog with `status = 'exported'` re-exported → status stays `exported`. Daniel transitions to `published_externally` MANUALLY via the status selector once he actually publishes.
* [ ] Tests: each format renders correctly; SHA-256 matches written file contents; re-export of a blog appends a second `blog_exports` row; failed export (e.g. target_path not writable) leaves no `blog_exports` row.

**X ↔ blog repurposing (§28.34):**

* [ ] Implement `app/agent/blog_repurposing.py`:

  * [ ] `repurpose_blog_to_x(blog_id, mode) -> RepurposingResult` — modes: `thread_from_sections` (one X post per major heading), `single_post_summary` (one X post summarizing the whole), `teaser_with_link` (a hook + a teaser + the `external_url` if set). Single Claude call per mode; outputs flow into the regular drafts pipeline as `agent_drafts` rows.
  * [ ] `repurpose_x_to_blog_idea(post_id) -> BlogIdeaResult` — single Claude call that takes an X post and produces a blog *idea* (title + outline + content type framing). Inserts a new `blogs` row with `status = 'idea'`, links via `blog_to_post_links(direction='post_to_blog', relationship_kind='derived_outline')`.
  * [ ] Plagiarism guard: every repurposing output runs through `app/agent/inspiration.py::compute_plagiarism_risk` (existing Phase 5.11 deterministic floor) against the source. High-risk outputs block the drafts-pipeline insertion until Daniel overrides (same UX as §14.13 high-risk inspiration transforms).
* [ ] New agent tools `#29 repurpose_blog_to_x(blog_id, mode)` and `#30 repurpose_x_to_blog_idea(post_id)` — registered agent tools.
* [ ] Wiring in §14.15: "Repurpose to X ▾" sub-menu with the three modes. Each click runs the tool, then opens the resulting `agent_drafts` row(s) in §14.8 Agent Chat for review.
* [ ] Wiring in §14.4 Content Performance: per-post "Repurpose to blog idea" button → calls `repurpose_x_to_blog_idea(post_id)`, opens the resulting `blogs` row in §14.15.
* [ ] Tests: round-trip post → blog idea → blog draft; assert `blog_to_post_links` linkage in both directions; assert plagiarism guard fires on high-overlap repurposing and blocks promotion until override.

**QA across the pack:**

* [ ] All existing tests pass (target ≥285 by end of Phase 6).
* [ ] Ruff clean across all new modules.
* [ ] Boot smoke: §14.14 Blogs index and §14.15 Blog Editor render; agent panel identity readout shows correct values after a voice-profile regenerate.
* [ ] End-to-end: create blog → outline (agent) → draft (agent) → edit + save → status `editing` → suggest edits (agent) → accept one → status `ready` → export Markdown to tmp dir → assert file contents + `blog_exports` row + audit log row → status auto-`exported`. Repurpose-to-X-thread → assert plagiarism guard runs + 4-post thread lands in `agent_drafts` → assert Phase 5.8 pipeline fired on each draft. Revert to v.2 → assert forward-moving history (v.5 carries v.2's body).

**Documentation:**

* [ ] Append a new Phase 6 block to `docs/index.html` per the CLAUDE.md "Implementation status doc" workflow.
* [ ] README addition: short Phase 6 section — what the Blogs surface adds; how it closes the final CreatorOS consolidation gap; the explicit reminder that the app NEVER publishes blogs (Daniel publishes externally).
* [ ] Spec rename consideration: a one-line "consider renaming to Distribution Dashboard / Personal Distribution OS" note in `docs/index.html` Phase 6 block, flagging the decision for Daniel without committing to it.

### Phase 7 — V1.1: Data collection (deferred from MVP, previously labeled Phase 6 before the Phase 6 Blogs renumber)

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
15. **Niche must be defined before drafting — orchestrator-gated.** The agent refuses to call `save_draft_post` or `save_draft_reply` when either `niche_problem` or `niche_person` setting is empty. The refusal happens at the orchestrator level (`app/agent/session.py`), NOT in the agent's prompt — Daniel cannot tell the agent to "skip the niche check," and a prompt-injected reply target cannot bypass it. The agent emits a structured "niche not defined" message back to the conversation that links to Settings → Growth Agent → Niche. The reason this is a non-negotiable rule rather than a soft warning: niche identity is the load-bearing assertion that ties every other agent behavior (voice, content type, reply targets) to a coherent strategy. Drafting without it is the agent stochastically generating creator-flavored noise. See §28.16 for the Settings panel + system-prompt splice details.

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
Pillars (topic): stir, build, self
Audiences: icp, other
CTAs: ask, none
Content types (purpose, per §28.17): value, growth, personality, proof

Pillar is *what the post is about*. Content type is *what the post is for*.
A `build × value` post teaches something about building.
A `build × personality` post is a behind-the-scenes from building.
A `stir × growth` post is a polarizing-but-genuine opinion about cooking with AI.
A `self × proof` post is a milestone or credibility marker.

The orchestrator requires content_type on every saved draft; pillar/audience/CTA
remain required as before. The two axes are orthogonal — do not collapse them.

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

**Voice samples + voice profile are further complemented by personality lore (§28.21).** Lore is the registry of recurring jokes, running bits, and personal motifs that the agent should pick up on when drafting `content_type = personality` posts. The three layers stack: voice samples give the agent example posts to model, voice profile gives it structural cadence/vocabulary, personality lore gives it recurring narrative threads to build on. See §28.21.

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

### 28.16 Structured niche definition

A niche is (problem you solve, person you solve it for). Distilled from Jacob Edmunds's "first 1k followers" framework (May 2026) and reconciled with XGrowth's existing identity discipline.

Two settings rows are load-bearing: `niche_problem` (one sentence: the problem) and `niche_person` (one sentence: the person). They appear together in §28.3 Section 1 (Identity) as a single load-bearing splice line: *"You help **{niche_person}** with **{niche_problem}**."*

**Why this is structural, not prose:**

The agent's existing Section 1 already names Stir, neuro-oncology, and ICP. That's *biography*. The niche fields are *positioning*. Biography says "here is who Daniel is"; niche positioning says "here is the precise audience-to-problem pairing the agent is optimizing for in this session." A loose biography drifts; a structured (problem, person) pair stays sharp. The agent reads both; they don't substitute for each other.

**Workflow:**

1. Daniel opens Settings → Growth Agent → Niche.
2. Two textareas: `niche_problem` and `niche_person`. Each one-sentence. Examples surfaced from the source video for first-time users (problem: "how to grow on X"; person: "educational creators"). Daniel writes his own.
3. "Test against bio" affordance: paste current X bio, click "Critique alignment." A single Haiku invocation against `config/niche_alignment_prompt.md` returns structured JSON: `{aligned: bool, gaps: [str], suggestions: [str]}`. The critique is read-only — the panel never edits the X bio itself. Suggestions are surfaced as a list Daniel can copy.
4. Empty-state CTA when either field is unset: "Your agent is in low-power mode — drafting is disabled until your niche is defined" (rule #15 enforcement).

**System prompt splice:**

- `app/agent/prompt_builder.py` reads both settings at build time.
- If BOTH are non-empty, splice line goes into §28.3 Section 1: `"You help **{niche_person}** solve **{niche_problem}**."` (verbatim, after the existing identity prose).
- If EITHER is empty, splice `"(niche not yet defined — drafting is disabled until Daniel fills Settings → Growth Agent → Niche)"` AND set the orchestrator's `niche_defined_flag = False` for that session. Orchestrator's `save_draft_*` handlers refuse with the canonical message.
- Pre-commit drift check extended (§28.3 build step) to verify the splice executes.

**Why this is rule #15 (hard gate) rather than soft warning:**

Niche identity is the load-bearing assertion that ties voice + content type + reply targets together. Drafting without it is the agent generating creator-flavored noise. Daniel's other gates (IWH, dark-pattern, repetition) check *quality*; this gate checks *whether the agent has any business drafting at all*. Same epistemological stance as §28.14 confidence labels: the structural enforcement makes the claim explicit rather than hoped-for.

### 28.17 Content type axis (V/G/P/P)

Every post and every agent draft carries a `content_type` enum: `value | growth | personality | proof | unspecified`. Orthogonal to pillar/audience/CTA. Distilled from Jacob Edmunds's V/G/P/P framework; reconciled with XGrowth's graduated-confidence discipline.

**Definitions (load-bearing — docstring'd in `app/agent/content_types.py`):**

| Type | What it does | Example for Daniel |
| --- | --- | --- |
| `value` | Teaches the reader how to do something. Specific, actionable, holds nothing back. | "Here's the exact prompt structure I use for kitchen-scanner item recognition." |
| `growth` | Aims at a broader audience: reacts to niche news, shares a polarizing-but-genuine opinion, starts a conversation. Distinct from `value` because the goal is reach via conversation, not knowledge transfer. | "Hot take: kitchen scanners that don't ground in nutrition data will all converge to the same bland LLM recipes." |
| `personality` | Humanizes. Behind-the-scenes, running jokes, the actual quirks of being Daniel. Pulls back the curtain. Pairs with `personality_lore` (§28.21). | "Day 3 of forgetting to put the rice on before the protein finishes — Cook Mode timing logic born from real grief." |
| `proof` | Builds credibility. Milestones, viral posts you wrote, testimonials, social proof. Distinguishes from `value` because anyone can copy `value`; only the original author can show `proof`. | "100 followers. Still pre-launch. The build-in-public bet is working faster than I expected." |
| `unspecified` | Backlog state for existing posts at Phase 5.9 install. Backfill default. NEVER assigned to new agent drafts — orchestrator rejects it. | (legacy rows only) |

**Why orthogonal to pillar/audience/CTA:**

Pillar is *topic* (stir / build / self). Content type is *purpose*. A `build × value × ask` post teaches how to build something and asks for input. A `build × personality × none` post is a behind-the-scenes from building. They share a pillar; they're not interchangeable. Collapsing the two axes (e.g., calling all personality posts `self`-pillar) loses information.

**Enforcement:**

- `posts.content_type` default `unspecified`; existing rows backfilled to it; no retro-classification.
- `agent_drafts.content_type` required-non-`unspecified` via orchestrator validation. The CHECK constraint permits `unspecified` (to allow backfill) but the orchestrator refuses to save a row with that value.
- §28.4 tools #4 and #5 (`save_draft_post`, `save_draft_reply`) gain a required `content_type` parameter.

**New tool #12 — `get_content_type_gaps(window_days: int = 7)`:**

Read-only. Returns counts per content type for the window: `{"value": int, "growth": int, "personality": int, "proof": int, "unspecified": int (counted but excluded from "under-represented" suggestion)}`. The agent uses this to suggest the under-represented type when Daniel asks "what should I post today" without specifying.

**§14.1 Today panel addition:**

A new line in the Today header: *"Today's content-type recommendation: `{under_represented_type}`"* with one-line rationale ("you've shipped 5 value posts this week, 0 personality"). Uses `content_type_recommendation_window_days` (default 7). When the spread is even (no clear under-represented type), the line reads "even spread — pick what you're moved by today."

**§14.4 Content Performance tab:**

New "Content type" tab showing `v_content_type_performance` with graduated-confidence labels (same logic as `v_lane_performance`). Daniel sees which *purpose* lands, not just which *topic*. Cross-pivot `v_content_type_x_pillar_performance` is V1.1+ deferred (density argument).

**Hard rule (carried from §13 hard rule #5):** the cadence advice ("post all four every day") from the source video is NOT enforced. The framework is for *classification + slice analysis*, not for daily-posting pressure. Some days a `value × build` is the right post; some days nothing's worth posting. The dashboard's job is to surface the gap, not to manufacture content.

### 28.18 Reply-quality lint

A second small-model lint pass on every reply draft, gated by `reply_quality_lint_enabled` (default `true`). Catches the specific failure mode the source video calls out: "people can tell when you're forcing a reply, when you're using AI, or when you're being selfish."

**Pipeline position:**

In `_save_draft_reply`:

1. IWH self-score preflight (rule #13).
2. Dark-pattern lint (rule #12).
3. **Reply-quality lint (this section).**
4. Pre-publish scorer (§28.11).
5. Repetition guard (§28.13).
6. `agent_drafts` insert.

Failure of step 3 is treated like failure of step 2: counts as a failed IWH revision, increments `iwh_attempt_index`, bounces back to the agent for revision. After `iwh_max_revision_attempts + 1` cumulative failures (across all gates), the orchestrator refuses to save.

**The prompt (one-shot Haiku call):**

```
You are reviewing a reply to an X post. Does this reply sound forced,
AI-generated, or selfishly self-promoting (would the original poster
find it annoying)?

Target post:
<target_post_text>

Proposed reply:
<reply_text>

Reply with exactly one of:
- "no, this is genuine and substantive" + one-line reasoning
- "yes, forced" + one-line reasoning
- "yes, AI-tasting" + one-line reasoning
- "yes, selfishly self-promoting" + one-line reasoning
```

**Persistence:**

- `agent_drafts.reply_quality_lint_passed` (boolean nullable). `true` on pass, `false` on fail, NULL when `draft_kind != reply` or the lint was disabled.
- Failure reason logged to `agent_tool_calls.notes`.

**Why this is a separate lint from §28.2 #12:**

Dark-pattern lint catches *manipulation* (fake urgency, fabricated social proof, engagement bait). Reply-quality lint catches *forced-ness* (the reply that's technically not manipulative but still reads as a hollow self-promo bolt-on). They overlap but are not redundant — a reply can be honest (no dark pattern) and substantive in topic but still annoy the original poster because the agent's only motive shows through.

**Toggle:** `reply_quality_lint_enabled = false` skips the call (useful for cost reasons in heavy-volume seasons). When disabled, `reply_quality_lint_passed` is written as `true` with a `agent_tool_calls.notes = "lint disabled"` audit trail.

### 28.19 Follower-velocity projection

The arithmetic from the source video, executed with XGrowth's noise-floor discipline. The video says "to hit 1k in 30 days from 50, you need 32/day"; XGrowth says "and only if your current 7-day velocity isn't in the noise floor — otherwise don't pretend to know."

**View:** `v_follower_velocity` (defined in §11).

**Hard rule:** when `abs(delta_7d) < velocity_projection_noise_floor_followers` (default 10, paralleling §13's display threshold), ALL projection columns return NULL and the UI shows "trend not yet measurable — projections suppressed." Do not display a precise date when the input is noise. This is non-negotiable; the calculation is trivially fakeable and Daniel's anti-precision-theater stance applies (§11 graduated confidence, §13 noise floor).

**§14.3 Progress velocity panel:**

Three states:

- **Noise floor (default at low follower count):** "Your 7-day delta is +3. That's in the noise floor — projections suppressed until you reach +10 or sustained 30-day velocity ≥ 0.5/day."
- **Measurable, positive velocity:** "Current pace: +X followers / day (7d). At this pace you'd reach `{current_milestone_target}` by `{date}`."
- **Date-target widget (always visible):** "To hit `{current_milestone_target}` by `{target_date}` (pick a date), you need +Y followers/day." Y is computed by the `daily_followers_needed_to_hit_milestone_by_date` helper. If milestone is met, widget shows "milestone met — pick the next one in Settings."

**New tool #13 — `get_velocity_projection()`:**

Read-only. Returns the entire `v_follower_velocity` row for the most recent snapshot, with the same null-on-noise-floor rule. Agent uses this to ground velocity-related questions ("at this pace, when do I hit 250?") in real data instead of guessing.

**Anti-feature carried from §13 + §5:**

- Never frame projection as a goal. The video does ("hit 1k in 30 days"); the dashboard does not. Projections are descriptive, not normative.
- Never use velocity to recommend changing tactics ("you need to post more"). The dashboard surfaces the math; Daniel decides what to do with it.

### 28.20 Replier-pool candidate discovery

The most novel tactical insight from the source video: niche-relevant audiences cluster in the *reply sections* of big accounts, not just in the accounts themselves. XGrowth's existing reply target discovery (§29) covers paste-URL and curated-account paths. This subsection adds the third path: replier-under-thread.

**MVP path (paste-driven, no scraping):**

1. Daniel opens Reply Target Queue (§29.7) → "Add replier pool" affordance.
2. Inputs:
   - `thread_url` — URL of the big account's post whose reply section Daniel is mining.
   - `replier_handles_or_excerpts` — pasted text. Either a list of @handles, or replier-text excerpts, or both (one per line).
   - `lookback_minutes` (default 60) — for the §29.3 timing-score sub-input (V1.1+ uses this; MVP records it for future calibration).
3. Click "Score" → calls new tool `#14 score_replier_pool(thread_url, replier_handles_or_excerpts_json, lookback_minutes)`.
4. Each candidate is scored on the existing §29.3 4-dim model PLUS a new dimension `thread_context_fit_score` (0-3, deterministic): how well the replier's text matches Daniel's `niche_person` description from §28.16.
5. Candidates land in `reply_targets` with `source = 'replier_under_thread'` and `agent_reasoning` populated with the per-dimension explanation.

**Why the new `thread_context_fit_score` dimension and not just `relevance_score`:**

§29.3's relevance score is "does this target post's topic match a Daniel pillar?" The replier-pool case is different: the replier *isn't* the original post; we're evaluating whether engaging *with the replier* is on-niche. That depends on the replier's text matching Daniel's `niche_person` definition, not the original post's pillar. Different question, different score.

**Schema change:**

- `reply_targets.source` enum (add value `replier_under_thread`). If `source` column doesn't yet exist (Phase 5.6 baseline may not have it), the migration adds the column with CHECK constraint `IN ('paste_url', 'agent_curated_account', 'replier_under_thread')` default `'paste_url'`; if it exists, ALTER to extend the enum.

**System prompt change:**

- §28.3 Section 7 (Tool catalog) gains the new tool.
- Pre-commit drift check extended to verify `reply_targets.source` enum matches across spec / `tools.py` / system prompt.

**V1.1+ deferred path:**

Programmatic scan of top-N replies under a target post via X API. Drops the paste step; otherwise identical. Spec'd here so the MVP paste flow isn't a dead end. When V1.1+ lands, the same tool signature gains an optional `auto_scan: bool = false` parameter that triggers the programmatic path.

**Anti-feature (carried from §5 + §29):**

- No scraping. The paste flow is intentional — Daniel manually transcribes; no browser automation.
- No follower-attribution. A reply to a replier-under-thread that produces a follow is logged as `attribution_method = self_reported` per §13.
- No reciprocal-follow expectation. Replier-pool engagement is the same engagement as any other reply: substantive, on-niche, no follow-for-follow.

### 28.21 Personality lore registry

A small Daniel-curated table of recurring jokes, running bits, and personal motifs. Spliced into the system prompt's voice section so the agent draws on existing threads when drafting `content_type = personality` posts instead of inventing fresh ones every time. See §10 `personality_lore` for the schema.

**Why a registry instead of letting the agent infer it from voice samples:**

Voice samples are tone-by-example. The voice profile is structural (cadence, vocabulary). Neither captures *running narrative threads* — the water-bottle-in-frame joke that exists across 6 posts, the kitchen-scanner-fail story Daniel has referenced 4 times, the "neuro-oncology long arc" as a recurring identity anchor. Without an explicit registry, the agent re-invents bits each time, leading to a "new personality content trying to feel familiar" failure mode.

**Workflow (Daniel-only):**

1. Settings → Growth Agent → Personality lore.
2. List of active lore rows. Each shows: `theme`, `description`, `invocation_count`, `last_invoked_at_utc`.
3. "Add lore" form: theme name (short), description (one paragraph), optional `example_posts_json` (post IDs where this lore has been invoked previously — Daniel pastes from Content Performance).
4. Toggle `is_active`, reorder by `priority`, edit theme/description.
5. Per-row "over-relied on" yellow banner triggers when `invocation_count > personality_lore_overuse_threshold` (default 8) AND `last_invoked_at_utc > now() - 30 days`. Banner is informational — doesn't auto-disable.

**System prompt splice:**

`app/agent/prompt_builder.py` reads top-N active lore rows (default `personality_lore_splice_count = 5`, ordered by `priority` ascending) at build time and splices into §28.3 Section 5 (Voice samples), AFTER the voice samples block. Rendering:

```
**Personal lore (running bits to draw on when content_type = personality):**
- water bottle in frame: long-running self-deprecating joke about accidentally leaving my water bottle visible in video shots. Invoked 6 times, last 19 days ago.
- kitchen-scanner fail story: the time the scanner read "ginger" as "soap." Invoked 4 times, last 11 days ago.
- neuro-oncology long arc: recurring reminder that Stir is a stepping stone, not the destination. Invoked 8 times, last 27 days ago.
```

When zero active rows, splice nothing (silent — no banner; Daniel doesn't need a nag for an empty optional feature).

**Invocation tracking:**

When an agent draft is saved with `content_type = personality`, the orchestrator scans the draft text against active lore `theme`s and `description` keyword tokens (case-insensitive). For each match, increment `invocation_count` and set `last_invoked_at_utc = now()`. Matching is fuzzy — over-counting is acceptable (a passing reference still counts), under-counting is not. The orchestrator never auto-edits the draft to insert lore; lore is *available* to the agent via prompt, not *injected* via post-processing.

**Access control:**

The agent has NO write access to `personality_lore`. No tool registry entry references the table. Startup-time assertion verifies this (same pattern as the publish-tool exclusion assertion in §28.4). Daniel-only edit.

**Why hand-curated, not auto-extracted from past posts:**

Auto-extracting would require an LLM to *guess* at recurring themes, which means hallucinated motifs would land in the prompt. Lore is identity-shaped; mis-extracted lore would warp drafts. Hand curation is a 5-minute Settings task once per quarter; auto-extraction would save those 5 minutes at the cost of an unbounded mis-attribution surface. Tradeoff is clearly worth it.

### 28.22 Brain Dump

Capture-first surface, distinct from §14.8 Agent Chat (conversation-first). The job of the Brain Dump is to absorb raw thinking *before* Daniel has to evaluate, structure, or commit to any particular draft. After the dump lands, the agent processes it into clarifying questions + structured candidate drafts; Daniel chooses what to promote into the drafts pipeline.

**Why this is a separate view from §14.8:**

§14.8 Agent Chat is conversational — it assumes a question. The Brain Dump assumes a mess. They are different cognitive modes, and trying to do "Brain Dump in chat" collapses both into a slot-machine experience where Daniel keeps asking "what should I post" without first surfacing what's actually in his head. The view's contract is: paste once, get structured candidates back, decide what to keep.

**Pipeline (one Brain Dump processing pass):**

1. Daniel pastes `raw_text` into §14.9. Inserts a `brain_dumps` row with `status = 'unprocessed'`.
2. On Process click (or automatically on insert via `brain_dump_auto_process_enabled` setting — default `true`), `app/agent/brain_dump.py::process(brain_dump_id)` runs.
3. Single Claude call against `config/brain_dump_prompt.md`. The prompt:
   - Receives `raw_text` wrapped in the `--- BEGIN_UNTRUSTED_DATA ... ---` convention per §28.2.
   - Receives the active niche definition + voice profile + content-type definitions + active personality lore as context.
   - Returns structured JSON: `{clarifying_questions: [str, ≤5], candidate_drafts: [{text, content_type, pillar, audience, cta, rationale}, ≤brain_dump_max_candidate_drafts]}`.
4. Results write to `clarifying_questions_json` + `candidate_drafts_json`; `status = 'processed'`, `processed_at_utc = now()`, `model_used` + `tokens_used` recorded.
5. UI renders both sections. Per-candidate "Send to drafts" buttons invoke `_save_draft_post` (or `_save_draft_reply` if the candidate carries a `target_post_url`) with the candidate's metadata — full Phase 5.8 pipeline runs downstream (IWH preflight, dark-pattern lint, content-type validation, pre-publish scorer, repetition guard).

**Immutability:**

- `raw_text` is NEVER edited after insert. If Daniel wants to refine the input, he creates a new dump. This is intentional — Brain Dump is a *capture* surface; preserving the original mess is part of the audit trail for what the agent worked with.
- `notes` is Daniel's annotation field, editable any time.

**Failure handling:**

- Bad JSON from model after bounded retry → `status = 'failed'`, error captured in `notes`. Retry button on the row creates a new processing attempt on the same row (overwrites prior failed-result fields).
- Anthropic 5xx → bounded retry per existing §28.6 client policy; on exhaustion, same `failed` state.

**Read scope (carried from §28):**

Brain Dump prompt sees: `raw_text`, active niche, active voice profile, content-type definitions, active personality lore. It does NOT see: `stir_testers`, `stir_conversion_events.qualitative_feedback`, prior `agent_messages` content. Each dump is processed in isolation; there's no implicit memory across dumps.

**Anti-feature:**

- No auto-promotion. The agent never moves a candidate draft into `agent_drafts` without Daniel clicking. The Brain Dump's output is *proposals*, not commits.
- No "regenerate" of candidate drafts on the same dump. Each processing run replaces all candidates atomically; partial-state UIs are confusing. If Daniel wants different candidates, he creates a new dump with refined `raw_text`.

### 28.23 Coach with citation allowlist

A second conversational surface (§14.10), structurally identical to §14.8 Agent Chat but with a hard discipline layered on: every analytical claim is filtered through a citation allowlist before persistence. Citations the agent emits that don't resolve to a real DB row are *stripped*; the strip count is surfaced under the message, and when `coach_refuse_without_evidence = true` is set, an uncited analytical message is replaced with a canonical refusal.

**Why this is a separate view from §14.8:**

§14.8 is open-ended: speculate, brainstorm, generate. The Coach explicitly constrains itself to *grounded* advice. Different cognitive contract: you bring a strategic question, you get an answer cited to your own data — or you get a refusal. No speculation surfacing as advice; no LLM-vague "consider posting more about your niche" without a DB row backing the recommendation.

**Citation format (load-bearing — spec'd here, mirrored in `app/agent/coach.py` docstring):**

Citations are inline tokens of the form `〔record_type id_or_filter〕`. Supported `record_type`s:

| `record_type` | Example | Resolution |
| --- | --- | --- |
| `post` | `〔post 142〕` | `posts.id = 142` must exist |
| `view_row` | `〔v_lane_performance row build/icp/value〕` | view row matching the filter must exist |
| `experiment` | `〔experiment 4〕` | `experiments.id = 4` must exist |
| `weekly_review` | `〔weekly_review 2026-W19〕` | `weekly_reviews.iso_week = '2026-W19'` must exist |
| `monthly_review` | `〔monthly_review 2026-05〕` | (Phase 5.11) — same pattern |
| `agent_draft` | `〔agent_draft 88〕` | `agent_drafts.id = 88` must exist |

Any citation with an unsupported `record_type` or a non-resolvable id is stripped with reason logged in `agent_tool_calls.notes`.

**Pipeline (one Coach message round-trip):**

1. Daniel asks a question via §14.10 Coach view (mode flag set on the session).
2. The orchestrator builds the system prompt with Section 9 (Coach mode) appended — instructs the coach on citation discipline.
3. Agent responds with assistant text containing inline citations + `<confidence>` tags per §28.14.
4. `app/agent/coach.py::enforce(message_text)` runs:
   - Extract all `〔...〕` citations.
   - Validate each against the allowlist (DB row existence check per `record_type`).
   - Strip invalid ones; collect them into `stripped_citations`.
   - Return `(clean_text, surviving_citations, stripped_citations)`.
5. If `coach_refuse_without_evidence = true` AND `surviving_citations is empty` AND `clean_text` contains analytical claims (regex per §28.14's `confidence_patterns`), the orchestrator REPLACES `clean_text` with the canonical refusal `"I don't have data in your dashboard to answer this honestly. {gap_description}"` BEFORE persistence.
6. Persist: `agent_messages.content = clean_text_or_refusal`, `agent_messages.evidence_citations_json = surviving_citations`, `agent_messages.confidence_label = parsed dominant label` (from §28.14). Log stripped count + reasons to `agent_tool_calls.notes` of the message's parent tool call.
7. UI renders: clean text with citation chips, confidence-label chips, "N citation(s) removed" yellow banner if applicable.

**Refusal behavior:**

When the coach refuses, the refusal carries `confidence_label = 'fact'` (the FACT being "I can't answer this with the data I have") and zero citations. The refusal message includes a one-line `gap_description` — e.g. "you don't have enough posts in the build pillar yet" or "your last weekly review is from 2026-04-30; data after that hasn't been digested into a reviewable form."

**Why a separate `evidence_citations_json` column rather than overloading `confidence_label`:**

Confidence labels (§28.14) describe the *epistemic stance* of a claim. Evidence citations describe the *provenance* of a claim. They're orthogonal: a `fact`-labeled claim should have at least one citation (or it's a lie); an `inference`-labeled claim might have several citations (the inference draws on multiple rows); a `speculation` claim has zero citations (it's explicitly unsupported and the label tells Daniel so). Keeping them in separate columns lets the strip-and-refuse logic operate on citations without conflating with confidence semantics.

**Read scope:**

Coach prompt sees: full `posts` text, classifications, `v_lane_performance`, `v_content_type_performance`, `v_funnel_daily`, `experiments`, `weekly_reviews`, agent draft history. It does NOT see: `stir_testers`, `stir_conversion_events.qualitative_feedback`, `publish_confirmation_tokens`, or any raw `agent_tool_calls.arguments_json` for publish tools. The read scope is a hard constraint enforced at the tool-result level in `app/agent/session.py`.

**Anti-feature:**

- No auto-acted-on advice. The Coach NEVER calls `save_draft_*` or any write tool. It is advice-only. Daniel decides what to do with the advice; if he wants the agent to draft from it, he switches to §14.8 Agent Chat or §14.9 Brain Dump.

### 28.24 Account Researcher

Deep strategic read on a target X account: posting patterns, positioning, reply-strategy entry points, niche alignment with Daniel. Different question from §28.20 replier-pool — replier-pool answers *who's worth replying to within this thread*; Account Researcher answers *should I be in this account's orbit at all, and how?*

**Manual-paste MVP (no scraping):**

1. Daniel opens §29.7 Reply Target Queue → Account Researcher tab.
2. Form fields:
   - `target_handle` (required) — normalized to `@handle` on insert.
   - `target_url` (optional).
   - `target_display_name` (optional).
   - `target_bio_snapshot` (recommended) — manually pasted.
   - `target_recent_posts_text` (required) — manually pasted, one post per `---` separator. Daniel decides the count (typically last 10–20 posts).
3. Click "Run analysis" → `app/agent/account_research.py::analyze(...)` runs.
4. Single Claude call against `config/account_research_prompt.md`. External content wrapped per §28.2 convention. Returns structured JSON per §10 `account_research_reports.analysis_json` schema.
5. Persist to `account_research_reports`. UI renders the structured analysis.

**Linkage to Reply Target Queue (§29.7):**

- "Generate reply target from this research" button — creates a `reply_targets` row prefilled with:
  - `target_user = account_research_reports.target_handle`
  - `agent_reasoning` populated from `analysis_json.reply_strategy`
  - `pillar` / `audience` inferred from `analysis_json.niche_alignment_with_daniel.overlap_score`
  - `source = 'agent_curated_account'` (Phase 5.9 enum value)
- Bidirectional link: `account_research_reports.linked_reply_target_id` ← `reply_targets.id`.

**Versioned history per handle:**

- The schema permits multiple reports per `target_handle` over time. Each is a point-in-time snapshot.
- The tab's per-handle history view renders consecutive reports side-by-side so Daniel can see how an account's positioning has shifted (the account starts as a kitchen-blogger, ten months later it's a meal-kit founder — research from then vs. now should show that).
- The most recent report is the "current" one for tooling purposes (e.g., the "generate reply target" button uses the latest).

**Read scope:**

Account Researcher prompt sees: pasted `target_bio_snapshot`, `target_recent_posts_text` (wrapped as untrusted), Daniel's active niche definition (for the `niche_alignment_with_daniel` field). It does NOT see any of Daniel's posts or analytics — the analysis is *about the target*, not a comparison; alignment is computed from niche definition alone.

**V1.1+ deferred:**

Programmatic X API pull of bio + recent posts via xurl / direct API. Drops the paste step. Same tool signature gains optional `auto_pull: bool = false`.

**Anti-feature:**

- No scraping (already prohibited by §5 + §28).
- No follower-tracking of target accounts — Daniel doesn't follow the account through Account Researcher. If he wants to engage, the linked `reply_targets` row drives the workflow.
- No "rate this account 1-10." The structured `analysis_json` includes a `niche_alignment_with_daniel.overlap_score` (0-3, graduated-confidence-style), but it's a niche-alignment indicator, not an overall quality score for the account.

### 28.25 Profile Audit

Periodic comprehensive AI review of Daniel's X profile as a *unified surface*: bio + pinned post + recent posts + active voice profile + niche definition, read together. Different question from §28.16's "test against bio" — that one checks bio against niche definition; Profile Audit checks the whole presented surface against itself for *internal consistency*.

**Audit composition:**

The audit consumes a snapshot of Daniel's surface as it appears to a new follower:

1. `bio_snapshot` — Daniel pastes (the X bio isn't auto-pulled at MVP; future V1.1+ direct API auto-pull).
2. `pinned_post_text` — Daniel pastes (or `pinned_post_id` references a tracked post).
3. `recent_post_ids_json` — last `profile_audit_recent_posts_window_days` (default 30) of posts from `posts` where `x_post_id IS NOT NULL`.
4. `active_voice_profile_id` — the current `voice_profiles.is_active = true` row.
5. `niche_problem_snapshot` + `niche_person_snapshot` — copied from current settings.

All five feed `config/profile_audit_prompt.md`. Single Claude call returns the structured `audit_json` per §10.

**Output structure (load-bearing):**

```json
{
  "overall_consistency_score": 0-3,
  "bio_alignment": {"score": 0-3, "gaps": [str], "suggestions": [str]},
  "pinned_post_alignment": {"score": 0-3, "gaps": [str], "suggestions": [str]},
  "recent_posts_themes": [str],
  "voice_consistency_with_profile": {"score": 0-3, "drift_observations": [str]},
  "niche_coherence": {"score": 0-3, "overall_assessment": str},
  "top_three_actions": [str]
}
```

The `top_three_actions` field is load-bearing — the audit is only useful if it produces concrete next steps. The prompt enforces "three specific actions, not generic advice." If the model can't produce three, it returns however many it can; UI handles 1-3.

**Settings panel UI (§14.7 field 12):**

- "Last audit: N days ago" or "No audits yet" header.
- "Run profile audit now" button → opens a form prefilled with current bio (from `account_settings.bio_text_snapshot` or §14.7 field 1) + niche + active voice profile; Daniel pastes pinned-post text + (optional) recent-post window override.
- Past audits table — `audited_at_utc`, `overall_consistency_score`, `top_three_actions` excerpt; expand → full `audit_json` rendering.
- Compare-to-previous diff view when ≥2 audits exist — side-by-side of all sub-scores + actions. Shows what shifted between audits (e.g., "voice consistency dropped from 3 to 2 — drift observations now include 'sentence rhythm more uniform'").
- Cadence reminder: when `now() - last_audit > profile_audit_cadence_reminder_days` (default 90), yellow banner in the panel. Doesn't auto-run.

**Read scope:**

Profile Audit prompt sees: `bio_snapshot`, `pinned_post_text`, post text from `recent_post_ids_json`, active voice profile JSON, niche settings. It does NOT see: `stir_testers`, `stir_conversion_events.qualitative_feedback`, other audits' history (each audit is a fresh independent read), `agent_messages` content.

**Versioned, never-superseding:**

- Audits are append-only history. No `is_active` flag; the "current" audit is implicitly the most recent.
- `superseded_by_audit_id` is a back-reference set when a later audit is run — purely for joining; doesn't disable the prior row.
- Daniel can annotate any past audit via `daniel_notes` — what he acted on, what he deferred.

**Anti-feature:**

- No auto-cadence. The cadence reminder banner surfaces; the audit itself is always Daniel-triggered.
- No auto-acted-on suggestions. Audits produce `top_three_actions`; what Daniel does with them is Daniel's call. The audit never edits the bio or the pinned post or settings.

### 28.26 Campaigns + Campaign items

Multi-week themed pushes. A campaign carries a hypothesis + date range + dual-stream success criteria + a set of items (planned, drafted, shipped, skipped). Distinct from `experiments` (hypothesis-only, no item planning) and from `weekly_reviews` (retrospective, one week).

**Why campaigns are the right granularity:**

XGrowth already has three time horizons: daily reps (§14.1), weekly reviews (§14.6), and milestone ladders (§14.3). Campaigns slot between weekly and milestone — typically 2–8 weeks, themed, hypothesis-driven, item-planned. They give Daniel a way to organize *a deliberate push* without committing to a milestone-sized arc.

**Schema discipline (load-bearing):**

- `success_criteria_json` MUST contain ≥1 distribution metric AND ≥1 validation metric. Schema validation in `app/agent/campaigns.py` rejects otherwise. This enforces §1's dual-stream discipline at campaign granularity — a "follower-focused campaign" with no validation lever is exactly what §1 was written to prevent.
- A campaign cannot be `completed` without all success-criteria actuals + a lesson + a counterfactual_note. Same epistemic discipline as `weekly_reviews`.

**State machine:**

```
campaigns.status:
  planning → active        (when start_date <= today AND Daniel clicks "Activate")
  active → completed       (when Daniel clicks "Complete" + retro form filled)
  active → abandoned       (when Daniel clicks "Abandon" + abandon_reason required)
  planning → abandoned     (planning a campaign you decide not to run is fine)

campaign_items.status:
  planned → drafted        (when agent_draft_id is populated)
  planned → shipped        (manual; when a post is published directly without a draft)
  drafted → shipped        (when linked posts.published_to_x_at populates)
  planned → skipped        (Daniel-decided)
  drafted → skipped        (Daniel-decided)
```

**Agent integration (new tool `#21 analyze_campaign_progress(campaign_id)`):**

Read-only. Returns:

```json
{
  "campaign_id": int,
  "name": str,
  "status": str,
  "days_remaining": int | null,
  "progress": {"shipped": int, "planned": int, "drafted": int, "skipped": int, "percent_shipped": float | null},
  "linked_posts_summary": {"impressions_total": int, "engagement_rate_median": float, "by_pillar": {...}, "by_content_type": {...}},
  "success_criteria_progress": [{"metric": str, "target": str, "current_actual": str | null, "on_track": bool}],
  "interpretation": "agent-generated structured interpretation with <confidence> tags per §28.14"
}
```

The "Ask the agent for ideas" affordance in §14.12 prefills an Agent Chat session with the campaign's hypothesis + this tool's output, and asks the agent for 3 candidate items as `campaign_items.status = 'planned'` rows.

**Linkage to existing tables:**

Campaign items don't duplicate state. An item is a *grouping* over an existing `posts` / `agent_drafts` / `reply_targets` row. The `campaign_items.{post_id, agent_draft_id, reply_target_id}` columns are nullable FKs; bidirectional joining is the query path.

**Anti-feature:**

- No auto-status-transitions based on time alone. Active campaigns whose `end_date` has passed do NOT auto-`completed` — they show "ended N days ago, complete now or extend?" Daniel decides. Auto-completion would let campaigns close without retros, defeating the point.
- No "campaign of campaigns" / nesting. A campaign is one level deep. Multi-campaign strategy lives in the milestone ladders.

### 28.27 Monthly AI reviews

Cadence companion to `weekly_reviews`. Same epistemic discipline (counterfactual required, speculation blocks export, agent sections emit `<confidence>` tags per §28.14), with month-granularity auto-fill and additional `content_type` axis fields per §28.17.

**New auto-filled fields vs. weekly:**

- `strongest_content_type` / `weakest_content_type` — per §28.17 V/G/P/P axis, with graduated-confidence labels carried from `v_content_type_performance`.
- `campaigns_completed_json` — JSON array of campaigns that completed in this month, with their success-criteria actuals.
- `follower_delta` over the month rather than week (computed from `v_account_daily`).

**New agent tool `#22 draft_monthly_review_section(section_name, iso_month)`:**

Mirror of the existing `draft_weekly_review_section` tool. `section_name` accepts `interpretation`, `lesson`, `counterfactual`, `next_month_experiment`, `campaigns_retro` (new — pulls from `campaigns_completed_json`).

**Cadence selector in §14.6:**

The Weekly Review view gains a Weekly / Monthly toggle at the top. Switching toggles the underlying table; the UI shell (auto-filled fields display, user-filled forms, agent-draft buttons, export blockers) is shared. Daniel can run both cadences in parallel; the schema permits a weekly review and a monthly review in the same week.

**Why not collapse weekly + monthly into one table:**

Considered. Rejected. Different cadences imply different auto-fill semantics, different sample-size confidence thresholds (a "monthly strongest pillar" needs n ≥ 30; weekly is n ≥ 15 per §11), different retro questions. A single table with a `cadence` enum would carry mode-aware logic in every consumer. Two tables, one shared UI shell — cleaner.

**`monthly_review_auto_draft_enabled = false` default:**

A future setting could auto-draft the monthly review at the start of each month. Default OFF — same anti-anxiety stance as the profile audit (§28.25). The reminder banner surfaces; Daniel clicks "Draft now" when he wants it.

### 28.28 Content Calendar

Visual planning grid. §14.11 view. The first XGrowth surface that's purely *planning*-oriented; everything else is doing or reviewing.

**Calendar's input sources:**

1. `posts` with `published_to_x_at` populated → POSTED.
2. `posts` with `manual_confirmation_status = 'draft'` and a `created_in_app_at` in the future → DRAFTED-FOR-FUTURE (paired with §19 item 11 scheduled drafts).
3. `agent_drafts` with `status = 'proposed'` or `'accepted_with_edits'` and no linked `posts.id` yet → DRAFTED.
4. `campaign_items` with `status = 'planned'` and a non-NULL `planned_for_date` → PLANNED.

The calendar reads from all four; the cell's status chip indicates which provenance.

**AM/PM split:**

Default rule: a row is AM if its time-of-day < `calendar_am_cutoff_hour` (default 12 local time), PM otherwise. Planned items without a time-of-day default to PM unless Daniel overrides.

**"+ schedule slot" inline form:**

Two paths:
1. **Campaign-scoped:** picks an existing campaign (or creates a new one) → adds a `campaign_items` row with `planned_for_date` = the selected day, `item_type = 'post'`, optional `planned_text`.
2. **Ad-hoc:** creates a `posts` row with `manual_confirmation_status = 'draft'`, `created_in_app_at` = selected day's noon, no campaign linkage.

Daniel picks. Both paths flow through the same `audit_logs` write-through.

**Filter dropdown:**

Persisted in `st.session_state['calendar_filter']`. Options: `all`, per-pillar, per-content-type, per-campaign. Filters apply across all visible weeks during navigation.

**No automation:**

- No "AI suggests when to schedule." The calendar is a Daniel tool; the agent doesn't write to it directly.
- No "auto-publish at scheduled time." §19 item 11's scheduled-drafts flow still requires fresh confirmation at publish time (§28.10 contract is non-negotiable). The calendar shows the schedule; the publish moment is still gated by Daniel's two-step confirm.

### 28.29 Inspiration library + transforms + plagiarism guard

Capture-then-remix for external content. Daniel saves posts he liked (paste-driven, no scraping), runs transform modes against them, and chooses whether to promote outputs to the drafts pipeline. The plagiarism guard is the load-bearing piece — without it, this becomes a copy-paste machine.

**Transform modes (load-bearing — spec'd here, mirrored in `app/agent/inspiration.py::TRANSFORM_MODES`):**

| Mode | Output |
| --- | --- |
| `structure` | The abstract structural pattern of the source — "an opener that names a specific small failure + a learning frame." Pattern, not wording. |
| `hook_pattern` | Just the hook style isolated: how the first sentence works, what it promises, what tension it creates. |
| `counterpoint` | An honest counterpoint to the source's argument — what it gets wrong, what it understates, what context it skips. |
| `original_version` | A Daniel-authored take on the same topic from his actual experience. Voice profile + niche definition + personality lore all spliced in. |
| `voice_profile_version` | The source's idea rendered in Daniel's voice. Higher plagiarism-risk surface (more retained structure); the guard handles this. |
| `expand` | The source's hook expanded into a longer thread structure. |
| `compress` | The source's longer point compressed into a single tight standalone. |

Adding a new mode requires updating both `TRANSFORM_MODES` AND the spec's table here AND the CHECK constraint on `inspiration_transforms.transform_mode` together.

**Plagiarism guard (deterministic-first, AI-cannot-underreport):**

The guard combines two reads:

1. **Deterministic**: Jaccard token similarity + longest contiguous n-gram shared between source and output.
2. **AI-reported**: the structured-output prompt asks the model to self-report its plagiarism risk as `low | medium | high`.

Final `plagiarism_risk_label = max(ai_reported, deterministic)` using the ordering `low < medium < high`. This is the load-bearing rule: the AI cannot undersell high token overlap because the deterministic score is computed in Python and the max function favors caution.

**Threshold tuning (settings):**

- `inspiration_plagiarism_jaccard_high_threshold` (default 0.65) — Jaccard ≥ this → deterministic `high`.
- `inspiration_plagiarism_jaccard_medium_threshold` (default 0.35) — Jaccard ≥ this (and < high) → deterministic `medium`.
- `inspiration_plagiarism_ngram_high_threshold` (default 8) — longest shared n-gram ≥ this words → deterministic `high`.
- `inspiration_plagiarism_ngram_medium_threshold` (default 5) — same logic.
- The final deterministic label is the worst of the two (`max(jaccard_label, ngram_label)`).

**UI gating (§14.13):**

- `low` → "Send to drafts" works freely.
- `medium` → "Send to drafts" works, with a yellow warning under the button.
- `high` → "Send to drafts" is DISABLED until Daniel checks "I've reviewed the overlap, this is intentional." Checking the box logs an `audit_logs` row with `event_category = 'data', event_type = 'inspiration_plagiarism_override'` and the override reason.

**Read scope:**

Inspiration prompts see: pasted `source_post_text` (wrapped per §28.2 untrusted-data convention), the active voice profile, niche definition, personality lore. They do NOT see `stir_testers` / `stir_conversion_events.qualitative_feedback` / other inspirations / `agent_messages`.

**Anti-feature:**

- No scraping of X posts. Daniel pastes; that's the loop.
- No "auto-transform on save." Each transform is an explicit Daniel-click — costs (token + cognitive) are predictable.
- No retro-attribution. If Daniel ships a post derived from an inspiration, the `inspiration_transforms.used_for_post_id` linkage is informational; the post itself doesn't carry "this was inspired by @other_account" — that's a workflow record, not a public attribution.

### 28.30 Comprehensive audit logs

Append-only canonical record of state-changing events. Distinct from `agent_tool_calls` (logs every tool invocation, including read-only) — `audit_logs` is what *changed*. Together they cover the full audit surface: what the agent looked at + what changed in the system.

**Why two tables and not one:**

- `agent_tool_calls` is high-volume (every read tool call is a row); pruning it eventually is fine.
- `audit_logs` is low-volume (only state-changes); long retention is reasonable.
- Different access patterns. The Settings → Audit log viewer queries `audit_logs`; the agent's own debugging surface queries `agent_tool_calls`.

**Categories (load-bearing):**

| Category | What it covers |
| --- | --- |
| `auth` | OAuth connect/disconnect events for X (Phase 5.5+). |
| `x_op` | X API operations: publish attempts (success + failure), token refreshes, rate-limit hits. |
| `publish` | Publish lifecycle events — every confirmation token mint, every publish-tool invocation, every reconciliation event (§28.10 crash recovery). |
| `settings` | Settings row UPDATEs. `details_json` carries `{setting_key, old_value, new_value}`. |
| `export` | CSV / Markdown / JSON exports — what was exported, where to, by which export action. |
| `data` | Data mutations: row deletions (with `snapshot_of_deleted_row`), corrections, inspiration plagiarism overrides. |
| `admin` | Backup runs, vacuum runs, audit-log prunes, manual data-integrity actions. |
| `migration` | Each applied migration logs one row at migration end. |

**Write-through points (every state-changing path must call `audit_log.log(...)`):**

- §28.5 voice sample mark/unmark.
- §28.10 every publish attempt — succeeded and failed.
- §28.12 voice profile regeneration.
- §28.16 niche setting change.
- §28.20 replier-pool entry created.
- §28.21 personality lore add/edit/disable.
- §28.25 profile audit run.
- §28.26 campaign create / item add / status transition.
- §28.27 monthly review draft / export.
- §28.29 inspiration save / transform / plagiarism override.
- §16 every export action.
- `scripts/backup_db.py` every backup run.
- Every migration application.

**Retention:**

`audit_log_retention_days` (default 365). A daily prune job in `scripts/prune_audit_log.py` deletes rows older than the retention window. The prune itself audit-logs as `event_category = 'admin', event_type = 'audit_logs_pruned'` with the pruned count in `details_json`. Pruning is opt-in by setting; default 365 keeps a year of state-changes (Daniel's whole MVP+1 horizon).

**Recovery via audit log:**

For `event_category = 'data', event_type = 'row_deleted'`, the `details_json.snapshot_of_deleted_row` preserves the full row contents so the audit log itself is a recovery option. A "Restore from audit log" Settings affordance reads the snapshot and re-INSERTs the row (with a new `id`; the old `id` is preserved in `details_json` for reference). This means the audit log doubles as a soft-delete mechanism without polluting every table with `deleted_at` columns.

**Read scope:**

The Settings → Audit log viewer renders `details_json` for Daniel-only viewing. The agent does NOT have read access to `audit_logs` — no tool registry entry references the table. This is the same access discipline as `publish_confirmation_tokens` per §28.10: state-change logs are Daniel's debugging surface, not the agent's context.

**Anti-feature:**

- No append from agent context. The agent can trigger state changes via its existing tools; those tools call `audit_log.log(...)` from the server-side code path. The agent never has a "write an audit row" tool.
- No structured-deletion-of-audit-rows. Pruning by retention is the only path that removes rows; even that self-audits.

### 28.31 Blogs — schema discipline and state machine

The blog production lifecycle has its own state machine because long-form has different gates than short-form. A blog is not a "long post" — it's authored, edited, reviewed, exported as a file, and (only then) published externally on Daniel's blog platform.

**State machine (load-bearing — enforced in `app/agent/blogs.py::transition_status`):**

```
idea          → outlining | archived
outlining     → drafting | idea | archived
drafting      → editing | outlining | archived
editing       → ready | drafting | archived
ready         → exported | editing | archived
exported      → published_externally | ready | archived
published_externally → archived
archived      → (terminal)
```

Why these transitions:
- `idea → outlining` (not directly to drafting): the outline is a separate artifact (`blogs.outline_markdown`) preserved through drafting so Daniel can compare draft to plan.
- `drafting → outlining`: legal. Sometimes the draft reveals the outline was wrong.
- `editing → drafting`: legal. Sometimes editing reveals the draft needs a rewrite, not polish.
- `ready → exported` requires an export operation that succeeded — transition cannot be set manually; it's set by the export path on success.
- `exported → published_externally` is the ONLY manual transition that depends on an out-of-app fact (Daniel actually publishing). When Daniel makes this transition, the editor requires `external_url` to be populated.
- `archived` is terminal — re-activating a blog means duplicating it.

**Versioning discipline:**

Every save that *changes content* (body OR outline OR title OR status) appends a `blog_versions` row. No-op saves (where everything is unchanged) skip the version row. This keeps history meaningful — every row represents real work.

**Why no soft-delete on blogs:**

`archived` covers the "I don't want to see this in my main list" use case. Hard deletion is rare and rare cases go through `audit_logs`'s recovery path (§28.30) — the deletion's `details_json.snapshot_of_deleted_row` preserves the blog + all versions + all exports as JSON, so a deleted blog is recoverable from the audit log.

**Unified identity (the point of putting blogs here):**

Every agent-driven blog action reads the same identity stack as X drafting:
- Active niche definition (§28.16)
- Active voice profile (§28.12)
- Top-N voice samples (§28.5)
- Top-N active personality lore (§28.21)
- Confidence-label discipline (§28.14)

Without this unification, "blogs in XGrowth" would just be "blogs in a different app that happens to share a database." The unified identity is the entire reason for the consolidation.

### 28.32 Blog drafting agent tools

Four agent tools cover the blog production pipeline: `outline_blog`, `draft_blog`, `suggest_blog_edits`, `generate_blog_seo_metadata`. All four are registered (not internal-only). All four respect §28.6 cost cap and emit `<confidence>` tags per §28.14.

**Tool catalog additions to §28.4:**

| # | Tool | Input | Output | Side effects |
| --- | --- | --- | --- | --- |
| 25 | `outline_blog` | `blog_id` | structured Markdown outline | Writes a `blog_versions` row via `save_blog(... agent_action='outline')`; populates `blogs.outline_markdown` |
| 26 | `draft_blog` | `blog_id, target_length_words?` | full draft body Markdown | Writes a `blog_versions` row via `save_blog(... agent_action='draft')`; populates `blogs.current_body_markdown` |
| 27 | `suggest_blog_edits` | `blog_id` | `[{paragraph_anchor, suggested_replacement, rationale, confidence_label}]` | None directly — UI surfaces with Accept/Reject/Modify; Accept calls `save_blog(... agent_action='edit_suggestion_applied')` |
| 28 | `generate_blog_seo_metadata` | `blog_id` | `{seo_title, seo_description, seo_tags}` | Writes to `blogs.seo_title` / `seo_description` / `seo_tags_json` DIRECTLY; no version row (SEO metadata is sidecar, not content) |

**Why suggest_blog_edits doesn't auto-apply:**

Edit suggestions are localized — per-paragraph replacements with rationale. Auto-applying all of them would collapse Daniel's authorial judgment. The UI lists each suggestion separately so Daniel accepts/rejects/modifies per-paragraph; only accepted changes persist.

**Confidence labels on blog drafts:**

A `draft_blog` output with `confidence_label_at_version = 'speculation'` is a SIGNAL — the agent generated content it can't ground in fact (e.g., made up a statistic, fabricated an example). Daniel sees a yellow chip in the version list AND the editor displays a chip above the body. The export path is NOT blocked on speculation — Daniel can still export a speculation-labeled blog if he reviews and accepts the speculation. But the `blog_versions.confidence_label_at_version` is a permanent epistemic record.

**Read scope:**

All four tools read: `blogs.*`, `blog_versions.*` for prior versions of THIS blog, active niche, active voice profile, active voice samples, active personality lore. They do NOT read: other blogs' content, `stir_testers`, `stir_conversion_events.qualitative_feedback`, other `agent_messages`. The read scope is enforced at the tool-result level in `app/agent/session.py`.

**No multi-blog context.** Each blog draft is generated in isolation. The agent doesn't "remember" what it wrote in another blog. Identity comes from voice profile + niche, not from prior agent output.

**Anti-feature:**

- No auto-drafting on idea creation. Creating a blog with `status = 'idea'` does NOT trigger `outline_blog`. Every agent invocation is explicit Daniel-click; cost and intent stay predictable.
- No automatic status transitions on agent action. Running `outline_blog` doesn't auto-transition `idea → outlining` — Daniel transitions when he's reviewed the outline.

### 28.33 Blog exports

Markdown / HTML / JSON / MDX. Atomic write-then-record. Append-only history (re-export = new row, overwrites file but preserves prior export's row).

**Atomicity contract:**

```
BEGIN TRANSACTION
  render content to bytes
  compute content_sha256
  write file to target_path (file I/O — outside DB transaction)
  IF file write succeeded:
    insert blog_exports row (capturing target_path, content_sha256, file_size_bytes)
    insert audit_logs row (event_category='export', event_type='blog_export_{format}')
    IF blog.status == 'ready':
      transition blog.status → 'exported'
  COMMIT
ELSE:
  ROLLBACK (DB)
  attempt to delete partially-written file (best-effort)
  surface error to user
```

The DB writes happen AFTER the file write succeeds. If the DB writes fail after the file write, the file exists on disk but the export isn't recorded — surface a "file written but export record failed" banner with manual-mark-resolved button (analogous to the §28.10 publish-flow reconciliation banner).

**SEO frontmatter (Markdown / MDX):**

```yaml
---
title: "Kitchen scanner UX from three failed dinner attempts"
description: "..."
tags: ["cook-mode", "ux", "ai"]
slug: "kitchen-scanner-ux-failures"
pillar: "stir"
audience: "icp"
created_at_utc: "2026-05-15T14:32:00Z"
exported_at_utc: "2026-05-22T18:42:00Z"
---
```

**Repurposing-notes footer (when `include_repurposing_links = True`):**

```markdown
---
**Repurposing notes (excluded from public publish):**
- X thread: 3 posts derived from this blog — [post 142](...), [post 156](...), [post 161](...)
- X teaser: [post 209](...)
```

Daniel can manually strip the footer before publishing externally if he wants — the export path's "include repurposing notes" toggle defaults to FALSE specifically because most public publishing surfaces don't want internal cross-references in the body.

**Export integrity (`content_sha256`):**

Every export row carries `content_sha256 = sha256(exported_file_contents)`. If Daniel later suspects the file was overwritten or tampered with on disk, he can re-hash the file and compare to the `blog_exports` row's hash to detect drift. The hash is the audit anchor.

**No publish-to-external-platform integration.**

The export writes to disk. Daniel takes the file (or its content) and publishes externally. The app NEVER calls Substack / Ghost / WordPress / Medium / any blog platform API. This is a hard scope rule (§0, §7.1, §1).

### 28.34 X ↔ blog repurposing

Bidirectional content reuse. A blog can be repurposed into X posts (thread / single-post summary / teaser-with-link). An X post can be expanded into a blog idea (outline + framing). Both directions are agent-tooled, both flow through the existing drafts pipeline + plagiarism guard.

**Tool catalog additions:**

| # | Tool | Input | Output | Side effects |
| --- | --- | --- | --- | --- |
| 29 | `repurpose_blog_to_x` | `blog_id, mode: 'thread_from_sections' \| 'single_post_summary' \| 'teaser_with_link'` | List of `agent_drafts.id` (one for `single_post_summary` and `teaser_with_link`, multiple for `thread_from_sections`) | Inserts `agent_drafts` rows via the full Phase 5.8 pipeline (IWH, dark-pattern lint, content-type validation, pre-publish scorer, repetition guard, AND plagiarism guard against the blog body). Inserts `blog_to_post_links` rows once Daniel ships the resulting drafts (linkage happens at ship time, not draft time — drafts may be discarded). |
| 30 | `repurpose_x_to_blog_idea` | `post_id` | New `blogs.id` (status='idea') | Inserts a new `blogs` row with `status='idea'`, the X post's text as starter `notes`, populates `pillar` and `audience` from the post's classification, populates `niche_*_snapshot` from current settings. Inserts a `blog_to_post_links(direction='post_to_blog', relationship_kind='derived_outline')` row immediately (linkage is unambiguous at idea creation). |

**Plagiarism guard (load-bearing for blog→X):**

The §28.29 deterministic floor (Jaccard + n-gram + AI-reported, with final = `max(...)`) runs on every blog→X repurposing output against the source blog body. Expected behavior:

- `thread_from_sections` outputs often have `medium` overlap (the X posts are derived directly from the blog's sentences) — the guard surfaces, the UI shows a yellow banner per draft, Daniel reviews each before promoting.
- `single_post_summary` typically has `low` overlap (it's a compression, not a quote).
- `teaser_with_link` typically has `low`/`medium` overlap depending on whether Daniel chose to quote a specific blog line.
- `high` overlap blocks the drafts-pipeline insertion until Daniel overrides (same UX as §14.13 high-risk inspiration transforms). Override is audit-logged.

**Why blog→X plagiarism check matters:**

Without it, the agent could literally emit a paragraph of the blog as the X post body. That's not plagiarism in the legal sense (it's Daniel's own content), but it IS a repurposing failure — the X post should be derivative, not duplicative. The guard catches that case.

**X→blog idea direction:**

Lower-overlap by construction (the blog is being *expanded* from a short post, not compressed). The guard still runs but rarely fires. The output is just an idea + outline framing — Daniel writes the actual blog via `outline_blog` + `draft_blog`. The linkage row is established at idea creation; subsequent blog→X repurposing of the same blog would create *additional* links per derived post.

**Bidirectional linkage at ship time:**

`blog_to_post_links` for blog→X is written when an `agent_drafts` row derived from a blog gets shipped (`posts.published_to_x_at` populated). Until then, the linkage is implicit in `agent_drafts.notes` ("derived from blog 42, mode=thread_from_sections"); shipping is the moment that promotes the implicit link to an explicit row. This avoids polluting `blog_to_post_links` with rows for drafts that get discarded.

**Read scope:**

`repurpose_blog_to_x` reads: the blog's current body + outline + identity context (niche, voice profile, lore). It does NOT read other blogs, other posts, `stir_testers`, etc. `repurpose_x_to_blog_idea` reads: the post's text + classification + identity context. Same exclusions.

**Anti-feature:**

- No auto-repurposing on blog status transitions. Transitioning to `ready` does NOT trigger any X repurposing.
- No auto-repurposing on post publish. A shipped X post does NOT auto-create a blog idea.
- Both directions are explicit Daniel-click, no exceptions.

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

### Niche & Content-Type Calibration Pack addition (2026-05-22) — §28.16 through §28.21

Distilled from Jacob Edmunds's "1k followers" YouTube framework (May 2026) cross-referenced against XGrowth's existing thesis. Six features selected; tactical advice that violated XGrowth's discipline (causal velocity claims, follow-for-follow, daily-V/G/P/P-cadence pressure, polarizing-for-reach) was rejected. Added as Phase 5.9 Should-ship. No existing contracts changed.

78. **Structured niche definition (§28.16).** Two settings rows (`niche_problem`, `niche_person`) spliced into §28.3 Section 1 as a load-bearing identity statement. Settings → Growth Agent → Niche panel with a "test against bio" Haiku affordance. New §28.2 rule #15: agent refuses to draft when either field is empty. Orchestrator-enforced, not prompt-enforced. (§28.2 rule #15, §28.16, §14.7 Settings field 10, §25 Phase 5.9)

79. **Content type axis V/G/P/P (§28.17).** New enum column on `posts` and `agent_drafts`: `value | growth | personality | proof | unspecified`. Orthogonal to pillar/audience/CTA — pillar is topic, content_type is purpose. New tool `#12 get_content_type_gaps` + new view `v_content_type_performance` (same graduated-confidence treatment as `v_lane_performance`). §14.1 Today gains a "today's content-type recommendation" line; §14.4 Content Performance gains a "Content type" tab. Daily-cadence pressure from the source video deliberately rejected. (§10 `posts.content_type`, §10 `agent_drafts.content_type`, §11 `v_content_type_performance`, §28.17, §25 Phase 5.9)

80. **Reply-quality lint (§28.18).** Second Haiku lint pass on every reply draft, gated by `reply_quality_lint_enabled` (default `true`), positioned in the `_save_draft_reply` preflight between dark-pattern lint and pre-publish scorer. Catches "forced / AI-tasting / selfishly self-promoting" — distinct failure mode from §28.2 #12 manipulation. Failure counts as a failed IWH revision via the same enforcement path. `agent_drafts.reply_quality_lint_passed` persists the result. (§10 `agent_drafts.reply_quality_lint_passed`, §28.18, §25 Phase 5.9)

81. **Follower-velocity projection (§28.19).** New computed view `v_follower_velocity` derived from `v_account_daily` with projection columns that return NULL when `abs(delta_7d) < velocity_projection_noise_floor_followers` (default 10). §14.3 Progress velocity panel renders the math without faking precision on noise. New tool `#13 get_velocity_projection()`. Source-video framing of velocity as a *goal* deliberately rejected; XGrowth treats projections as descriptive, not normative, per §13 + §5. (§11 `v_follower_velocity`, §28.19, §14.3, §25 Phase 5.9)

82. **Replier-pool candidate discovery (§28.20).** Third reply-target discovery path beyond paste-URL and curated-account: pasted replier handles/excerpts from under a big-account thread. New tool `#14 score_replier_pool` adds a `thread_context_fit_score` dimension on top of §29.3's four. `reply_targets.source` enum extended with `replier_under_thread`. MVP is paste-driven (no scraping); V1.1+ adds the programmatic X API scan. (§10 `reply_targets.source` enum extension, §28.20, §29.7 UI extension, §25 Phase 5.9)

83. **Personality lore registry (§28.21).** New tiny `personality_lore` table — Daniel-only curated, agent has no write access. Spliced into §28.3 Section 5 (Voice samples) after the raw samples. Three-layer voice stack: voice samples (tone-by-example) + voice profile (cadence/vocabulary) + personality lore (recurring narrative threads). Orchestrator tracks `invocation_count` and `last_invoked_at_utc` via fuzzy text scan when `content_type = personality` drafts are saved. (§10 `personality_lore`, §28.5 cross-reference, §28.21, §14.7 Settings field 11, §25 Phase 5.9)

Deliberately rejected from the source video:

- **"1k followers in 30 days" as a target.** §5 #2 + §13 already refuse causal/velocity claims. The §28.19 projection panel surfaces the arithmetic as a planning helper, never as a goal.
- **"Post all four V/G/P/P types daily."** §28.17 explicitly carves this out — the framework is for *slice analysis*, not daily-posting pressure.
- **"Polarizing opinions" as a content recipe.** The §28.18 reply-quality lint + §28.2 #12 dark-pattern lint together keep "polarizing-but-genuine" allowed and "polarizing-for-reach" blocked. The video's distinction is acknowledged; the recipe-ification is not.
- **Engagement-group / follow-for-follow / gain-train tactics.** Already prohibited via §5 + §28.2 #12; reaffirmed.
- **Community / coaching-call upsell context from the source video.** Influencer monetization framing; ignored.
- **Auto-extraction of personality lore from past posts.** §28.21 carves this out explicitly — lore is identity-shaped and mis-attribution would warp drafts; hand curation is a tiny tax for a meaningful safety floor.

### Strategic Analysis Pack addition (2026-05-22) — §28.22 through §28.25

Phase 5.10. Ports CreatorOS's four strategic-analysis surfaces into XGrowth's discipline, with the explicit goal of closing the consolidation gap — after this phase, the workflows that previously required jumping to CreatorOS (ideation, advice, target-account research, profile review) all live in XGrowth. Two new views (§14.9, §14.10), three new tables, one extension to `agent_messages`. No existing contracts changed.

84. **Brain Dump capture-first view (§28.22).** New `brain_dumps` table + §14.9 view + `app/agent/brain_dump.py` + tool `#18 process_brain_dump`. Distinct cognitive mode from §14.8 Agent Chat: capture-first instead of conversation-first. Raw text is immutable after insert; processing produces clarifying questions + ≤5 structured candidate drafts; promotion to actual drafts is explicit Daniel action that runs the full Phase 5.8 pipeline. (§10 `brain_dumps`, §14.9, §28.22, §25 Phase 5.10)

85. **Coach with citation allowlist (§28.23).** Second conversational surface (§14.10) with hard discipline layered on §14.8: every analytical claim is filtered through a citation allowlist; invalid citations are stripped with strip-count surfaced; `coach_refuse_without_evidence = true` (default) produces canonical refusals instead of un-cited speculation. New column `agent_messages.evidence_citations_json`. Citation format `〔record_type id_or_filter〕` is load-bearing and spec'd in §28.23. (§10 `agent_messages.evidence_citations_json`, §14.10, §28.23, §25 Phase 5.10)

86. **Account Researcher (§28.24).** New `account_research_reports` table + tool `#19 analyze_account` + Account Researcher tab inside §29.7 Reply Target Queue + bidirectional linkage to `reply_targets`. Manual-paste workflow for MVP; V1.1+ adds programmatic X API pull. Versioned history per handle lets Daniel see how a target account's positioning has shifted over time. Answers a different question from §28.20 replier-pool. (§10 `account_research_reports`, §28.24, §29.7 tab extension, §25 Phase 5.10)

87. **Profile Audit (§28.25).** New `profile_audits` table + tool `#20 audit_profile` + Settings → Growth Agent → Profile Audit panel. Quarterly (or on-demand) comprehensive review of bio + pinned post + recent posts + active voice profile + niche as a unified surface; `top_three_actions` field is load-bearing; append-only history with compare-to-previous diff view. Cadence reminder banner at 90 days; never auto-runs. (§10 `profile_audits`, §14.7 field 12, §28.25, §25 Phase 5.10)

Deliberately rejected from CreatorOS for this phase:

- **Auto-processing of Brain Dump on insert without confirmation.** Could be added later as a setting (`brain_dump_auto_process_enabled`); MVP is explicit click to keep costs predictable and intent clear.
- **Coach as a tool the agent can call from §14.8.** Considered, rejected — the Coach's discipline is a *mode*, not a *tool*. Mixing modes within one chat session breaks the citation contract. Separate view, separate session.
- **Account Researcher auto-pull via scraping.** Already prohibited by §5; manual paste is the MVP path, X API direct is V1.1+.
- **Profile Audit cadence as a cron.** §28.25 carves this out — Daniel-triggered only; cron would import the "anxiety dashboard" failure mode the spec exists to prevent.
- **Coach allowed to call write tools.** §28.23 carves this out — the Coach is advice-only; it never calls `save_draft_*` or any state-changing tool. Different cognitive contract from §14.8 Agent Chat.

### Growth Layer + Quality-of-Life Pack addition (2026-05-22) — §28.26 through §28.30

Phase 5.11. Ports CreatorOS's strategic-layer surfaces (campaigns, monthly reviews) and quality-of-life capabilities (content calendar, inspiration library, audit logs) into XGrowth's discipline. Three new views (§14.11 Content Calendar, §14.12 Campaigns, §14.13 Inspiration Library), one cadence-toggle extension to §14.6, six new tables, one new computed view. After Phase 5.11, the only remaining CreatorOS capability is blogs (Phase 6 with explicit scope rewrite).

88. **Campaigns + campaign items (§28.26).** New `campaigns` and `campaign_items` tables + `v_campaign_progress` view + §14.12 view + tool `#21 analyze_campaign_progress`. Multi-week themed pushes with hypothesis + dual-stream success criteria (≥1 distribution metric + ≥1 validation metric — schema-enforced). Retro on completion requires actuals + lesson + counterfactual_note. No nested campaigns; no auto-status-transitions on time. (§10 `campaigns`, §10 `campaign_items`, §11 `v_campaign_progress`, §14.12, §28.26, §25 Phase 5.11)

89. **Monthly AI reviews (§28.27).** New `monthly_reviews` table + cadence toggle in §14.6 + tool `#22 draft_monthly_review_section`. Mirror of weekly review with month-granularity auto-fill, `strongest_content_type` / `weakest_content_type` per §28.17, and `campaigns_completed_json` populated. Same export-blocked rules. Default `monthly_review_auto_draft_enabled = false` — Daniel-triggered, no cron. (§10 `monthly_reviews`, §14.6 cadence toggle, §28.27, §25 Phase 5.11)

90. **Content Calendar (§28.28).** New §14.11 view — visual AM/PM grid over shipped/drafted/planned items. Reads from `posts`, `agent_drafts`, `campaign_items`, and scheduled-drafts (§19 item 11). "+ schedule slot" inline form picks campaign-scoped or ad-hoc. No automation of publish; scheduling does not bypass §28.10's two-step confirmation. (§14.11, §28.28, §25 Phase 5.11)

91. **Inspiration Library + 7 transform modes + deterministic plagiarism guard (§28.29).** New `saved_inspiration_posts` and `inspiration_transforms` tables + §14.13 view + tools `#23 transform_inspiration` and `#24 score_inspiration_plagiarism_risk` + `app/agent/inspiration.py`. Seven transform modes (structure, hook_pattern, counterpoint, original_version, voice_profile_version, expand, compress). Plagiarism guard combines deterministic Jaccard + n-gram with AI-reported risk via `max(...)` — AI cannot underreport. High-risk transforms gate "Send to drafts" until override; override is audit-logged. (§10 `saved_inspiration_posts`, §10 `inspiration_transforms`, §14.13, §28.29, §25 Phase 5.11)

92. **Comprehensive audit logs (§28.30).** New append-only `audit_logs` table. Write-through from every state-changing path (publish, settings change, voice profile regenerate, inspiration override, campaign transition, export, migration, etc.). Eight categories spec'd. Configurable retention default 365 days; pruning self-audits. Recovery via `details_json.snapshot_of_deleted_row` — audit log doubles as soft-delete mechanism. Agent has no read or write access to this table. (§10 `audit_logs`, §28.30, §14.7 Settings audit-log viewer, §25 Phase 5.11)

Deliberately rejected from CreatorOS for this phase:

- **Nested campaigns / multi-campaign hierarchies.** §28.26 carves this out — campaign-of-campaigns gets ambiguous fast; milestone ladders are the level above.
- **Auto-completion of campaigns whose end_date has passed.** Would let campaigns close without retros, defeating the retrospective discipline.
- **Auto-draft of monthly reviews on a cron.** Anti-anxiety stance carried from §28.25 profile audit; reminder banners surface, Daniel triggers.
- **Auto-publishing on the calendar's scheduled times.** §28.10's two-step confirmation is non-negotiable; the calendar shows the schedule, the publish moment is still gated.
- **Auto-transform of inspiration on save.** Costs (token + cognitive) need to be predictable; explicit click is the loop.
- **AI/agent write access to `audit_logs`.** State-change logs are Daniel's debugging surface, not the agent's context. Same discipline as `publish_confirmation_tokens` per §28.10.
- **`agent_tool_calls`/`audit_logs` merger.** Considered, rejected — different volumes, different access patterns, different pruning policies. Two tables, clear contract.

### Long-form blogs addition (2026-05-22) — §28.31 through §28.34

Phase 6. Final consolidation step — ports CreatorOS's long-form blog system into XGrowth with explicit scope rewrite (§0 new fifth paragraph, §1 expansion paragraph, §7.1 untouched). After Phase 6, XGrowth subsumes CreatorOS's functional surface entirely; CreatorOS can be retired or kept frozen as historical archive. Four new tables, one new computed view, two new top-level views (§14.14 Blogs index, §14.15 Blog Editor), six new agent tools (tools #25–#30 in §28.4). The previously-deferred-from-MVP "V1.1 Data collection" phase (formerly §25 Phase 6) is renumbered to Phase 7.

93. **Blog production schema and state machine (§28.31).** New `blogs` table with eight-state lifecycle (`idea → outlining → drafting → editing → ready → exported → published_externally → archived`); `blog_versions` table (immutable append-only history with `is_current_for_blog` partial-unique index); `blog_exports` table (one row per export op, `content_sha256` as audit anchor); `blog_to_post_links` table (bidirectional X↔blog linkage). State machine enforced in `app/agent/blogs.py::transition_status`. `external_url` / `external_published_at` are manual — app NEVER publishes externally. (§10 `blogs`, §10 `blog_versions`, §10 `blog_exports`, §10 `blog_to_post_links`, §11 `v_blog_pipeline`, §28.31, §25 Phase 6)

94. **Blog drafting agent tools (§28.32).** Four registered tools: `#25 outline_blog`, `#26 draft_blog`, `#27 suggest_blog_edits`, `#28 generate_blog_seo_metadata`. All read the unified identity stack (niche + voice profile + voice samples + personality lore + confidence-label discipline). `suggest_blog_edits` returns structured per-paragraph suggestions for Accept/Reject/Modify — NEVER auto-applies. SEO metadata writes directly to `blogs.seo_*` columns without creating a version row (sidecar, not content). (§28.4 tool catalog, §28.32, §25 Phase 6)

95. **Blog editor view §14.15.** Three-panel layout — outline (left, editable Markdown), body (center, editable Markdown), agent panel (right, with identity readout + four action buttons + version history + linked posts). Status selector enforces legal transitions; illegal transitions surface inline error. Revert to older version creates forward-moving history. Save is atomic (body + outline + version row in one transaction). (§14.14, §14.15, §25 Phase 6)

96. **Blog exports (§28.33).** Four formats (Markdown / HTML / JSON / MDX), optional SEO frontmatter, optional repurposing-notes footer (default OFF). Atomic write-then-record contract — file write succeeds before DB rows insert; partial-state surfaces a manual-mark-resolved banner (mirrors §28.10 publish-flow reconciliation). `content_sha256` is the audit anchor for detecting later disk-side tampering. Re-export = new row, overwrites file but preserves prior export's row. No platform integration (no Substack/Ghost/WordPress/Medium API). (§28.33, §25 Phase 6)

97. **Bidirectional X ↔ blog repurposing (§28.34).** Two tools: `#29 repurpose_blog_to_x` (modes: `thread_from_sections` / `single_post_summary` / `teaser_with_link`) and `#30 repurpose_x_to_blog_idea`. blog→X outputs flow through the full Phase 5.8 drafts pipeline AND the §28.29 plagiarism guard (deterministic floor catches high overlap; high-risk blocks until override). Linkage in `blog_to_post_links` is established at ship time for blog→X (not at draft time — drafts may be discarded) and at idea creation for X→blog. (§28.34, §25 Phase 6)

Scope-rewrite anchors (load-bearing for Phase 6):

- **§0 new fifth paragraph** explicitly acknowledges Phase 6 as a content-production-surface expansion within the same single-user-local thesis. §7.1 (single-user local tool) is unchanged.
- **§1 product thesis** gains a "content-production boundary" paragraph clarifying that blogs serve the X-distribution thesis via repurposing — they're not a parallel product, they're a feeder.
- **Project name remains "X Growth Dashboard"** for now. A rename to "Distribution Dashboard" or "Personal Distribution OS" is flagged in §0 as a Daniel-only decision; the implementation doesn't depend on it. If Daniel renames, it's a one-line spec edit + a one-line README edit.
- **`§25` Phase 6 (formerly V1.1 Data collection) was renumbered to Phase 7.** All cross-references in this changelog from items 19 / 33 / 34 referencing "Phase 6" prior to this revision point at the NEW Phase 6 (Blogs); the prior Phase 6 (V1.1 Data collection) is now Phase 7. No prior changelog items reference Phase 7 — there is no historical-collision risk.

Deliberately rejected from CreatorOS's blog system for Phase 6:

- **External publish APIs (Substack / Ghost / WordPress / Medium / Hashnode).** Hard scope rule per §0 + §7.1 — local tool, single user. Daniel publishes externally by hand using the exported file.
- **Auto-publish at scheduled time.** No scheduling for blogs at all; the moment between "exported file on disk" and "live on the web" is intentionally Daniel-mediated.
- **Multi-author / shared drafts / comments.** Single-user, no exceptions.
- **Newsletter integration / RSS feeds.** Out of scope.
- **Drafting auto-trigger on idea creation.** Every agent invocation is explicit Daniel-click; cost and intent stay predictable.
- **`suggest_blog_edits` auto-apply.** Collapses authorial judgment; per-paragraph Accept/Reject/Modify is the loop.
- **Auto-status transitions on agent action.** Running `outline_blog` doesn't auto-transition `idea → outlining`; Daniel decides.

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
