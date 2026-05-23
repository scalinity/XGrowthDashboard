# Scheduled jobs (Phase 7)

Phase 7 ships four background jobs that pull from the X API on a
schedule. Each has a corresponding `.plist` file under `launchd/`
that's **NOT auto-loaded** — running `launchctl load …` on each one is
a deliberate Daniel-consent step per CLAUDE.md user-consent discipline
and §17 Phase 7. Until you load them, the dashboard works exactly as
it did pre-Phase-7 (manual entry forms remain the always-available
fallback regardless of plist state).

This document is the per-job runbook: prerequisites, exact `launchctl`
invocations, behavior on the various failure modes, and the unload
procedure.

---

## Prerequisites (once, before loading any plist)

1. **xurl installed + authenticated.** See [`X_API_SETUP.md`](X_API_SETUP.md).
   The smoke test (`xurl /2/users/me`) must return a JSON envelope before
   any plist is loaded — otherwise the jobs hit a 401 wall and waste
   `raw_api_responses` rows.

2. **Migration 018 applied.** Run `uv run python -m scripts.init_db` once.
   The `data_collection_mode` setting flips to `'api'` on this run.

3. **`data/logs/` directory exists.** All four plists write `.out.log`
   and `.err.log` under `data/logs/`; if the directory doesn't exist
   launchd fails the job with "no such file or directory":

   ```bash
   mkdir -p data/logs
   ```

4. **One-shot post backfill.** Before the daily import job lands, run
   the backfill once so existing posts get tracked:

   ```bash
   uv run python -m scripts.import_recent_posts --backfill
   ```

   Idempotent — re-running exits 0 with `skipped_reason='already_ran'`.

---

## The four jobs at a glance

| Job | Cadence | Endpoint | DB write surface |
| --- | --- | --- | --- |
| `collect-account-snapshot` | Daily 09:00 ET | `/2/users/me` | `account_snapshots` |
| `import-recent-posts` | Daily 09:05 ET | `/2/users/me/tweets?max_results=100` | `posts` (new only) |
| `post-metrics-refresh` | Hourly | `/2/tweets?ids=<batch>&tweet.fields=public_metrics,non_public_metrics,organic_metrics` | `post_metric_snapshots` + `posts.last_metrics_refresh_at_utc` |
| `reply-target-metrics-refresh` | Hourly | `/2/tweets?ids=<batch>&tweet.fields=public_metrics,created_at` | `reply_target_snapshots` + `reply_targets.{like_count,reply_count,…,velocity_score,timing_score,recommended_action_*,last_checked_at_utc}` |

Each job writes a `scheduled_job` row to `audit_logs` at run-end with
the per-run summary (rows touched, rate-limit hits, runtime). The
Settings → "Recent scheduled-job activity" panel reads from there.

---

## Loading each plist

The pattern is identical for all four — `cp` the plist into
`~/Library/LaunchAgents/`, then `launchctl load` it. macOS validates
the XML at load time; a malformed plist surfaces as a `launchctl`
error.

```bash
# Phase 7 job #1 — daily account snapshot (09:00).
cp launchd/com.scalinity.xgrowth.collect-account-snapshot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.collect-account-snapshot.plist

# Phase 7 job #2 — daily post import (09:05).
cp launchd/com.scalinity.xgrowth.import-recent-posts.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.import-recent-posts.plist

# Phase 7 job #3 — hourly post metrics refresh.
cp launchd/com.scalinity.xgrowth.post-metrics-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.post-metrics-refresh.plist

# Phase 7 job #4 — hourly reply-target metrics refresh.
cp launchd/com.scalinity.xgrowth.reply-target-metrics-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.reply-target-metrics-refresh.plist
```

Verify each one is loaded:

```bash
launchctl list | grep com.scalinity.xgrowth
# Output: PID  Status  Label
```

A `Status` of `0` means "loaded and waiting for next trigger"; the
PID column is empty until the job is actively running.

---

## Triggering a job ad-hoc (without waiting for cron)

Each module is also a CLI:

```bash
uv run python -m scripts.collect_account_snapshot
uv run python -m scripts.import_recent_posts             # daily incremental
uv run python -m scripts.import_recent_posts --backfill  # one-shot (idempotent)
uv run python -m app.jobs.post_metrics_refresh
uv run python -m app.jobs.reply_target_metrics_refresh
```

