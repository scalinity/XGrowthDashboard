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
# rounding error.
PROJECTED_CALL_COST_GUESS_USD: float = 0.02

# Status-code based rejection_reason categorization (matches the CHECK
# constraint in migration 021 on grok_api_responses.rejection_reason).
_REJECTION_RATE_LIMIT: str = "rate_limit_429"
_REJECTION_COST_CEILING: str = "cost_ceiling_hit"
_REJECTION_5XX: str = "http_error_5xx"
_REJECTION_OTHER: str = "http_error_other"

# X post URL → (handle, post_id) extraction. Matches both x.com and
# twitter.com hostnames (Grok cites either). Used to parse the
# citations array into typed candidates.
_X_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:x|twitter)\.com/"
    r"(?P<handle>[A-Za-z0-9_]{1,15})/status/(?P<post_id>\d+)"
    r"(?:/|$|\?)"
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


class GrokCostCeilingError(GrokError):
    """§28.6 combined Anthropic + xAI ceiling reached — refuse new call.

    Raised by the preflight check before the HTTP request fires. No
    Grok call is made; the audit row is logged with
    ``rejection_reason='cost_ceiling_hit'`` and zero token usage.
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
def is_configured() -> bool:
    """Return True if ``XAI_API_KEY`` is set in the process environment.

    Used by the Settings UI to render a "configured / not set" status
    indicator WITHOUT exposing the key value (§29.12 + §18 item 19).
    """
    return bool(os.environ.get("XAI_API_KEY", "").strip())


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

    # §28.6 preflight — refuse the call if combined Anthropic + xAI
    # spend would breach the cap. The audit row records the refusal so
    # Settings → Recent Grok failures shows it (Daniel can disambiguate
    # ceiling-hit from rate-limit / 5xx without checking another panel).
    if conn is not None and cost.is_combined_ceiling_breached(
        conn, projected_call_cost_usd=PROJECTED_CALL_COST_GUESS_USD
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
            attempt += 1
            last_server_error = GrokServerError(
                f"xAI returned {response_status}: {_safe_truncate(response_body, 300)}",
                status_code=response_status,
            )
            if attempt > retry_attempts:
                _log_grok_response(
                    conn,
                    query=query,
                    request_payload=payload,
                    response_status_code=response_status,
                    response_body=response_body,
                    rate_snapshot=None,
                    rejection_reason=_REJECTION_5XX,
                    duration_ms=duration_ms,
                )
                break
            time.sleep(_DEFAULT_RETRY_SLEEP_SECONDS)
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
) -> tuple[int | None, dict[str, Any] | None, float | None]:
    """POST JSON to ``url`` with Bearer auth. Return (status, body, retry_after).

    Raises ``GrokUnavailable`` only on connection-level failures (DNS,
    refused, timeout, non-JSON body). HTTP-level errors (4xx/5xx) are
    returned with the parsed body so the caller can decide whether to
    retry.
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
        # Server reachable; non-2xx response.
        status = http_err.code
        try:
            raw = http_err.read() or b""
        except Exception:  # noqa: BLE001 — pragma: covers .read() oddities
            raw = b""
        retry_after = _parse_retry_after(http_err.headers)
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
    raw = (
        headers.get("Retry-After")
        or headers.get("retry-after")
        or headers.get("X-RateLimit-Reset")
    )
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
    except sqlite3.OperationalError as exc:
        _LOG.warning("grok_api_responses insert failed (suppressed): %s", exc)
        return None


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
