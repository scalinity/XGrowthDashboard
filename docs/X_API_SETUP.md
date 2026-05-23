# X API setup (Phase 7)

The dashboard reaches the X API via [`xurl`](https://github.com/xdevplatform/xurl), an
official command-line client that holds Daniel's OAuth refresh tokens
under `~/.xurl/` and attaches the bearer header on every invocation.
The dashboard never touches the raw tokens; `app/x_client.py` shells
out to `xurl` as a subprocess.

This document covers Phase 7's read scope only. Phase 8 reuses the same
xurl auth state; Phase 8's install step is one extra `xurl auth login`
run that augments the granted scope set with `tweet.write`.

---

## 1. Install xurl

macOS (the only supported platform per CLAUDE.md):

```bash
brew install xurl
```

Verify the binary is on `PATH`:

```bash
which xurl
xurl --version
```

The dashboard's `app/x_client.is_available()` returns `True` when
`shutil.which("xurl")` resolves; the Settings → Data sources panel
surfaces the result alongside the auth-state check below.

> **Tests / CI:** `XURL_BIN` env var overrides the binary path so
> fixture-driven test runs can point at a fake script that emits canned
> JSON. Production sets `XURL_BIN` unset and the default `xurl` binary
> on `PATH` is used.

---

## 2. Authenticate (one-time)

```bash
xurl auth login
```

When prompted for scopes, paste this exact string:

```
tweet.read users.read offline.access
```

Walk-through:

- `tweet.read` — read public tweets + their metrics.
- `users.read` — read user profiles (bio, follower count).
- `offline.access` — issue a refresh token so the dashboard's scheduled
  jobs don't prompt Daniel mid-run.

After consent, xurl writes the refresh token to `~/.xurl/config.toml`.
That file is `chmod 600` by default and must NEVER be checked into git
or copied to a shared machine. `app/x_client.py` does not read this
file directly — `xurl` is the only process that ever sees the token.

### Phase 8 forward-pointer

When Phase 8 (X API writes, migration 019) lands, re-run:

```bash
xurl auth login
```

and add `tweet.write` to the scope string:

```
tweet.read tweet.write users.read offline.access
```

xurl will refresh the granted scope set. Same `~/.xurl/config.toml`
file; same auth surface.

---

## 3. Smoke-test the auth state

```bash
xurl /2/users/me
```

Expected: a JSON envelope under `{"data": {"id": "...", "username": "..."}}`.

Failure modes:

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `command not found: xurl` | `brew install` didn't add to PATH | `brew doctor`, restart shell |
| JSON body with `"status": 401` | refresh token expired or scopes missing | re-run `xurl auth login` |
| HTML / non-JSON stdout | upstream X API outage or proxy interfering | retry after a minute |

The dashboard treats every non-2xx response as a failure surfaced in
the Settings → Recent X API failures panel; the manual-fallback path
(paste-driven entry) remains the always-available alternative.

---

## 4. Authorize launchd plists (optional, per-job)

`docs/SCHEDULED_JOBS.md` documents the four Phase 7 plists. The plists
ship in `launchd/` but are **NOT** auto-loaded — running `launchctl
load …` on each one is a deliberate Daniel-consent step per CLAUDE.md.
Auth state under `~/.xurl/` is inherited by the plist's job because
launchd jobs run as Daniel's user.

---

## 5. Endpoints the dashboard hits in Phase 7

| Endpoint | Caller |
| --- | --- |
| `GET /2/users/me?user.fields=public_metrics` | `scripts/collect_account_snapshot.py` (§17 Phase 7 job #1) |
| `GET /2/users/me/tweets?max_results=100` | `scripts/import_recent_posts.py` (§17 job #2) |
| `GET /2/tweets?ids=<batch>&tweet.fields=public_metrics,non_public_metrics` | `app/jobs/post_metrics_refresh.py` (§17 job #3) |
| `GET /2/tweets?ids=<batch>&tweet.fields=public_metrics` | `app/jobs/reply_target_metrics_refresh.py` (§17 job #4) |
| `GET /2/tweets/search/recent?query=conversation_id:<id>` | §28.20 replier-pool `auto_scan` |
| `GET /2/users/by/username/<handle>?user.fields=description,public_metrics` | §28.24 Account Researcher `auto_pull` + §28.25 Profile Audit bio |
| `GET /2/users/<id>/tweets?max_results=20` | §28.24 Account Researcher recent-posts |

All seven endpoints share the same xurl wrapper, the same OAuth scope
set, and the same rate-limit handling (`x_api_rate_limit_window_minutes`
setting, default 15). Failures land on `raw_api_responses` with the
endpoint string and the inferred HTTP status code.

---

## 6. Rotating the auth state

If Daniel's X account password changes, or his app credentials are
re-issued from X developer portal:

```bash
xurl auth logout
xurl auth login          # re-paste the scope string from §2 above
```

No dashboard restart needed — the next subprocess invocation picks up
the new token.

---

## 7. What this file is NOT

- Not Phase 9 (Grok / xAI). `XAI_API_KEY` lives in `.env`, not
  `~/.xurl/`; see Phase 9 docs when migration 020 lands.
- Not Phase 8 write-side fixture recording. `scripts/rerecord_x_api_fixtures.py`
  (Phase 8) covers that.
- Not a general OAuth tutorial — refer to X developer portal for the
  app-registration prerequisites (creating a developer project and an
  app + redirect URI) that xurl assumes are already in place.