These run in your interactive shell, log to stdout, and produce the
same `audit_logs` row as a launchd-triggered run. Useful when
debugging a failed run from `data/logs/*.err.log`.

---

## Behavior on failure modes

| Mode | What the job does | What you see |
| --- | --- | --- |
| `data_collection_mode = 'manual'` | Exits 0 immediately, no API call | `skipped_reason='data_collection_mode=manual'` in audit row |
| xurl not on PATH | Exits 1 | `XApiUnavailable` in audit `error` field |
| `xurl auth login` expired (401) | Exits 1, no DB write | `XApiUnavailable: X API returned 401 …` in audit row |
| X API 429 (rate limit) | `last_checked_at_utc` stays at previous value; no score drift | `rate_limit_hits=1` in audit row + the 429 details in `raw_api_responses` |
| X API 404 on a candidate's `target_x_post_id` (reply-target job only) | Transitions `status='target_deleted'`; writes `data/reply_target_marked_deleted` audit row | `candidates_marked_deleted=N` in the sweep's audit row |
| Network timeout | Exits 1 after 30s subprocess timeout | `XApiUnavailable: xurl call timed out` in audit row |
| Schema mismatch (e.g., new columns introduced and migration not applied) | IntegrityError on insert | Job-level audit row carries the SQLite error; **re-run `init_db`** |

In every failure mode the manual fallback paths remain available —
the Today form for account snapshots, the Reply Target Queue's manual
"Add candidate" affordance, the §28.20/§28.24/§28.25 paste flows.

---

## Unloading (disable a job)

```bash
launchctl unload ~/Library/LaunchAgents/com.scalinity.xgrowth.<job>.plist
rm ~/Library/LaunchAgents/com.scalinity.xgrowth.<job>.plist
```

The unload is symmetric: re-loading restores the original cadence
from the plist (StartCalendarInterval or StartInterval). Daniel can
selectively disable any subset of the four jobs at any time without
affecting the others.

The dashboard does NOT auto-disable launchd plists when
`data_collection_mode='manual'` — the plist's own job logic checks
the setting and no-ops. If you want to fully stop the jobs from
firing (instead of having them fire and no-op), unload them.

---

## Disk usage + log rotation

Each job appends to its own `.out.log` and `.err.log` under
`data/logs/`. The output is bounded:

- `.out.log` per run: ~200–800 bytes (a single Python `_LOG.info` line)
- `.err.log` per run: bytes on failure (a traceback); empty on success

Across 4 jobs × ~25 runs/day = ~100 lines/day of stdout. A year is
under 10 MB. If you want hard rotation, add a one-line cron to
truncate the .log files weekly:

```cron
0 0 * * 0 find /Users/danny/Documents/Codez/Apps/XGrowthDashboard/data/logs -name '*.log' -size +1M -delete
```

This is optional — the volume is low enough that manual `rm` once a
year suffices.

---

## Phase 9 Grok discovery sweep

Phase 9 (migration **021**; the original spec said 020 but
`020_force_drafted_reason_required.sql` had already shipped, so the
Grok migration landed at 021) adds a fifth scheduled job:
`launchd/com.scalinity.xgrowth.grok-sweep.plist`.

It calls xAI Grok with `search_parameters.sources=[{"type":"x"}]` for
firehose discovery (§29.12), then verifies every returned candidate
against the X API via Phase 7's xurl wrapper (§29.2 source-of-truth
invariant) before inserting into `reply_targets` with
`discovered_via='grok_semantic'`.

### Prerequisites

1. **Migration 021 applied.** Re-run `uv run python -m scripts.init_db`
   if you haven't, or `uv run pytest tests/test_schema.py -q` to verify.

2. **`XAI_API_KEY` in `.env` AND in the plist.** The dashboard process
   reads `.env`; launchd doesn't inherit it. Get a key at
   https://console.x.ai/ and paste the value into both places:

   - `.env` (next to `ANTHROPIC_API_KEY`).
   - The `EnvironmentVariables` dict in the plist (replace
     `REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD`).

3. **Phase 7 prerequisite — `xurl` installed + authenticated.** The
   §29.2 verification call uses Phase 7's xurl wrapper. If xurl isn't
   ready, every Grok candidate gets rejected on the X API call and the
   sweep produces noise without value.

