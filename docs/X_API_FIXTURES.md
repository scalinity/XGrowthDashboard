# X API fixtures (Phase 8 write surface)

Phase 8 write-side tests run against canned X API responses stored as
YAML cassettes under `tests/fixtures/x_api/`. The cassettes are loaded
by a small subprocess-aware fixture loader (`tests/_xurl_fixture.py`)
that patches `subprocess.run` so `app/x_client.request()` and
`app/x_client.publish_post_to_x_via_api()` get the canned response
without making a real network call.

This document covers (1) the cassette file format, (2) the
`scripts/rerecord_x_api_fixtures.py` re-record procedure, and (3) the
sandbox-cleanup discipline.

---

## 1. Why custom cassettes, not native vcr.py?

vcr.py instruments Python HTTP client libraries (`requests`, `httpx`,
`urllib3`). `app/x_client.py` shells out to the `xurl` CLI binary —
vcr.py never sees those HTTP calls because they happen in a separate
process.

Two paths exist:

* **Custom fixture loader (chosen).** Cassettes are vcr.py-compatible
  YAML files; the loader patches `subprocess.run` so a matching
  request returns the cassette's recorded body without invoking
  `xurl`. Honors the spec's "shells out to xurl" promise verbatim and
  keeps the Phase 7 read wrapper unchanged.
* Direct HTTP via `requests` with manual OAuth 1.0a signing — would
  let vcr.py work natively but would diverge from Phase 7's transport
  and require maintaining OAuth signing code in-house. Rejected.

`vcrpy` is still listed in `pyproject.toml [dev-dependencies]` because
the cassette YAML shape is compatible with vcr.py's, which keeps the
door open if we ever migrate transports.

---

## 2. Cassette file layout

**Canonical list:** the cassettes that exist are exactly those under
`tests/fixtures/x_api/*.yaml` — run `ls tests/fixtures/x_api/` (or
`uv run python -m scripts.rerecord_x_api_fixtures --dry-run` for the
re-recordable subset) for the current set.

**Per-cassette doc lives in the cassette YAML itself** (top-of-file
comment block), not duplicated here — that way the doc moves with
the cassette and can't drift. As of this writing:

```text
tests/fixtures/x_api/
  publish_post_success_200.yaml          — POST /2/tweets returning data.id (recordable)
  publish_reply_success_200.yaml         — POST /2/tweets with reply.in_reply_to_tweet_id (recordable)
  publish_rate_limit_429.yaml            — 429 with Retry-After (hand-maintained)
  publish_cold_reply_403.yaml            — 403 with X cold-reply error body (hand-maintained)
  publish_server_error_500.yaml          — 500 with no body (hand-maintained)
  publish_timeout.yaml                   — sentinel that triggers subprocess.TimeoutExpired (hand-maintained)
  recent_tweets_match.yaml               — GET /2/users/me/tweets for crash-recovery (recordable)
```

When adding a new cassette: also add a one-line top-of-file YAML
comment describing what response shape it captures, and if it's
re-recordable, append it to `_RECORDABLE_CASSETTES` in
`scripts/rerecord_x_api_fixtures.py`. The script is the source of
truth for which cassettes can be re-recorded automatically; this doc
is the source of truth for cassette semantics and the safety rules.

Each YAML cassette has this shape:

```yaml
interactions:
  - request:
      method: POST
      uri: /2/tweets
      body:
        text: "phase 8 smoke"
    response:
      status_code: 200
      body:
        data:
          id: "1234567890"
          edit_history_tweet_ids: ["1234567890"]
          text: "phase 8 smoke"
```

For the timeout sentinel cassette, the `response` block is replaced
with `raise: subprocess.TimeoutExpired` so the loader knows to raise
the matching exception when `subprocess.run` is called.

---

## 3. Re-record procedure

When X API contracts change (response shape, error envelope keys,
status codes), re-record the cassettes. The script lives at
`scripts/rerecord_x_api_fixtures.py`.

```bash
uv run python -m scripts.rerecord_x_api_fixtures
```

The script:

1. Confirms `xurl` is installed AND `xurl auth login` has been run
   with `tweet.write` scope (Phase 8 §8 of `docs/X_API_SETUP.md`).
2. Prompts for confirmation that you understand it will post real
   tweets to your authenticated X account.
3. For each cassette name, runs the corresponding real `xurl` call
   and writes the response into the YAML file under `tests/fixtures/x_api/`.
4. For the cold-reply and rate-limit cassettes, the script does NOT
   try to provoke a 403 / 429 on demand — those YAML files are
   hand-maintained because X doesn't surface those statuses
   deterministically.
5. **Auto-deletes every real tweet it posted via `DELETE /2/tweets/{id}`
   before exit.** A failed delete is a script-level FAILURE — the
   script exits non-zero and surfaces the orphaned tweet ID so you can
   manually delete it via the X web UI.

The success-path cassettes (`publish_post_success_200`,
`publish_reply_success_200`, `recent_tweets_match`) re-record without
manual intervention. The error-path cassettes
(`publish_rate_limit_429`, `publish_cold_reply_403`,
`publish_server_error_500`, `publish_timeout`) are hand-maintained
because X doesn't surface them deterministically on demand.

---

## 4. The timeout cassette

vcr.py cannot record a genuine `subprocess.TimeoutExpired` — there is
no HTTP transaction to play back. The cassette uses a sentinel:

```yaml
interactions:
  - request:
      method: POST
      uri: /2/tweets
      body:
        text: "phase 8 timeout"
    response:
      raise: subprocess.TimeoutExpired
      timeout_seconds: 30
```

The fixture loader (`tests/_xurl_fixture.py`) sees `raise:
subprocess.TimeoutExpired` and raises that exception from
`subprocess.run`, which `app/x_client.request()` catches and converts
to `XApiTimeoutError`. The publish wrapper's `except XApiTimeoutError`
branch then ROLLBACKs the transaction and the crash-recovery scan
takes over.

---

## 5. Safety rules

* **Never run the re-record script against your production X account
  without reviewing the cassette names first.** The script always
  posts AND deletes, but a network failure between post and delete
  leaves a live tweet on your timeline.
* **Use a dedicated sandbox X account if you can.** Daniel's setup
  uses his real account because he's a single-user system; the script
  still enforces the post/delete pair.
* **The committed cassettes contain no real tweet IDs.** Re-record
  output replaces the IDs in place; verify `git diff` before
  committing to ensure no sensitive payload (rare, but possible)
  leaked into the YAML.
* **CI never re-records.** CI uses the committed cassettes verbatim
  and asserts they match the in-flight request via the fixture
  loader's request-matcher.

---

## 6. Cross-references

- `docs/X_API_SETUP.md` §8 — Phase 8 write-scope augmentation.
- `app/x_client.py::publish_post_to_x_via_api` — the wrapped X API call.
- `app/agent/publish.py::publish_post_atomic` — branches on
  `publish_via_api_enabled`; the wrapper that consumes these
  cassettes via the test fixture loader.
- `tests/_xurl_fixture.py` — the cassette loader + `subprocess.run`
  patcher used by `tests/test_x_api_writes.py` and the existing
  publish-flow tests in `tests/test_agent.py`.
