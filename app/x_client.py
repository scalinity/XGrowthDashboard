"""X API read client — shells out to ``xurl`` (Phase 7, spec §17 / §18).

Phase 7 ships the read surface only. The xurl binary (installed via
``brew install xurl``; authenticated once via ``xurl auth login``) holds
the OAuth refresh tokens under ``~/.xurl/`` — the dashboard NEVER touches
the raw tokens. We invoke xurl as a subprocess; the binary attaches the
bearer header and returns the X API JSON body on stdout.

Every call is logged to ``raw_api_responses`` so the Settings "Recent X
API failures" panel (§17 Phase 7) can surface non-2xx outcomes and the
audit trail covers what data we pulled from where.

The Phase 8 write surface (``POST /2/tweets`` + cold-reply 403 UX) lives
in this same module but is added in migration 019 / Phase 8 — the
``request`` function below already accepts a ``method`` argument so the
Phase 8 additions are call-site changes rather than a parallel wrapper.

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
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal

_log = logging.getLogger(__name__)

# Subprocess timeout — xurl makes one HTTP request per invocation. X API
# routinely answers in <1s; 30s is a generous ceiling that covers
# transient network blips without hanging the scheduled job.
_DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Default xurl binary path. Tests + scheduled jobs honor the
# ``XURL_BIN`` env var to override (CI fixture mode points it at a fake
# script that emits canned responses).
_DEFAULT_XURL_BIN: str = "xurl"

HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


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
    """Return True if the xurl binary is installed and on PATH.

    Does NOT verify that ``xurl auth login`` has been completed — that
    surfaces as a 401 on the first real call. The Settings → Data sources
    panel surfaces both states explicitly.
    """
    binary = xurl_bin or os.environ.get("XURL_BIN") or _DEFAULT_XURL_BIN
    return shutil.which(binary) is not None


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
    if shutil.which(binary) is None:
        raise XApiUnavailable(
            f"xurl binary {binary!r} not found on PATH; "
            "see docs/X_API_SETUP.md to install + authenticate."
        )

    # Build the subprocess argv. xurl's CLI: `xurl [--method M] [--data D] <endpoint>`.
    argv: list[str] = [binary]
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
            notes=f"subprocess timeout; {log_notes or ''}".strip(),
        )
        raise XApiUnavailable(
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

    raw_id = _log_raw(
        conn,
        source=log_source,
        endpoint=endpoint,
        method=method,
        body_json=body_json,
        response_text=stdout[:65_000],  # bounded so we don't blow up the audit row
        status_code=status_code,
        notes=log_notes,
    )

    if status_code == 401:
        raise XApiUnavailable(
            f"X API returned 401 for {method} {endpoint} — "
            f"xurl auth missing or expired. See docs/X_API_SETUP.md."
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
    if isinstance(body, dict):
        # Top-level "reset" or nested under "errors".
        reset = body.get("reset") if isinstance(body, dict) else None
        if isinstance(reset, (int, float)):
            now = time.time()
            return max(1.0, float(reset) - now)
        errs = body.get("errors") if isinstance(body, dict) else None
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


__all__ = [
    "HttpMethod",
    "XApiError",
    "XApiNotFound",
    "XApiRateLimited",
    "XApiResponse",
    "XApiUnavailable",
    "batch_request",
    "is_available",
    "request",
]
