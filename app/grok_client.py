"""xAI Grok API wrapper — Phase 9 firehose discovery (spec §29.12).

Grok firehose discovery is a third reply-target source alongside
manual paste (§29.6 MVP) and curated `agent_target_accounts` (§28.4 #7).
It exists for one reason: Daniel's lanes have semantically-related X
posts that don't surface via Daniel's hand-written xurl saved-searches
or his curated account list. Grok's value is **discovery breadth via
real-time X firehose**, not measurement. The §29.2 source-of-truth
invariant is what keeps Grok safely scoped — every candidate this
module surfaces MUST be verified against the X API (Phase 7's xurl
wrapper) by ``app/jobs/grok_discovery_sweep.py`` before any score
affects ``engagement_surface_score``. Grok is NEVER the source of
truth for any engagement metric.

xAI API shape — confirmed against https://docs.x.ai/docs/api-reference
and https://docs.x.ai/docs/models on 2026-05-23:

  * Endpoint: ``POST https://api.x.ai/v1/chat/completions`` (OpenAI-
    compatible chat-completions surface).
  * Auth: ``Authorization: Bearer $XAI_API_KEY`` header.
  * Model: ``grok-4.3`` — 1M context window, $1.25/M input, $2.50/M
    output. Recommended general-purpose model per the docs.
  * Live Search via ``search_parameters`` object:
        {
          "mode": "on",
          "sources": [{"type": "x"}],   # restrict to X firehose
          "max_search_results": 50,     # int cap on results
          "return_citations": true      # X post URLs in response
        }
  * Response shape:
        {
          "id": "...", "created": <epoch>, "model": "grok-4.3",
          "choices": [{"index": 0, "message": {"content": "..."},
                       "finish_reason": "stop"}],
          "citations": ["https://x.com/<handle>/status/<id>", ...],
          "usage": {"prompt_tokens": N, "completion_tokens": M,
                    "total_tokens": N+M}
        }
  * Rate limits: standard HTTP 429 with ``Retry-After`` header in
    seconds (xAI doesn't publish a public limits table, so we treat
    429 + Retry-After as the only reliable signal).

The X post URLs Grok returns in ``citations`` are the candidates this
module produces. Each candidate has only ``target_x_post_id`` +
``target_author_handle`` + ``target_post_url`` populated; metrics
(``like_count``, etc.) come from the §29.2 X API verification call,
NOT from Grok. Per §29.12: Grok's job is discovery, not measurement.

Every call (success OR error) is logged to ``grok_api_responses``
(migration 021) so the Settings "Recent Grok failures (last 7 days)"
panel can surface non-2xx outcomes and the audit trail covers what
queries we ran.

Combined Anthropic + xAI spend is gated at the API client layer per
§28.6: this module calls ``cost.is_combined_ceiling_breached()``
before each ``POST /v1/chat/completions`` request. At 100% the
function raises ``GrokCostCeilingError`` and the sweep aborts mid-
run; Anthropic agent calls also pause per the same combined ceiling
(see ``app/agent/cost.py::check_ceiling_or_raise``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import random

from app.agent import cost

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants.
# ---------------------------------------------------------------------------
# xAI chat completions endpoint (OpenAI-compatible). Confirmed against
# https://docs.x.ai/docs/api-reference 2026-05-23.
GROK_ENDPOINT: str = "https://api.x.ai/v1/chat/completions"

# Recommended general-purpose model per https://docs.x.ai/docs/models —
# "For everything else, use Grok 4.3. It is the most intelligent and
# fastest model we've built." 1M-token context, supports Live Search
# with source type "x" for X firehose discovery.
DEFAULT_GROK_MODEL: str = "grok-4.3"

# Caller-tunable defaults; the discovery sweep overrides per query.
DEFAULT_MAX_RESULTS: int = 50
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Bounded retry on 5xx — matches the x_client.py pattern.
_DEFAULT_RETRY_ATTEMPTS: int = 2
_DEFAULT_RETRY_SLEEP_SECONDS: float = 0.5

# Hard cap on the in-job rate-limit retry wait. launchd ExitTimeOut is
# 300s per the Phase 7 plists; sleeping longer than that would risk
# SIGKILL between an audit-row INSERT and the follow-up logic. Mirrors
# the cap in app/x_client.py::_MAX_RATE_LIMIT_WAIT_SECONDS.
_MAX_RATE_LIMIT_WAIT_SECONDS: float = 90.0

# Projected per-call cost guess for the §28.6 preflight. Grok-4.3 is
# $1.25/$2.50 per million tokens; a typical search call costs <$0.01.
# We use $0.02 as a defensive guess that won't slip past the cap by a
# rounding error. P9R-13: prefixed with GROK_ so it doesn't shadow
# app.agent.cost.PROJECTED_CALL_COST_GUESS_USD (Anthropic side, $0.05)
# — same name, different value pre-fix.
GROK_PROJECTED_CALL_COST_GUESS_USD: float = 0.02

# Kept as a name alias for backward compatibility with any external
# caller that imported the bare name. Internal Phase 9 code now uses
# GROK_PROJECTED_CALL_COST_GUESS_USD exclusively.
PROJECTED_CALL_COST_GUESS_USD: float = GROK_PROJECTED_CALL_COST_GUESS_USD

# Status-code based rejection_reason categorization (matches the CHECK
# constraint in migration 021 on grok_api_responses.rejection_reason).
_REJECTION_RATE_LIMIT: str = "rate_limit_429"
_REJECTION_COST_CEILING: str = "cost_ceiling_hit"
_REJECTION_5XX: str = "http_error_5xx"
_REJECTION_OTHER: str = "http_error_other"

# X post URL → (handle, post_id) extraction. Matches both x.com and
# twitter.com hostnames (Grok cites either). Used to parse the
# citations array into typed candidates. P9R-15: trailing alternation
# now also accepts '#' so fragment-anchored URLs (e.g.
# https://x.com/foo/status/123#m) don't silently drop.
_X_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:x|twitter)\.com/"
    r"(?P<handle>[A-Za-z0-9_]{1,15})/status/(?P<post_id>\d+)"
    r"(?:/|$|\?|#)"
)


# ---------------------------------------------------------------------------
# Exceptions.
# ---------------------------------------------------------------------------
class GrokError(Exception):
    """Base for xAI Grok API failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GrokUnavailable(GrokError):
    """API key missing, network error, or process-level failure."""


