"""X API read client — shells out to ``xurl`` (Phase 7, spec §17 / §18).

Phase 7 ships the read surface only. The xurl binary (installed via
``brew install xurl``; authenticated once via ``xurl auth login``) holds
the OAuth refresh tokens under ``~/.xurl/`` — the dashboard NEVER touches
the raw tokens. We invoke xurl as a subprocess; the binary attaches the
bearer header and returns the X API JSON body on stdout.

Every call is logged to ``raw_api_responses`` so the Settings "Recent X
API failures" panel (§17 Phase 7) can surface non-2xx outcomes and the
audit trail covers what data we pulled from where.

Phase 8 adds the write surface (``POST /2/tweets``) in this same module
(§28.10 Phase 5.5 → Phase 8 transition; §25 Phase 8 checklist). The
write path reuses ``request()`` — POST is already a first-class method
argument — and layers ``publish_post_to_x_via_api()`` on top with:

* Bounded retry per ``x_posting_publish_retry_attempts_per_token`` on
  5xx (X-side transient).
* No retry on 429 (rate-limit), 403 (cold-reply), or timeout — those
  map to specific token-consumed outcomes in ``app/agent/publish.py``
  and retrying would either burn the token (429) or risk a duplicate
  post (timeout).
* Typed exception hierarchy (``XApiColdReplyError``,
  ``XApiServerError``, ``XApiTimeoutError``, plus the existing
  ``XApiRateLimited``) so the publish wrapper can take the right
  except branch without re-parsing error messages.

The sliding-window write quota is enforced by
``check_write_rate_capacity()``, called BEFORE the X API call inside
the §28.10 atomic transaction. On capacity exhausted the function
returns ``(False, reason)`` and the publish flow surfaces "rate-limited
until {reset_time}" with the confirmation token UN-consumed.

Manual fallback is sacrosanct (CLAUDE.md scope discipline / §29.1
"Manual workflows remain inviolable"): every consumer of this module
checks ``data_collection_mode == 'api'`` first, and if the setting is
``'manual'`` OR ``xurl`` is unavailable / unauthenticated, the consumer
takes the manual paste path instead. This module raises
``XApiUnavailable`` for the auth/install failure modes so consumers can
distinguish them from transient HTTP errors.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

_log = logging.getLogger(__name__)

# Subprocess timeout — xurl makes one HTTP request per invocation. X API
# routinely answers in <1s; 30s is a generous ceiling that covers
# transient network blips without hanging the scheduled job.
_DEFAULT_TIMEOUT_SECONDS: float = 30.0

# RV2-13: hard cap on the in-job rate-limit retry wait. Launchd plists
# have ExitTimeOut=300s (5 min); if we sleep longer than that the OS
# SIGKILLs the process mid-sleep, possibly between an audit-row INSERT
# and a follow-up UPDATE. Set well below the timeout so the sweep can
# still finish its current batch + write the scheduled_job audit row.
_MAX_RATE_LIMIT_WAIT_SECONDS: float = 90.0

# Default xurl binary path. Tests + scheduled jobs honor the
# ``XURL_BIN`` env var to override (CI fixture mode points it at a fake
# script that emits canned responses).
_DEFAULT_XURL_BIN: str = "xurl"
_STANDARD_XURL_PATHS: tuple[str, ...] = (
    "~/go/bin/xurl",
    "/opt/homebrew/bin/xurl",
    "/usr/local/bin/xurl",
)

HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


# RV2-8: X handles are limited to [A-Za-z0-9_]{1,15}. Validate at the
# tool boundary so an agent-supplied or hallucinated handle (e.g.
# "foo/../tweets?max_results=1000" or "foo?expansions=author_id") can't
# escape the intended endpoint path when interpolated into a xurl URL.
# CWE-20 / CWE-88.
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _read_timeout_setting(conn: sqlite3.Connection) -> float:
    """RV2-30: optional setting override for the subprocess timeout.

    Returns the configured value or the module default. Surfaced in
    Settings → Growth Agent so Daniel can raise the timeout for
    flaky-network scenarios without code changes.
    """
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'x_api_subprocess_timeout_seconds'"
        ).fetchone()
    except sqlite3.OperationalError:
        return _DEFAULT_TIMEOUT_SECONDS
    if row is None or row[0] is None:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        val = float(json.loads(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _DEFAULT_TIMEOUT_SECONDS
    # Sanity: must be a positive number; cap at 300s (launchd ExitTimeOut).
    if val <= 0 or val > 300:
        return _DEFAULT_TIMEOUT_SECONDS
    return val


def validate_x_handle(handle: str) -> str:
    """Return the normalized handle (no @, stripped) or raise ValueError.

    Rules per X's handle spec:
    - 1–15 characters
    - Only A-Z, a-z, 0-9, _
    - Leading '@' is stripped (Daniel often pastes with the @)
    - Whitespace is stripped

    Rejects anything with '/', '?', '&', '..', '%', spaces, or path
    separators so the handle is safe to interpolate into a URL path
    component.
    """
    # Strip whitespace first, then '@', then whitespace again — handles
    # the '  @user_15  ' shape Daniel sometimes pastes from X mobile.
    clean = (handle or "").strip().lstrip("@").strip()
    if not _X_HANDLE_RE.match(clean):
        raise ValueError(
            f"invalid X handle: {handle!r} "
            f"(expected ^[A-Za-z0-9_]{{1,15}}$ after @-stripping)"
        )
    return clean


class XApiError(Exception):
    """Base for X API call failures. Carries an HTTP-ish status code."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class XApiUnavailable(XApiError):
    """xurl is not installed, not authenticated, or returned no parseable
    HTTP status (process-level failure). Consumers fall back to manual."""