4. **`grok_query_list_json` populated.** Empty list = no Grok calls
   happen even with `grok_api_enabled=TRUE`. Populate via Settings →
   Reply Targets → Grok queries.

### Enable

P9R-18 — the checked-in plist is `…grok-sweep.plist.example` with the
`REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD` placeholder. The operator copy
(real key pasted in) lives at `~/Library/LaunchAgents/…grok-sweep.plist`
and is `.gitignore`'d so a stray `git add launchd/` can't ship the
live key to public history. There's also a local
`launchd/com.scalinity.xgrowth.grok-sweep.plist` slot you can use as a
working copy outside the LaunchAgents directory if you prefer; that
path is gitignored too.

```bash
# 1. Copy the template into LaunchAgents.
cp launchd/com.scalinity.xgrowth.grok-sweep.plist.example \
   ~/Library/LaunchAgents/com.scalinity.xgrowth.grok-sweep.plist

# 2. Replace the placeholder with the real key (edit in place — DO NOT
#    edit the checked-in .example file).
${EDITOR:-vi} ~/Library/LaunchAgents/com.scalinity.xgrowth.grok-sweep.plist

# 3. Load.
launchctl load ~/Library/LaunchAgents/com.scalinity.xgrowth.grok-sweep.plist
```

The first sweep happens at the next `StartInterval` tick (default 120
minutes after `launchctl load`). To run immediately, use Settings →
Reply Targets → Grok queries → "Run sweep now".

### Disable

```bash
launchctl unload ~/Library/LaunchAgents/com.scalinity.xgrowth.grok-sweep.plist
```

Or flip `grok_api_enabled=FALSE` in Settings (the sweep code reads
the flag at start and aborts if disabled, so the launchd cadence
becomes a no-op without unloading the plist).

### Failure modes

| Mode | Behavior |
|---|---|
| `XAI_API_KEY` missing | `GrokUnavailable` raised; sweep aborts with the audit-log row recording the error. No Grok call fires; no row written to `grok_api_responses` (configuration failure, not API failure). |
| Grok 429 | Sweep pauses, respects `Retry-After`, resumes. In-progress-but-unverified candidates dropped (re-discovered next sweep). Row written to `grok_api_responses` with `rejection_reason='rate_limit_429'`. |
| Grok 5xx after retry | Sweep skips the affected query and continues. Row written with `rejection_reason='http_error_5xx'`. |
| §28.6 combined ceiling hit | `GrokCostCeilingError` raised pre-call. Sweep aborts. Row written with `rejection_reason='cost_ceiling_hit'`. Settings banner displays "Grok paused: combined AI ceiling hit." Anthropic agent calls also pause per §28.6. |
| §29.2 verification 404 | Candidate dropped (post deleted between Grok discovery and our verification). Row written with `rejection_reason='verification_404'`. No `reply_targets` insert. |
| Dedupe: same `target_x_post_id` already in `reply_targets` | Silently dropped at insert via the `unique(target_x_post_id)` partial index. Sweep continues. Original `discovered_via` (manual / agent_score / earlier sweep) preserved. |

### Cost ceiling — combined Anthropic + xAI

The §28.6 ceiling is shared infrastructure: one setting
(`combined_ai_monthly_cost_ceiling_usd`, default $30) gates BOTH the
Anthropic agent calls AND the Grok sweep. At 100%:

- `app/grok_client.py` refuses new calls (the preflight check raises
  `GrokCostCeilingError`).
- `app/agent/client.py` refuses new calls (the same check raises
  `MonthlyCostCeilingExceeded`).

Settings → Reply Targets → "Combined AI spend this month" shows the
Anthropic + xAI split with a progress bar against the cap.

### §29.2 verification invariant

Grok is **never** the source of truth for any engagement metric. Every
candidate Grok returns is verified against the X API
(`xurl /2/tweets/{id}?tweet.fields=public_metrics,non_public_metrics`)
before any score touches `engagement_surface_score`. On X API 404 the
candidate is rejected; on X API 200 the candidate flows through §29.3
scoring with X API metrics as the source of truth — NOT Grok's
`observed_metrics`. The invariant lives in
`app/agent/reply_targets.py::verify_grok_candidate_against_x_api`.