class GrokRateLimitError(GrokError):
    """xAI returned 429. ``retry_after_seconds`` honors the header hint."""

    def __init__(
        self, message: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


class GrokCostCeilingError(GrokError, cost.MonthlyCostCeilingExceeded):
    """§28.6 combined Anthropic + xAI ceiling reached — refuse new call.

    Raised by the preflight check before the HTTP request fires. No
    Grok call is made; the audit row is logged with
    ``rejection_reason='cost_ceiling_hit'`` and zero token usage.

    P9R-44: multiple-inherits BOTH ``GrokError`` (so the sweep's
    ``except grok_client.GrokError`` ladder still routes it) AND
    ``cost.MonthlyCostCeilingExceeded`` (so a caller that uses the
    Anthropic-side ceiling-exception name catches the cross-provider
    invariant uniformly). Pure marker — no new fields.
    """


class GrokServerError(GrokError):
    """xAI returned 5xx after the bounded retry budget was exhausted."""


# ---------------------------------------------------------------------------
# Candidate shape.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GrokCandidate:
    """One X post Grok returned as a reply-target candidate.

    Per §29.12: Grok provides only the URL + handle + post_id. Metrics
    come from the §29.2 X API verification call in
    ``app/jobs/grok_discovery_sweep.py``. ``observed_metrics`` here is
    deliberately empty — included only for forward-compat with the
    spec text shape.

    P9R-55 — DO NOT CONSUME ``observed_metrics`` IN ANY SCORE PATH.
    The field exists ONLY because the §29.12 spec text named it as
    part of the candidate-dict shape. Grok is discovery, not
    measurement — every metric on a reply_targets row MUST come from
    the X API verification step (§29.2). A future maintainer who
    looks at the empty dict and thinks "I'll just trust Grok's
    numbers this once" defeats the load-bearing source-of-truth
    invariant. The Phase 9 happy-path test (
    test_happy_path_grok_candidate_ingestion) deliberately passes
    ``observed_metrics={"like_count": 99}`` and asserts the stored
    value is the X API's (42) — that test will fail loudly if
    anyone wires observed_metrics into the score path.
    """

    target_x_post_id: str
    target_post_url: str
    target_author_handle: str
    target_text: str | None = None
    observed_metrics: dict[str, Any] = field(default_factory=dict)
    grok_relevance_rationale: str | None = None


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
# P9R-5: placeholder strings the launchd plist + docs use as
# substitutions Daniel must replace before `launchctl load`. is_configured
# returns False (and search() raises GrokUnavailable) when any of these
# are observed in XAI_API_KEY — otherwise the launchd job would silently
# 401 forever and the Settings status would show green "configured".
_PLACEHOLDER_API_KEY_VALUES: frozenset[str] = frozenset({
    "REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD",
    "YOUR_XAI_API_KEY_HERE",
    "your-xai-key",
})


def _is_placeholder_key(key: str) -> bool:
    """True if ``key`` looks like a documented placeholder, not a real key."""
    if not key:
        return True
    stripped = key.strip()
    if stripped in _PLACEHOLDER_API_KEY_VALUES:
        return True
    upper = stripped.upper()
    return (
        upper.startswith("REPLACE_WITH_")
        or upper.startswith("YOUR_")
        or "PLACEHOLDER" in upper
    )


def is_configured() -> bool:
    """Return True if ``XAI_API_KEY`` is set AND not a placeholder string.

    Used by the Settings UI to render a "configured / not set" status
    indicator WITHOUT exposing the key value (§29.12 + §18 item 19).

    P9R-5: the launchd plist ships with the placeholder
    ``REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD`` so Daniel can `cp` it into
    `~/Library/LaunchAgents/` and only then paste the real key. A naive
    `bool(env.strip())` check would mark the placeholder as "configured"
    and the launchd job would silently 401 forever. We reject the
    documented placeholders (and the `REPLACE_WITH_…` / `YOUR_…` /
    `…PLACEHOLDER…` shapes more generally) so the UI tells the truth.
    """
    raw = os.environ.get("XAI_API_KEY", "")
    return bool(raw.strip()) and not _is_placeholder_key(raw)


def search(
    query: str,
    *,
    conn: sqlite3.Connection | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    model: str = DEFAULT_GROK_MODEL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
    api_key: str | None = None,
    endpoint: str | None = None,
) -> list[GrokCandidate]:
    """Run one Grok-with-X-firehose search and return candidate dicts.

    Phase 9 §29.12 entry point. Workflow:

      1. §28.6 preflight — if the combined Anthropic + xAI monthly
         spend already meets the ceiling, raise ``GrokCostCeilingError``
         immediately and log the rejection to ``grok_api_responses``
         (no HTTP call fires).
      2. ``POST /v1/chat/completions`` with ``search_parameters.sources
         = [{"type": "x"}]`` and ``return_citations = true``.
      3. Parse the response's ``citations`` array — each X post URL
         becomes one ``GrokCandidate`` with ``target_x_post_id``,
         ``target_author_handle``, and ``target_post_url`` populated.
         Metrics are deliberately NOT pulled from Grok (§29.2: Grok is
         discovery, not measurement).
      4. Write one ``grok_api_responses`` audit row covering the call,
         including ``rate_snapshot_json`` for cost reconstruction.

    Returns the list of candidates (possibly empty). Raises:

      * ``GrokUnavailable`` — ``XAI_API_KEY`` missing or network error.
      * ``GrokRateLimitError`` — 429 returned; ``retry_after_seconds``
        on the exception. Caller is responsible for the pause/resume.
      * ``GrokCostCeilingError`` — §28.6 combined ceiling reached.
      * ``GrokServerError`` — 5xx after bounded retry exhausted.

    ``conn`` is optional only so unit tests can exercise the HTTP path
    without an attached DB; production callers always pass one so the
    audit row + cost-ceiling check both run.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    key = api_key or os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        # Distinct from a network error — the caller can surface a
        # specific "set XAI_API_KEY in .env" message. NOT logged to
        # grok_api_responses (the audit table presumes a real call
        # attempt; missing-key is a configuration failure caught
        # before any request).
        raise GrokUnavailable(
            "XAI_API_KEY is not set in .env — see .env.example for the line "
            "and https://console.x.ai/ to mint a key."
        )
    # P9R-5: reject the launchd plist placeholder explicitly so the sweep
    # doesn't quietly 401-spam grok_api_responses with 50+ rows before
    # Daniel notices.
    if _is_placeholder_key(key):
        raise GrokUnavailable(
            "XAI_API_KEY looks like the documented placeholder "
            f"({key!r:.40}…). Replace it with a real key in .env AND "
            "in launchd/com.scalinity.xgrowth.grok-sweep.plist before "
            "`launchctl load`."
        )

    # §28.6 preflight — refuse the call if combined Anthropic + xAI
    # spend would breach the cap. The audit row records the refusal so
    # Settings → Recent Grok failures shows it (Daniel can disambiguate
    # ceiling-hit from rate-limit / 5xx without checking another panel).
    if conn is None:
        # P9R-26: make conn=None bypass loud. Production callers always
        # pass a conn so the ceiling check fires; this branch covers
        # narrow unit tests + future-misuse defense.
        _LOG.warning(
            "grok_client.search called with conn=None — §28.6 ceiling "
            "preflight skipped. Production callers MUST pass a conn."
        )
    elif cost.is_combined_ceiling_breached(
        conn, projected_call_cost_usd=GROK_PROJECTED_CALL_COST_GUESS_USD
    ):
        _log_grok_response(
            conn,
            query=query,
            request_payload={
                "model": model,
                "max_search_results": max_results,
                "preflight": "cost_ceiling_check",
            },
            response_status_code=None,
            response_body=None,
            rate_snapshot=None,
            rejection_reason=_REJECTION_COST_CEILING,
            duration_ms=0,
        )
        raise GrokCostCeilingError(
            "Phase 9 Grok call refused — §28.6 combined Anthropic + xAI "
            "monthly ceiling has been reached. Raise the cap in Settings "
            "or wait until the next month."
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a discovery assistant that surfaces X posts "
                    "matching a user's natural-language query. Return only "
                    "the live citations — the X post URLs are what the "
                    "caller needs. Do not summarize or invent posts."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Find recent X posts matching this query and return "
                    f"the post URLs as citations: {query}"
                ),
            },
        ],
        "search_parameters": {
            "mode": "on",
            "sources": [{"type": "x"}],
            "max_search_results": int(max_results),
            "return_citations": True,
        },
    }
    url = endpoint or GROK_ENDPOINT

    # P9R-31: clamp negative retry_attempts so the loop body always
    # runs at least once. Pre-fix, a caller passing retry_attempts=-1
    # got an immediate fallthrough to "Grok server error after retry
    # budget exhausted" with no actual server error.
    retry_attempts = max(0, retry_attempts)

    last_server_error: GrokError | None = None
    attempt = 0
    started_total = time.perf_counter()
    while attempt <= retry_attempts:
        started = time.perf_counter()
        try:
            response_status, response_body, retry_after = _http_post_json(
                url=url, payload=payload, api_key=key, timeout_seconds=timeout_seconds
            )
        except GrokUnavailable as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _log_grok_response(
                conn,
                query=query,
                request_payload=payload,
                response_status_code=None,
                response_body=None,
                rate_snapshot=None,
                rejection_reason=_REJECTION_OTHER,
                duration_ms=duration_ms,
                notes=f"GrokUnavailable: {exc}",
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)

        if response_status == 429:
            wait = retry_after if retry_after is not None else 60.0
            wait = max(1.0, float(wait))
            _log_grok_response(
                conn,
                query=query,
                request_payload=payload,
                response_status_code=429,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_RATE_LIMIT,
                duration_ms=duration_ms,
            )
            raise GrokRateLimitError(
                f"xAI rate-limited; retry after {wait:.0f}s.",
                retry_after_seconds=wait,
            )

        if response_status is not None and 500 <= response_status < 600:
            # P9R-4: log EVERY 5xx attempt (not just the final one) so
            # an auditor can reconstruct the retry trail. Non-final
            # attempts carry a notes field tagging the attempt index.
            is_final = (attempt + 1) > retry_attempts
            _log_grok_response(
                conn,
                query=query,
                request_payload=payload,
                response_status_code=response_status,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_5XX,
                duration_ms=duration_ms,
                notes=(
                    None if is_final
                    else f"retry attempt {attempt + 1} of {retry_attempts + 1}"
                ),
            )
            attempt += 1
            last_server_error = GrokServerError(
                f"xAI returned {response_status}: {_safe_truncate(response_body, 300)}",
                status_code=response_status,
            )
            if attempt > retry_attempts:
                break
            # P9R-4: re-check the §28.6 combined ceiling between retries.
            # Earlier 5xx attempts may have spent tokens (xAI prices at
            # inference, not response serialization); without this gate
            # the bounded retry can push past the cap.
            if conn is not None and cost.is_combined_ceiling_breached(
                conn, projected_call_cost_usd=PROJECTED_CALL_COST_GUESS_USD
            ):
                _log_grok_response(
                    conn,
                    query=query,
                    request_payload={"phase": "5xx_retry_ceiling_recheck"},
                    response_status_code=None,
                    response_body=None,
                    rate_snapshot=None,
                    rejection_reason=_REJECTION_COST_CEILING,
                    duration_ms=0,
                )
                raise GrokCostCeilingError(
                    "5xx retry refused — §28.6 combined ceiling reached "
                    "between attempts."
                )
            # P9R-53: exponential backoff with jitter. Pre-fix retries
            # fired at fixed 0.5s spacing — three clients hitting xAI
            # during a partial outage would thunder in lockstep. Cap at
            # ~5s so launchd's ExitTimeOut still has headroom.
            backoff = _DEFAULT_RETRY_SLEEP_SECONDS * (2 ** (attempt - 1))
            backoff = min(backoff, 5.0) + random.uniform(0, 0.5)
            time.sleep(backoff)
            continue

        if response_status is None or response_status >= 400:
            _log_grok_response(
                conn,
                query=query,
                request_payload=payload,
                response_status_code=response_status,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_OTHER,
                duration_ms=duration_ms,
            )
            raise GrokError(
                f"xAI returned {response_status}: "
                f"{_safe_truncate(response_body, 300)}",
                status_code=response_status,
            )

        # Success path — parse + audit.
        candidates = _parse_candidates_from_response(response_body)
        rate_snapshot = _build_rate_snapshot(response_body, model=model)
        _log_grok_response(
            conn,
            query=query,
            request_payload=payload,
            response_status_code=response_status,
            response_body=response_body,
            rate_snapshot=rate_snapshot,
            rejection_reason=None,
            duration_ms=duration_ms,
        )
        # P9R-19 (TOCTOU mitigation): the §28.6 preflight ran milliseconds
        # before urlopen; a concurrent Anthropic call could have crossed
        # 100% in the interval. Re-check AFTER the audit-row write so the
        # newly-recorded usage is included. If the cap is now breached,
        # raise GrokCostCeilingError so the sweep's outer loop logs +
        # surfaces "ceiling crossed mid-sweep" instead of returning
        # silently. The candidates we just spent tokens on are still
        # returned for the audit, but the caller knows the next call
        # must refuse.
        if conn is not None and cost.is_combined_ceiling_breached(conn):
            _log_grok_response(
                conn,
                query=query,
                request_payload={"phase": "postcall_ceiling_recheck"},
                response_status_code=None,
                response_body=None,
                rate_snapshot=None,
                rejection_reason=_REJECTION_COST_CEILING,
                duration_ms=0,
            )
            _LOG.warning(
                "grok_client.search: ceiling crossed mid-call (TOCTOU) — "
                "this call's tokens were spent, future calls refused."
            )
        _LOG.info(
            "grok_client.search query=%r candidates=%d duration_ms=%d",
            query[:80],
            len(candidates),
            int((time.perf_counter() - started_total) * 1000),
        )
        return candidates

    # 5xx retry budget exhausted.
    if last_server_error is not None:
        raise last_server_error
    raise GrokServerError("Grok server error after retry budget exhausted")


# ---------------------------------------------------------------------------
# HTTP plumbing.
# ---------------------------------------------------------------------------
def _http_post_json(
    *,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any] | None, float | None]:
    """POST JSON to ``url`` with Bearer auth. Return (status, body, retry_after).

    Raises ``GrokUnavailable`` only on connection-level failures (DNS,
    refused, timeout, non-JSON body). HTTP-level errors (4xx/5xx) are
    returned with the parsed body so the caller can decide whether to
    retry.

    P9R-56: return type tightened — ``status`` is guaranteed ``int``
    because the only ``None`` path used to be connection-level failure,
    which now raises ``GrokUnavailable`` instead.
    """
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "xgrowth-dashboard/phase9 (grok_client.py)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = resp.status
            raw = resp.read()
            retry_after = _parse_retry_after(resp.headers)
    except urllib.error.HTTPError as http_err:
        # Server reachable; non-2xx response. P9R-24: contextlib.closing
        # so the underlying socket releases promptly, not at GC time.
        status = http_err.code
        retry_after = _parse_retry_after(http_err.headers)
        try:
            with contextlib.closing(http_err):
                raw = http_err.read() or b""
        except Exception:  # noqa: BLE001 — pragma: covers .read() oddities
            raw = b""
    except urllib.error.URLError as url_err:
        raise GrokUnavailable(
            f"xAI Grok call failed at network layer: {url_err.reason!r}"
        ) from url_err
    except TimeoutError as timeout_err:
        raise GrokUnavailable(
            f"xAI Grok call timed out after {timeout_seconds}s"
        ) from timeout_err

    if not raw:
        return (status, None, retry_after)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Body present but unparseable — treat as 5xx for retry semantics.
        _LOG.warning(
            "grok_client: status=%s body non-JSON (%s); first 200 bytes: %r",
            status, exc, raw[:200],
        )
        return (status, {"_raw": raw[:1000].decode("utf-8", errors="replace")},
                retry_after)
    if not isinstance(body, dict):
        return (status, {"_raw_list_or_scalar": body}, retry_after)
    return (status, body, retry_after)


def _parse_retry_after(headers: Any) -> float | None:
    """Read the ``Retry-After`` header in seconds.

    Per RFC 7231, Retry-After can be either a delta-seconds integer or
    an HTTP-date. xAI's docs don't specify which form they use; we
    handle delta-seconds first (the common case) and ignore HTTP-date
    (the caller already has a 60s fallback).
    """
    if headers is None:
        return None
    # P9R-28: dropped the X-RateLimit-Reset fallback. By convention
    # that header carries an absolute epoch timestamp, not delta-
    # seconds; conflating it with Retry-After made a future xAI
    # behavior change produce nonsense "retry_after=1716440000s"
    # errors. xAI doesn't emit that header today anyway. If they
    # ever do, treat the epoch shape explicitly via a separate
    # parser — don't bolt it onto this one.
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Response parsing.
# ---------------------------------------------------------------------------
def _parse_candidates_from_response(
    body: dict[str, Any] | None,
) -> list[GrokCandidate]:
    """Extract X post URLs from the Grok ``citations`` array.

    Per the xAI Live Search response shape, ``body["citations"]`` is a
    list of URLs the model used. We filter to x.com / twitter.com
    status URLs and parse handle + post_id from each.

    The model's ``choices[0].message.content`` is intentionally NOT
    parsed: per §29.12, Daniel's queries surface candidates via Live
    Search citations, not via Grok's prose. The content text would
    drift over time as Grok's chat behavior changes; the citations
    list is the stable contract.
    """
    if not body or not isinstance(body, dict):
        return []
    citations = body.get("citations")
    if not isinstance(citations, list):
        return []

    grok_text_lookup: dict[str, str] = {}
    # If the model decided to include short rationales inline in the
    # content text, we don't depend on them — but we do harvest them
    # opportunistically for ``grok_relevance_rationale`` when present.
    # Future enhancement; today we leave the field None.

    seen: set[str] = set()
    out: list[GrokCandidate] = []
    for entry in citations:
        # Citations are typically strings (URLs), but some xAI
        # implementations wrap them in dicts with metadata; handle both.
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            url = (
                entry.get("url")
                or entry.get("href")
                or entry.get("citation")
                or ""
            )
        else:
            continue
        if not url or not isinstance(url, str):
            continue
        match = _X_URL_RE.match(url.strip())
        if match is None:
            continue
        post_id = match.group("post_id")
        handle = match.group("handle")
        if post_id in seen:
            continue
        seen.add(post_id)
        out.append(
            GrokCandidate(
                target_x_post_id=post_id,
                target_post_url=f"https://x.com/{handle}/status/{post_id}",
                target_author_handle=handle,
                target_text=None,
                observed_metrics={},
                grok_relevance_rationale=grok_text_lookup.get(post_id),
            )
        )
    return out


def _extract_assistant_content(body: dict[str, Any] | None) -> str | None:
    """Return the model's prose from a chat-completions body, if present."""
    if not body or not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def fetch_post_by_url(
    url: str,
    *,
    conn: sqlite3.Connection | None = None,
    expected_post_id: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_GROK_MODEL,
    endpoint: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    """Ask Grok Live Search to cite a specific X status URL.

    Agent Chat comparison path (§28.4) — discovery/text supplement only.
    Engagement metrics remain xurl's job per §29.2.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        raise ValueError("url must be a non-empty string")

    post_id = expected_post_id
    if not post_id:
        match = _X_URL_RE.match(clean_url)
        if match is not None:
            post_id = match.group("post_id")

    audit_query = f"fetch_post_by_url: {clean_url}"

    key = api_key or os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise GrokUnavailable(
            "XAI_API_KEY is not set in .env — see .env.example for the line "
            "and https://console.x.ai/ to mint a key."
        )
    if _is_placeholder_key(key):
        raise GrokUnavailable(
            "XAI_API_KEY looks like the documented placeholder "
            f"({key!r:.40}…). Replace it with a real key in .env."
        )

    if conn is None:
        _LOG.warning(
            "grok_client.fetch_post_by_url called with conn=None — "
            "§28.6 ceiling preflight skipped."
        )
    elif cost.is_combined_ceiling_breached(
        conn, projected_call_cost_usd=GROK_PROJECTED_CALL_COST_GUESS_USD
    ):
        _log_grok_response(
            conn,
            query=audit_query,
            request_payload={"preflight": "cost_ceiling_check", "url": clean_url},
            response_status_code=None,
            response_body=None,
            rate_snapshot=None,
            rejection_reason=_REJECTION_COST_CEILING,
            duration_ms=0,
        )
        raise GrokCostCeilingError(
            "Grok fetch_post_by_url refused — §28.6 combined ceiling reached."
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You locate a specific X post by URL. Return live citations "
                    "for the exact status URL Daniel provided. Quote the post text "
                    "in your reply when Live Search surfaces it."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Find this exact X post and cite its URL: {clean_url}"
                ),
            },
        ],
        "search_parameters": {
            "mode": "on",
            "sources": [{"type": "x"}],
            "max_search_results": 10,
            "return_citations": True,
        },
    }
    post_url = endpoint or GROK_ENDPOINT
    retry_attempts = max(0, retry_attempts)

    last_server_error: GrokError | None = None
    attempt = 0
    started_total = time.perf_counter()
    while attempt <= retry_attempts:
        started = time.perf_counter()
        try:
            response_status, response_body, retry_after = _http_post_json(
                url=post_url,
                payload=payload,
                api_key=key,
                timeout_seconds=timeout_seconds,
            )
        except GrokUnavailable as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _log_grok_response(
                conn,
                query=audit_query,
                request_payload=payload,
                response_status_code=None,
                response_body=None,
                rate_snapshot=None,
                rejection_reason=_REJECTION_OTHER,
                duration_ms=duration_ms,
            )
            raise GrokUnavailable(str(exc)) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)

        if response_status == 429:
            _log_grok_response(
                conn,
                query=audit_query,
                request_payload=payload,
                response_status_code=response_status,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_RATE_LIMIT,
                duration_ms=duration_ms,
            )
            raise GrokRateLimitError(
                "xAI returned 429 for fetch_post_by_url",
                retry_after_seconds=retry_after,
            )

        if response_status is not None and response_status >= 500:
            last_server_error = GrokServerError(
                f"xAI returned {response_status}: "
                f"{_safe_truncate(response_body, 300)}",
                status_code=response_status,
            )
            _log_grok_response(
                conn,
                query=audit_query,
                request_payload=payload,
                response_status_code=response_status,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_5XX,
                duration_ms=duration_ms,
            )
            attempt += 1
            if attempt > retry_attempts:
                break
            if conn is not None and cost.is_combined_ceiling_breached(conn):
                raise GrokCostCeilingError(
                    "5xx retry refused — §28.6 combined ceiling reached."
                )
            backoff = _DEFAULT_RETRY_SLEEP_SECONDS * (2 ** (attempt - 1))
            backoff = min(backoff, 5.0) + random.uniform(0, 0.5)
            time.sleep(backoff)
            continue

        if response_status is None or response_status >= 400:
            _log_grok_response(
                conn,
                query=audit_query,
                request_payload=payload,
                response_status_code=response_status,
                response_body=response_body,
                rate_snapshot=None,
                rejection_reason=_REJECTION_OTHER,
                duration_ms=duration_ms,
            )
            raise GrokError(
                f"xAI returned {response_status}: "
                f"{_safe_truncate(response_body, 300)}",
                status_code=response_status,
            )

        candidates = _parse_candidates_from_response(response_body)
        matched = None
        if post_id:
            matched = next(
                (c for c in candidates if c.target_x_post_id == post_id),
                None,
            )
        if matched is None and candidates:
            matched = candidates[0]

        assistant_text = _extract_assistant_content(response_body)
        rate_snapshot = _build_rate_snapshot(response_body, model=model)
        grok_id = _log_grok_response(
            conn,
            query=audit_query,
            request_payload=payload,
            response_status_code=response_status,
            response_body=response_body,
            rate_snapshot=rate_snapshot,
            rejection_reason=None,
            duration_ms=duration_ms,
        )

        citation_matched = (
            matched is not None
            and post_id is not None
            and matched.target_x_post_id == post_id
        )
        status = "success" if matched is not None or assistant_text else "error"
        result: dict[str, Any] = {
            "status": status,
            "target_post_url": clean_url,
            "target_post_id": post_id,
            "target_post_text": assistant_text,
            "target_author_handle": matched.target_author_handle if matched else None,
            "citation_matched": citation_matched,
            "citations_count": len(candidates),
            "assistant_excerpt": _safe_truncate(assistant_text, 500) if assistant_text else None,
            "grok_api_response_id": grok_id,
            "elapsed_seconds": round(time.perf_counter() - started_total, 3),
        }
        if status == "error":
            result["error"] = (
                "Grok did not cite the requested status URL"
                if not matched
                else "Grok returned no post text"
            )
        _LOG.info(
            "grok_client.fetch_post_by_url url=%r matched=%s duration_ms=%d",
            clean_url[:80],
            citation_matched,
            int((time.perf_counter() - started_total) * 1000),
        )
        return result

    if last_server_error is not None:
        raise last_server_error
    raise GrokServerError("Grok server error after retry budget exhausted")


def _build_rate_snapshot(
    body: dict[str, Any] | None, *, model: str
) -> dict[str, Any]:
    """Construct the ``rate_snapshot_json`` blob recorded with each call.

    Records the model name, token counts (for the §28.6 monthly spend
    reconstruction), and the rate-table snapshot version so a future
    repricing leaves historical cost calcs intact (same versioning
    discipline as ``agent_messages.rate_snapshot_json``).
    """
    usage: dict[str, Any] = {}
    if isinstance(body, dict):
        u = body.get("usage")
        if isinstance(u, dict):
            usage = u
    input_tokens = int(
        usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    )
    output_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    rate_in, rate_out = cost.get_model_rates(model)
    return {
        "provider": "xai",
        "model": model,
        "version": cost.RATE_TABLE_VERSION,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_per_million_usd": rate_in,
        "output_per_million_usd": rate_out,
    }


# ---------------------------------------------------------------------------
# Audit logging — grok_api_responses (migration 021).
# ---------------------------------------------------------------------------
def _log_grok_response(
    conn: sqlite3.Connection | None,
    *,
    query: str,
    request_payload: dict[str, Any] | None,
    response_status_code: int | None,
    response_body: dict[str, Any] | None,
    rate_snapshot: dict[str, Any] | None,
    rejection_reason: str | None,
    duration_ms: int,
    notes: str | None = None,
) -> int | None:
    """Insert one row into ``grok_api_responses``; return the new id.

    Safe to call with ``conn=None`` (returns None) — that path is used
    by unit tests that don't want to set up a DB just to verify the
    HTTP plumbing.

    Logging never raises — a failed audit insert must not also fail
    the upstream API call. Mirrors the best-effort pattern from
    ``app/x_client.py::_log_raw``.
    """
    if conn is None:
        return None

    # Truncate to 64KB for storage; the full text is in the launchd
    # err.log if anyone needs a richer dump.
    body_str: str | None = None
    if response_body is not None:
        try:
            body_str = json.dumps(response_body)[:64_000]
        except (TypeError, ValueError):
            body_str = repr(response_body)[:64_000]

    payload_str: str | None = None
    if request_payload is not None:
        try:
            payload_str = json.dumps(request_payload)[:64_000]
        except (TypeError, ValueError):
            payload_str = repr(request_payload)[:64_000]

    rate_str: str | None = None
    if rate_snapshot is not None:
        try:
            rate_str = json.dumps(rate_snapshot)
        except (TypeError, ValueError):
            rate_str = None

    # If notes need to be carried alongside the body (the JSON-decode
    # warning, network-error reason), pack them into the body_json
    # column under a clearly-marked key. We don't add a separate
    # column to keep the schema minimal.
    if notes is not None and body_str is None:
        body_str = json.dumps({"_notes": notes})

    try:
        cur = conn.execute(
            """
            INSERT INTO grok_api_responses
              (query, request_payload_json, response_status_code,
               response_body_json, rate_snapshot_json,
               rejection_reason, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                query[:1000],
                payload_str,
                response_status_code,
                body_str,
                rate_str,
                rejection_reason,
                duration_ms,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError as exc:
        # P9R-11: catch DatabaseError (parent of OperationalError +
        # IntegrityError + ProgrammingError) so a future CHECK violation
        # on rejection_reason doesn't escape and break the upstream
        # call. Audit-row insert is documented as "logging never raises".
        _LOG.warning("grok_api_responses insert failed (suppressed): %s", exc)
        # P9R-17: JSONL sidecar fallback. When the DB INSERT fails (locked
        # / disk full / migration drift), the §28.6 spend reconstruction
        # silently loses the row's rate_snapshot_json. Persist a JSON line
        # to data/logs/grok_api_responses.lost.jsonl so the spend can be
        # reconciled later via a one-off ingestion script. Best-effort;
        # any filesystem error is also suppressed (we don't make the
        # upstream call fail to record an audit-of-an-audit).
        try:
            _append_lost_audit_row(
                query=query,
                request_payload_json=payload_str,
                response_status_code=response_status_code,
                response_body_json=body_str,
                rate_snapshot_json=rate_str,
                rejection_reason=rejection_reason,
                duration_ms=duration_ms,
                db_error=str(exc),
            )
        except Exception:  # noqa: BLE001 — sidecar is best-effort
            pass
        return None


def _append_lost_audit_row(
    *,
    query: str,
    request_payload_json: str | None,
    response_status_code: int | None,
    response_body_json: str | None,
    rate_snapshot_json: str | None,
    rejection_reason: str | None,
    duration_ms: int,
    db_error: str,
) -> None:
    """P9R-17: append one JSON line to data/logs/grok_api_responses.lost.jsonl.

    Used when the DB INSERT to grok_api_responses fails. The line shape
    mirrors the table columns so a future ingestion script can read
    the JSONL and replay the rows into grok_api_responses.

    Defensive: silently no-ops if the logs directory can't be created
    or written. The caller already logged a WARNING about the original
    DB failure; we don't want to mask the original error with a
    sidecar-write error.
    """
    from pathlib import Path as _Path

    log_dir = _Path("data") / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = log_dir / "grok_api_responses.lost.jsonl"
    record = {
        "query": query[:1000] if query else None,
        "request_payload_json": request_payload_json,
        "response_status_code": response_status_code,
        "response_body_json": response_body_json,
        "rate_snapshot_json": rate_snapshot_json,
        "rejection_reason": rejection_reason,
        "duration_ms": duration_ms,
        "lost_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "db_error": db_error,
    }
    with sidecar_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record) + "\n")


def _safe_truncate(body: dict[str, Any] | None, n: int) -> str:
    if body is None:
        return ""
    try:
        return json.dumps(body)[:n]
    except (TypeError, ValueError):
        return repr(body)[:n]


__all__ = [
    "DEFAULT_GROK_MODEL",
    "DEFAULT_MAX_RESULTS",
    "GROK_ENDPOINT",
    "GrokCandidate",
    "GrokCostCeilingError",
    "GrokError",
    "GrokRateLimitError",
    "GrokServerError",
    "GrokUnavailable",
    "is_configured",
    "search",
]