class XApiRateLimited(XApiError):
    """X API returned 429 — consumer should respect ``retry_after_seconds``."""

    def __init__(
        self, message: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


class XApiNotFound(XApiError):
    """X API returned 404 — the target post / user no longer exists.

    The reply_target_metrics_refresh job catches this specifically to
    transition ``status='target_deleted'`` per §29.11.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class XApiColdReplyError(XApiError):
    """X API returned 403 on a write — typically a cold reply.

    Per §22 / §29.11: X requires the authenticated user to have engaged
    with a target account before the API will accept a reply to that
    account. The publish wrapper treats this as a "X accepted the
    request and refused it" outcome — the confirmation token is
    CONSUMED (X considers it a real attempt) and no ``posts`` row is
    created. UX surfaces "engage with this author's posts first, or
    use the manual fallback."
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


class XApiServerError(XApiError):
    """X API returned a 5xx after the retry budget was exhausted.

    Per §22: the publish wrapper ROLLBACKs the transaction, sets
    ``publish_last_error``, and consumes the token per rule #10(f). The
    crash-recovery scan reconciles on next app boot via
    ``api_get_recent_tweets()`` matched by text hash.
    """


class XApiTimeoutError(XApiUnavailable):
    """xurl subprocess timed out mid-write.

    Subclasses ``XApiUnavailable`` so existing read-side ``except
    XApiUnavailable`` handlers (which fall back to manual paste) still
    catch it. The publish wrapper has a dedicated ``except
    XApiTimeoutError`` branch that ROLLBACKs the transaction, sets
    ``publish_last_error``, and leaves the orphan for crash-recovery to
    reconcile — we MUST NOT retry on timeout because X may have actually
    processed the request and a retry would double-post.
    """


@dataclass(frozen=True, slots=True)
class XApiResponse:
    """One xurl invocation's outcome. ``raw_response_id`` is the audit
    backref into ``raw_api_responses``."""

    status_code: int
    body: dict[str, Any] | list[Any]
    raw_response_id: int | None
    endpoint: str
    method: HttpMethod
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def is_available(xurl_bin: str | None = None) -> bool:
    """Return True if the xurl binary is installed and resolvable.

    Does NOT verify that ``xurl auth login`` has been completed — that
    surfaces as a 401 on the first real call. The Settings → Data sources
    panel surfaces both states explicitly.
    """
    return _resolve_xurl_binary(xurl_bin) is not None


def request(
    endpoint: str,
    *,
    method: HttpMethod = "GET",
    conn: sqlite3.Connection | None = None,
    body_json: dict[str, Any] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    xurl_bin: str | None = None,
    log_source: str = "xurl",
    log_notes: str | None = None,
) -> XApiResponse:
    """Invoke xurl against ``endpoint`` and log the call to raw_api_responses.

    ``endpoint`` is the X API path — ``/2/users/me``, ``/2/tweets?ids=…``,
    etc. xurl prepends the host and attaches the bearer token. Query
    parameters are part of ``endpoint`` (we don't accept a separate
    params dict — xurl itself doesn't, and keeping the call shape 1:1
    with the URL is more debuggable).

    Returns an ``XApiResponse`` on 2xx. Raises:

    * ``XApiUnavailable`` if xurl is missing, hits a process-level error,
      or returns 401 (not authenticated).
    * ``XApiRateLimited`` on 429.
    * ``XApiNotFound`` on 404.
    * ``XApiError`` for any other non-2xx response.

    Every invocation — successful OR failed — is logged to
    ``raw_api_responses`` when ``conn`` is provided. The audit row's
    ``id`` is on ``XApiResponse.raw_response_id`` so callers can stitch
    snapshots back to the exact API call that produced them.
    """
    binary = xurl_bin or os.environ.get("XURL_BIN") or _DEFAULT_XURL_BIN
    resolved_binary = _resolve_xurl_binary(xurl_bin)
    if resolved_binary is None:
        raise XApiUnavailable(
            f"xurl binary {binary!r} not found on PATH or standard macOS user locations; "
            "see docs/X_API_SETUP.md to install + authenticate."
        )

    # RV2-30: honor the optional `x_api_subprocess_timeout_seconds` setting
    # when the caller didn't pass an explicit override. Daniel can tune
    # this from Settings for flaky-network scenarios without redeploying.
    if timeout_seconds == _DEFAULT_TIMEOUT_SECONDS and conn is not None:
        timeout_seconds = _read_timeout_setting(conn)

    # Build the subprocess argv. xurl's CLI: `xurl [--method M] [--data D] <endpoint>`.
    argv: list[str] = [resolved_binary]
    if method != "GET":
        argv += ["--request", method]
    if body_json is not None:
        argv += ["--data", json.dumps(body_json)]
    argv.append(endpoint)

    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 — argv is a list, not a shell string
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        raw_id = _log_raw(
            conn,
            source=log_source,
            endpoint=endpoint,
            method=method,
            body_json=body_json,
            response_text=f"<timeout after {timeout_seconds}s>",
            status_code=None,
            # RV2-19: drop the trailing semicolon when log_notes is empty.
            notes=(
                f"subprocess timeout; {log_notes}"
                if log_notes
                else "subprocess timeout"
            ),
        )
        # Distinct typed exception for Phase 8 write timeouts — the
        # publish wrapper has a dedicated branch (ROLLBACK + crash-
        # recovery handoff) that read-side callers don't need. Subclass
        # of XApiUnavailable so existing read-side handlers still match.
        raise XApiTimeoutError(
            f"xurl call timed out after {timeout_seconds}s for {method} {endpoint}"
        ) from exc

    elapsed = time.perf_counter() - started
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # xurl writes JSON body to stdout on success. On HTTP error responses,
    # xurl still writes the response body to stdout (it surfaces the X
    # API's JSON error envelope). On NON-HTTP failures (network down,
    # auth missing) it writes a human-readable message to stderr and
    # exits non-zero with no parseable JSON.
    parsed_body: dict[str, Any] | list[Any]
    try:
        parsed_body = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        # stdout wasn't JSON — could be xurl printing a status line, or
        # an unparseable error. Treat as unavailable; the raw text is on
        # the audit row.
        raw_id = _log_raw(
            conn,
            source=log_source,
            endpoint=endpoint,
            method=method,
            body_json=body_json,
            response_text=(stdout + "\n" + stderr).strip()[:8000],
            status_code=None,
            notes=f"xurl returned non-JSON; exit={proc.returncode}",
        )
        raise XApiUnavailable(
            f"xurl returned non-JSON output for {method} {endpoint}; "
            f"exit code {proc.returncode}. stderr (truncated): "
            f"{stderr.strip()[:200]!r}"
        )

    # Infer the HTTP status code. xurl's exit code is 0 for any
    # successful HTTP transaction (even 4xx/5xx); the status code itself
    # is implicit in the body shape. X API error envelopes look like:
    #   {"errors": [{"status": 404, "title": "...", ...}], "title": "..."}
    # Successful 2xx responses look like {"data": [...], "meta": {...}}.
    status_code = _infer_status_code(parsed_body, proc.returncode, stderr)

    # RV2-21: cap raised to 128KB to fit the typical
    # /2/tweets/search/recent + expansions response shape (50-100KB).
    # When truncation kicks in we annotate the audit row's notes so the
    # operator knows the response_json may be mid-string-truncated and
    # therefore not necessarily parseable as JSON.
    _stdout_len = len(stdout)
    _truncated_to = 128_000
    if _stdout_len > _truncated_to:
        _truncation_note = (
            f"[response_text truncated_to={_truncated_to} "
            f"original_length={_stdout_len}]"
        )
        notes_with_truncation = (
            f"{log_notes}; {_truncation_note}" if log_notes else _truncation_note
        )
    else:
        notes_with_truncation = log_notes
    raw_id = _log_raw(
        conn,
        source=log_source,
        endpoint=endpoint,
        method=method,
        body_json=body_json,
        response_text=stdout[:_truncated_to],
        status_code=status_code,
        notes=notes_with_truncation,
    )

    if status_code == 401:
        raise XApiUnavailable(
            f"X API returned 401 for {method} {endpoint} — "
            f"xurl auth missing or expired. See docs/X_API_SETUP.md."
        )
    if status_code == 403:
        # Phase 8 write surface: 403 on POST /2/tweets is X's "cold
        # reply" refusal. Token is consumed (X accepted the request,
        # then refused it). Read-side callers don't currently hit 403,
        # so raising the typed exception here is forward-compatible.
        raise XApiColdReplyError(
            f"X API returned 403 for {method} {endpoint}: "
            f"{stdout.strip()[:300]!r}"
        )
    if status_code == 404:
        raise XApiNotFound(
            f"X API returned 404 for {method} {endpoint} — target not found "
            f"(post / user deleted, or never existed)."
        )
    if status_code == 429:
        retry_after = _parse_retry_after(parsed_body, stderr)
        raise XApiRateLimited(
            f"X API rate-limited on {method} {endpoint}; "
            f"retry after {retry_after}s.",
            retry_after_seconds=retry_after,
        )
    if status_code is None or status_code >= 400:
        raise XApiError(
            f"X API error {status_code} on {method} {endpoint}: "
            f"{stdout.strip()[:300]!r}",
            status_code=status_code,
        )

    return XApiResponse(
        status_code=status_code,
        body=parsed_body,
        raw_response_id=raw_id,
        endpoint=endpoint,
        method=method,
        elapsed_seconds=elapsed,
    )


def batch_request(
    endpoint_template: str,
    ids: Iterable[str],
    *,
    conn: sqlite3.Connection | None = None,
    batch_size: int = 100,
    extra_query: str = "",
    xurl_bin: str | None = None,
    log_source: str = "xurl",
) -> list[XApiResponse]:
    """Call ``endpoint_template`` in batches of up to ``batch_size`` ids.

    ``endpoint_template`` must contain literal ``{ids}`` where the
    comma-joined batch is interpolated. Example:

        batch_request("/2/tweets?ids={ids}&tweet.fields=public_metrics", post_ids)

    Returns one XApiResponse per non-empty batch. The job-layer caller
    composes the responses (X API guarantees batch order matches
    request order for the ``/2/tweets`` endpoint).

    On 429: the function pauses for the Retry-After hint and retries
    once; if the second attempt is also rate-limited the exception
    propagates and the caller schedules the next run.
    """
    if "{ids}" not in endpoint_template:
        raise ValueError(
            "batch_request requires '{ids}' placeholder in endpoint_template"
        )
    id_list = [str(i) for i in ids if i is not None and str(i).strip()]
    if not id_list:
        return []

    responses: list[XApiResponse] = []
    for offset in range(0, len(id_list), batch_size):
        batch = id_list[offset : offset + batch_size]
        endpoint = endpoint_template.format(ids=",".join(batch)) + extra_query
        try:
            resp = request(
                endpoint,
                method="GET",
                conn=conn,
                xurl_bin=xurl_bin,
                log_source=log_source,
                log_notes=f"batch {offset}–{offset + len(batch) - 1}",
            )
        except XApiRateLimited as rate:
            wait = max(1.0, float(rate.retry_after_seconds or 60.0))
            # RV2-13: cap the in-job sleep so the launchd ExitTimeOut
            # (300s default per docs/SCHEDULED_JOBS.md) doesn't SIGKILL
            # the process mid-sleep — possibly between an audit-row
            # INSERT and a subsequent UPDATE on the same logical work-
            # unit. X API's documented Retry-After can be up to 15 min;
            # if the wait exceeds the cap, abort the sweep cleanly and
            # let the next scheduled run retry. The audit row already
            # logged the 429 + retry_after via _log_raw.
            if wait > _MAX_RATE_LIMIT_WAIT_SECONDS:
                _log.warning(
                    "batch_request retry_after=%.0fs exceeds in-job cap "
                    "(%ss) at offset %d; aborting sweep — next run will retry",
                    wait, _MAX_RATE_LIMIT_WAIT_SECONDS, offset,
                )
                raise XApiRateLimited(
                    f"rate-limited; retry_after={wait}s exceeds in-job cap "
                    f"({_MAX_RATE_LIMIT_WAIT_SECONDS}s)",
                    retry_after_seconds=wait,
                ) from rate
            _log.warning(
                "batch_request rate-limited at offset %d; sleeping %.0fs before retry",
                offset,
                wait,
            )
            time.sleep(wait)
            resp = request(
                endpoint,
                method="GET",
                conn=conn,
                xurl_bin=xurl_bin,
                log_source=log_source,
                log_notes=f"batch {offset}–{offset + len(batch) - 1} (retry after 429)",
            )
        responses.append(resp)
    return responses


# ---------------------------------------------------------------------------
# Audit logging — raw_api_responses (§10.2, §17 Phase 7).
# ---------------------------------------------------------------------------
def _log_raw(
    conn: sqlite3.Connection | None,
    *,
    source: str,
    endpoint: str,
    method: HttpMethod,
    body_json: dict[str, Any] | None,
    response_text: str,
    status_code: int | None,
    notes: str | None,
) -> int | None:
    """Insert one row into ``raw_api_responses`` and return the new id.

    Safe to call with ``conn=None`` (returns None) — that path is used
    by tests that don't want to set up a DB just to verify the
    subprocess plumbing.

    RV2-4 rollback-survival promise (§17 Phase 7 / §28.30 "every xurl
    call is logged"): the audit row survives caller-transaction
    rollback BY ARCHITECTURE — the publish flow (publish_post_atomic
    in app/agent/publish.py) runs the X API call OUTSIDE any open
    transaction in its split-txn design (step 2). When the X API call
    fires, ``conn`` is in autocommit mode, so this INSERT commits
    immediately. A subsequent ROLLBACK in publish_post_atomic step 3
    cannot affect the already-committed audit row.

    Side-channel auto-commit writes were considered but rejected: SQLite
    permits only one writer at a time, so a second connection trying to
    INSERT into the same DB while the caller holds BEGIN IMMEDIATE would
    deadlock on the writer lock. The architectural invariant
    (X-API-outside-transaction) is the load-bearing guarantee; the
    invariant is pinned by ``tests/test_x_api_reads.py``
    ``test_log_raw_survives_publish_flow_outside_transaction``.

    Logging never raises — a failed audit insert must not also fail the
    upstream API call. The OperationalError catch mirrors the
    best-effort pattern from ``app/backup.py``.
    """
    if conn is None:
        return None
    try:
        req_params: dict[str, Any] = {"method": method}
        if body_json is not None:
            req_params["body"] = body_json
        cur = conn.execute(
            """
            INSERT INTO raw_api_responses
              (source, endpoint_or_command, request_params_json,
               response_json, status_code, collected_at_utc, notes)
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
            RETURNING id
            """,
            (
                source,
                endpoint,
                json.dumps(req_params),
                response_text,
                status_code,
                notes,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except sqlite3.OperationalError as exc:
        _log.warning("raw_api_responses insert failed (suppressed): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _resolve_xurl_binary(xurl_bin: str | None = None) -> str | None:
    """Resolve xurl for shell-launched jobs and Finder-launched app sidecars.

    macOS GUI apps do not inherit the user's interactive shell PATH, so a
    valid install under ~/go/bin or Homebrew can be invisible to the packaged
    sidecar unless we check the common absolute locations ourselves.
    """
    binary = xurl_bin or os.environ.get("XURL_BIN") or _DEFAULT_XURL_BIN
    found = shutil.which(binary)
    if found:
        return found

    expanded = os.path.expanduser(binary)
    if os.path.sep in binary and os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        return expanded

    if binary != _DEFAULT_XURL_BIN:
        return None

    for candidate in _STANDARD_XURL_PATHS:
        expanded_candidate = os.path.expanduser(candidate)
        if os.path.isfile(expanded_candidate) and os.access(expanded_candidate, os.X_OK):
            return expanded_candidate
    return None


def _infer_status_code(
    body: dict[str, Any] | list[Any],
    exit_code: int,
    stderr: str,
) -> int | None:
    """Best-effort HTTP status inference from xurl's stdout body.

    xurl normalizes the X API response shape — the response body is
    available as-is on stdout but the HTTP headers are NOT exposed by
    default. We infer from the X API error envelope shape:

        {"errors": [{"status": <int>, "title": "...", ...}], "title": "..."}

    A successful response is ``{"data": ...}`` (no "errors" key). We
    return 200 in that case. Process-level failures (xurl exit non-zero
    with auth/network errors) infer 401 if the stderr mentions auth,
    else None (caller treats as unavailable).
    """
    if isinstance(body, dict):
        if "errors" in body and isinstance(body["errors"], list) and body["errors"]:
            first_error = body["errors"][0]
            if isinstance(first_error, dict):
                status = first_error.get("status")
                if isinstance(status, int):
                    return status
                # Some X API errors omit numeric "status" but include "title";
                # title='Not Found' is the canonical 404 marker.
                title = (first_error.get("title") or "").lower()
                if "not found" in title:
                    return 404
                if "too many" in title or "rate" in title:
                    return 429
                # Phase 8: 403 cold-reply error envelope is keyed
                # "Forbidden" — match before the broader "auth" check
                # below so we don't misclassify as 401.
                # P8R-11: title check is exact-match ("forbidden") not
                # substring (avoids matching "scold" etc.); also
                # honor the stable X API `type` URI for cold-reply
                # which is the canonical machine-readable marker.
                error_type = str(first_error.get("type") or "").lower()
                if title == "forbidden" or "cold-reply" in error_type:
                    return 403
                if "unauthorized" in title or "auth" in title:
                    return 401
        if "data" in body or "meta" in body:
            return 200
        if "client_id" in body or "id" in body:
            # Single-resource endpoints like /2/users/me return the raw
            # resource without a wrapping "data" key when xurl is
            # configured with --raw — we treat that as success too.
            return 200
    if isinstance(body, list):
        # Bare list response — uncommon but treat as 200.
        return 200
    if exit_code != 0:
        if "auth" in stderr.lower() or "401" in stderr:
            return 401
        return None
    return 200


def _parse_retry_after(
    body: dict[str, Any] | list[Any],
    stderr: str,
) -> float | None:
    """Parse Retry-After from X API's rate-limit response.

    X API includes ``x-rate-limit-reset`` as an HTTP header (epoch
    seconds) which xurl prints to stderr in verbose mode. The body
    payload sometimes includes a numeric ``reset`` field. Best-effort
    only — caller has a sane fallback (60s).
    """
    # RV2-22: outer isinstance(body, dict) already gates everything below,
    # so the inner `if isinstance(body, dict) else None` ternaries were
    # redundant.
    if isinstance(body, dict):
        # Top-level "reset" or nested under "errors".
        reset = body.get("reset")
        if isinstance(reset, (int, float)):
            now = time.time()
            return max(1.0, float(reset) - now)
        errs = body.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            err_reset = errs[0].get("reset")
            if isinstance(err_reset, (int, float)):
                return max(1.0, float(err_reset) - time.time())
    # stderr fallback — "x-rate-limit-reset: 1700000000" if xurl printed headers.
    if "x-rate-limit-reset" in stderr.lower():
        for line in stderr.splitlines():
            if "rate-limit-reset" in line.lower():
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        return max(1.0, float(parts[-1].strip()) - time.time())
                    except ValueError:
                        continue
    return None


# ---------------------------------------------------------------------------
# Phase 8 — write surface (§28.10 Phase 5.5 → Phase 8 transition).
# ---------------------------------------------------------------------------
# Default bounded retry on 5xx — overridable per call. The
# `x_posting_publish_retry_attempts_per_token` settings row holds the
# operational value; this constant is the conservative fallback when the
# settings table isn't reachable (which never happens in production but
# keeps the function usable in narrow unit tests).
_DEFAULT_WRITE_RETRY_ATTEMPTS: int = 2
_DEFAULT_WRITE_RETRY_SLEEP_SECONDS: float = 0.5


def publish_post_to_x_via_api(
    text: str,
    *,
    in_reply_to_x_post_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    retry_attempts: int | None = None,
    retry_sleep_seconds: float | None = None,
    xurl_bin: str | None = None,
) -> dict[str, Any]:
    """POST ``/2/tweets`` via xurl. Return the parsed ``data`` body on success.

    Used by the §28.10 Phase 8 branch of ``publish_post_atomic`` when
    ``publish_via_api_enabled = TRUE``. The publish wrapper holds the
    six-check + atomic-transaction logic; this function is the X API
    call itself.

    Bounded retry on 5xx via ``retry_attempts`` (default 2, sourced from
    ``x_posting_publish_retry_attempts_per_token`` when available).
    NEVER retries on:

    * 429 — would burn the token without recovery; re-raised
      immediately so the publish wrapper can leave the token UN-consumed
      and surface "rate-limited until …".
    * 403 — X has refused this specific request (typically a cold
      reply). Re-raised as ``XApiColdReplyError``; publish wrapper
      consumes the token and skips the posts row insert.
    * Timeout — X may have already processed the request and a retry
      would double-post. Re-raised as ``XApiTimeoutError``; publish
      wrapper ROLLBACKs and hands off to crash-recovery.

    Returns the X API response's ``data`` dict — shaped::

        {"id": "1234...", "edit_history_tweet_ids": ["1234..."], "text": "..."}

    Raises ``XApiServerError`` after the retry budget is exhausted on 5xx.
    """
    if retry_attempts is None:
        retry_attempts = _read_write_retry_attempts(conn)

    body_payload: dict[str, Any] = {"text": text}
    if in_reply_to_x_post_id:
        # X API v2 reply shape — see https://docs.x.com/x-api/posts/creation-of-a-post
        body_payload["reply"] = {"in_reply_to_tweet_id": str(in_reply_to_x_post_id)}

    attempt = 0
    last_server_error: XApiError | None = None
    while attempt <= retry_attempts:
        try:
            response = request(
                "/2/tweets",
                method="POST",
                conn=conn,
                body_json=body_payload,
                xurl_bin=xurl_bin,
                log_source="xurl",
                log_notes=(
                    f"publish_post_to_x_via_api attempt {attempt + 1}/{retry_attempts + 1}"
                ),
            )
        except XApiRateLimited:
            # Re-raise unchanged. The publish wrapper leaves the token
            # UN-consumed (no X-side state change on a 429).
            raise
        except XApiColdReplyError:
            # 403 — X refused this request. Token will be consumed by
            # the publish wrapper; no retry.
            raise
        except XApiTimeoutError:
            # Timeout mid-call. X may have processed the request. Do NOT
            # retry — risk of duplicate post. Crash-recovery picks up.
            raise
        except XApiUnavailable:
            # xurl missing / auth / non-JSON output — this is an env
            # failure not an X API failure. Re-raise unchanged; publish
            # wrapper takes the same crash-recovery handoff path.
            raise
        except XApiError as exc:
            if exc.status_code is not None and exc.status_code >= 500:
                last_server_error = exc
                attempt += 1
                if attempt > retry_attempts:
                    break
                # P8R-13: sleep duration is injectable via kwarg so
                # tests don't have to patch module state. Default keeps
                # production behavior unchanged.
                _sleep_seconds = (
                    retry_sleep_seconds
                    if retry_sleep_seconds is not None
                    else _DEFAULT_WRITE_RETRY_SLEEP_SECONDS
                )
                time.sleep(_sleep_seconds)
                continue
            # Other 4xx (excluding 401 / 403 / 404 / 429 already handled
            # inside `request()`) — surface unchanged.
            raise
        else:
            data = response.body.get("data") if isinstance(response.body, dict) else None
            if not isinstance(data, dict) or "id" not in data:
                raise XApiServerError(
                    "X API POST /2/tweets returned 200 without a valid "
                    f"data.id field. body={response.body!r}",
                    status_code=response.status_code,
                )
            return data

    # Retry budget exhausted on 5xx.
    raise XApiServerError(
        "X API POST /2/tweets failed after "
        f"{retry_attempts + 1} attempts: {last_server_error}",
        status_code=last_server_error.status_code if last_server_error else None,
    ) from last_server_error


@dataclass(frozen=True, slots=True)
class WriteRateCapacity:
    """Outcome of ``check_write_rate_capacity``.

    ``ok=True`` → caller may proceed with the publish.
    ``ok=False`` → caller surfaces ``reason`` to the user and leaves the
    confirmation token UN-consumed. ``reset_at_utc`` is the earliest
    moment the window is expected to roll over (best-effort — wallclock
    based, not X-API-reported).
    """

    ok: bool
    reason: str | None
    count_15min: int
    count_24h: int
    limit_15min: int
    limit_24h: int
    reset_at_utc: datetime | None


def check_write_rate_capacity(conn: sqlite3.Connection) -> WriteRateCapacity:
    """Sliding-window read of recent successful publishes.

    Honors ``x_write_rate_limit_per_15min`` and
    ``x_write_rate_limit_per_24h``. Counts rows from ``posts`` where
    ``published_to_x_at IS NOT NULL`` and the timestamp falls inside
    each window. Manual-clipboard publishes count too — Daniel is rate-
    limited globally on his X account, not per branch.
    """
    limit_15min = _read_int_setting(conn, "x_write_rate_limit_per_15min", default=50)
    limit_24h = _read_int_setting(conn, "x_write_rate_limit_per_24h", default=1000)

    now = datetime.now(timezone.utc)
    window_15min_start = now - timedelta(minutes=15)
    window_24h_start = now - timedelta(hours=24)

    # P8R-17: single CTE returns (count, oldest) per window in one
    # round-trip instead of two separate scans. 4 queries → 2.
    count_15min, oldest_15min = _count_and_oldest_publish_in_window(
        conn, since=window_15min_start
    )
    count_24h, oldest_24h = _count_and_oldest_publish_in_window(
        conn, since=window_24h_start
    )

    if count_15min >= limit_15min:
        reset_at = (
            (oldest_15min + timedelta(minutes=15)) if oldest_15min else None
        )
        reason = (
            f"rate-limited until {reset_at.isoformat() if reset_at else 'window rolls over'} "
            f"({count_15min} of {limit_15min} per-15min publishes used)"
        )
        return WriteRateCapacity(
            ok=False,
            reason=reason,
            count_15min=count_15min,
            count_24h=count_24h,
            limit_15min=limit_15min,
            limit_24h=limit_24h,
            reset_at_utc=reset_at,
        )

    if count_24h >= limit_24h:
        reset_at = (
            (oldest_24h + timedelta(hours=24)) if oldest_24h else None
        )
        reason = (
            f"rate-limited until {reset_at.isoformat() if reset_at else 'window rolls over'} "
            f"({count_24h} of {limit_24h} per-24h publishes used)"
        )
        return WriteRateCapacity(
            ok=False,
            reason=reason,
            count_15min=count_15min,
            count_24h=count_24h,
            limit_15min=limit_15min,
            limit_24h=limit_24h,
            reset_at_utc=reset_at,
        )

    return WriteRateCapacity(
        ok=True,
        reason=None,
        count_15min=count_15min,
        count_24h=count_24h,
        limit_15min=limit_15min,
        limit_24h=limit_24h,
        reset_at_utc=None,
    )


def api_get_recent_tweets(
    *,
    since_id: str | None = None,
    max_results: int = 25,
    conn: sqlite3.Connection | None = None,
    xurl_bin: str | None = None,
) -> list[dict[str, Any]]:
    """Pull recent tweets from the authenticated user's timeline.

    Used by the §28.10 step 8 crash-recovery scan: when a publish
    transaction ROLLBACKs after the X API call may have succeeded, this
    function lets ``app/agent/recovery.py`` query recent tweets and
    match by text hash against ``draft_text_hash_at_issue``.

    Returns an empty list (instead of raising) on ``XApiUnavailable`` so
    the recovery scan degrades gracefully to the existing manual-
    reconcile UI when xurl isn't installed.
    """
    # P8R-10: url-encode via urllib.parse.urlencode instead of f-string
    # concat. Today's only caller passes a sanitized snowflake (from
    # _highest_committed_x_post_id, which is INT-cast in SQL), but the
    # helper is in __all__ and may grow callers — defensive against a
    # future caller passing a value containing '&' or '=' (e.g. from a
    # corrupted row or a hand-edited test fixture).
    from urllib.parse import urlencode

    query_params: dict[str, str] = {
        "max_results": str(max(5, min(max_results, 100))),
    }
    if since_id:
        query_params["since_id"] = str(since_id)
    endpoint = "/2/users/me/tweets?" + urlencode(query_params)

    try:
        response = request(
            endpoint,
            method="GET",
            conn=conn,
            xurl_bin=xurl_bin,
            log_source="xurl",
            log_notes="api_get_recent_tweets — crash-recovery scan",
        )
    except XApiUnavailable:
        return []
    except XApiError:
        # 4xx / 5xx — degrade to manual-reconcile UI.
        return []

    body = response.body
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# Settings-table helpers (Phase 8).
# ---------------------------------------------------------------------------
def _read_int_setting(
    conn: sqlite3.Connection, key: str, *, default: int
) -> int:
    """Read an integer settings value or fall back to ``default``."""
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return default
    if row is None or row["value_json"] is None:
        return default
    try:
        parsed = json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        return default
    if isinstance(parsed, bool):
        # bool is a subclass of int in Python; guard so a stray TRUE
        # doesn't become 1 here.
        return default
    if isinstance(parsed, int):
        return parsed
    return default


def _read_write_retry_attempts(conn: sqlite3.Connection | None) -> int:
    """Resolve the per-token retry budget from settings."""
    if conn is None:
        return _DEFAULT_WRITE_RETRY_ATTEMPTS
    return _read_int_setting(
        conn,
        "x_posting_publish_retry_attempts_per_token",
        default=_DEFAULT_WRITE_RETRY_ATTEMPTS,
    )


def _count_and_oldest_publish_in_window(
    conn: sqlite3.Connection, *, since: datetime
) -> tuple[int, datetime | None]:
    """Return (count, oldest_published_to_x_at) for the window since ``since``.

    P8R-17: replaces the two-helper, two-scan pattern in
    ``check_write_rate_capacity`` with a single SELECT that does both
    aggregates. Same x_post_id IS NOT NULL filter as RV2-6 and same
    epoch-cast compare as P8R-6 so semantics are identical — just
    fewer round-trips. The legacy ``_count_recent_publishes`` and
    ``_oldest_publish_since`` helpers are kept for test-suite
    backward compat; they each delegate to this combined helper.
    """
    since_iso = since.strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(published_to_x_at) AS oldest
          FROM posts
         WHERE published_to_x_at IS NOT NULL
           AND x_post_id IS NOT NULL
           AND CAST(strftime('%s', published_to_x_at) AS INTEGER)
               >= CAST(strftime('%s', ?) AS INTEGER)
        """,
        (since_iso,),
    ).fetchone()
    if row is None:
        return (0, None)
    n = int(row["n"]) if row["n"] is not None else 0
    oldest_raw = row["oldest"]
    if oldest_raw is None:
        return (n, None)
    try:
        oldest_dt = datetime.strptime(oldest_raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        oldest_dt = None
    return (n, oldest_dt)


def _count_recent_publishes(
    conn: sqlite3.Connection, *, since: datetime
) -> int:
    """Count ``posts`` rows with ``published_to_x_at >= since``.

    RV2-6 (corroborated by both reviewers): filters by ``x_post_id IS NOT
    NULL`` so the rate-limit counter only counts publishes that actually
    landed on X. Two pre-fix overcounts:

    * ``XApiTimeoutError`` sets ``published_to_x_at`` defensively (X may
      have processed) but no ``x_post_id`` lands → phantom rate-limit slot.
    * Manual-clipboard flow sets ``published_to_x_at`` at click-time
      BEFORE Daniel pastes the URL; abandoned clicks held rate-limit slots.

    The combined filter (NOT NULL on both columns) covers both paths.
    """
    # P8R-6: compare via strftime('%s', ...) cast on both sides instead
    # of a lexicographic string compare. The legacy compare worked only
    # because every writer used the same fixed-width "%Y-%m-%d %H:%M:%S"
    # format — but confirmation._parse_db_timestamp tolerates ISO-T on
    # the READ side, so one stray .isoformat() write would silently
    # under-count publishes in the rate-limit window (Daniel's quota
    # would silently inflate). SQLite's strftime('%s', ...) parses both
    # forms identically and the comparison is numeric, not lexicographic.
    since_iso = since.strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM posts
        WHERE published_to_x_at IS NOT NULL
          AND x_post_id IS NOT NULL
          AND CAST(strftime('%s', published_to_x_at) AS INTEGER)
              >= CAST(strftime('%s', ?) AS INTEGER)
        """,
        (since_iso,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _oldest_publish_since(
    conn: sqlite3.Connection, *, since: datetime
) -> datetime | None:
    """Return the oldest ``published_to_x_at`` inside the window, as a UTC datetime.

    RV2-6: same ``x_post_id IS NOT NULL`` filter as ``_count_recent_publishes``
    so the reset-time hint Daniel sees in the publish modal reflects only
    publishes that actually landed on X.
    """
    # P8R-6: epoch-cast compare; matches _count_recent_publishes.
    since_iso = since.strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """
        SELECT MIN(published_to_x_at) AS oldest FROM posts
        WHERE published_to_x_at IS NOT NULL
          AND x_post_id IS NOT NULL
          AND CAST(strftime('%s', published_to_x_at) AS INTEGER)
              >= CAST(strftime('%s', ?) AS INTEGER)
        """,
        (since_iso,),
    ).fetchone()
    if row is None or row["oldest"] is None:
        return None
    try:
        return datetime.strptime(row["oldest"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def api_get_user_metrics() -> dict[str, int | str | None]:
    """Fetch the authenticated user's public metrics from X API v2.

    Hits ``/2/users/me?user.fields=public_metrics`` and returns a dict
    with ``followers_count``, ``following_count``, ``post_count``,
    ``listed_count``, and ``username``. Raises ``XApiError`` on failure.
    """
    resp = request("/2/users/me?user.fields=public_metrics")
    if resp.status_code != 200:
        raise XApiError(
            f"Failed to fetch user metrics (HTTP {resp.status_code}): "
            f"{resp.body}"
        )
    data = resp.body.get("data", {})
    pm = data.get("public_metrics", {})
    return {
        "username": data.get("username"),
        "followers_count": pm.get("followers_count"),
        "following_count": pm.get("following_count"),
        "post_count": pm.get("tweet_count"),  # X API calls it tweet_count
        "listed_count": pm.get("listed_count"),
    }


__all__ = [
    "HttpMethod",
    "WriteRateCapacity",
    "XApiColdReplyError",
    "XApiError",
    "XApiNotFound",
    "XApiRateLimited",
    "XApiResponse",
    "XApiServerError",
    "XApiTimeoutError",
    "XApiUnavailable",
    "api_get_recent_tweets",
    "batch_request",
    "api_get_user_metrics",
    "check_write_rate_capacity",
    "is_available",
    "publish_post_to_x_via_api",
    "request",
]
