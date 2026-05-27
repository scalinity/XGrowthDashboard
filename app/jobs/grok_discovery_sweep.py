"""Phase 9 / §17 Phase 9 / §29.12 — Grok discovery sweep.

Runs every ``grok_discovery_sweep_interval_minutes`` (default 120) via
launchd (``launchd/com.scalinity.xgrowth.grok-sweep.plist``, NOT
auto-loaded — Daniel runs ``launchctl load`` per §17 user-consent
discipline).

Workflow per sweep:

  1. Read ``grok_api_enabled``; if FALSE, abort with audit log row.
  2. Read ``grok_query_list_json``; if [], abort (no queries to run).
  3. For each query:
     a. Call ``grok_client.search(query)`` — returns candidates.
     b. For each candidate:
        i.   §29.2 verification via
             ``reply_targets.verify_grok_candidate_against_x_api``
             (Phase 7's xurl wrapper).
        ii.  On X API 200: continue with X API metrics as source of
             truth (NOT Grok's observed_metrics).
        iii. On X API 404: reject; log to ``grok_api_responses`` with
             ``rejection_reason='verification_404'``; skip to next.
        iv.  §29.3 scoring with X API metrics (saturation +
             engagement_surface from §29.4 thresholds; relevance and
             reply_opportunity stay NULL — they're Daniel-judged).
        v.   INSERT INTO reply_targets with
             ``discovered_via='grok_semantic'`` and
             ``created_via_agent_message_id=NULL``.
             On ``unique(target_x_post_id)`` violation: silently drop
             (first insert wins — could be from manual paste or an
             earlier sweep).
  4. Write a ``scheduled_job`` audit-log row with queries-run /
     candidates-discovered / candidates-verified /
     candidates-rejected counts (§28.30).

On ``GrokRateLimitError``: sweep pauses, respects Retry-After,
resumes. In-progress-but-unverified candidates are dropped
(re-discovered next sweep).

On ``GrokCostCeilingError``: sweep aborts mid-sweep; remaining
queries skipped. Settings banner displays "Grok paused: combined AI
ceiling hit." per §28.6.

Manual fallback inviolable: every API path has a separately-tested
manual equivalent. The Grok sweep failing means Daniel still has the
queue's manual-paste affordance.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app import grok_client, x_client  # noqa: E402
from app.agent import audit_log, settings_io  # noqa: E402
from app.agent.reply_targets import (  # noqa: E402
    GrokVerificationResult,
    engagement_surface_score,
    engagement_surface_thresholds,
    saturation_score,
    timing_score,
    verify_grok_candidate_against_x_api,
)
from app.agent.timeparse import parse_x_api_datetime  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402

# P9R-45: rename to _LOG to match the project-wide convention used by
# app/grok_client.py and app/x_client.py. The bare _log name diverged
# from the rest of the codebase.
_LOG = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


# launchd ExitTimeOut is 300s; we keep the per-sweep wall-clock guard
# below that so the sweep can always finish its audit-log row even when
# Grok or the X API are slow.
_MAX_SWEEP_SECONDS: float = 240.0

# Bounded ceiling on the in-job sleep when Grok rate-limits us. Same
# rationale as ``app/x_client.py::_MAX_RATE_LIMIT_WAIT_SECONDS``.
_MAX_RATE_LIMIT_WAIT_SECONDS: float = 90.0

# P9R-59: filesystem lock to keep the launchd cron and the Streamlit
# "Run sweep now" button from running the sweep concurrently. Two
# parallel sweeps would double-spend tokens at xAI and could partially
# collide on grok_api_responses + reply_targets writes. The unique
# constraint on target_x_post_id keeps rows correct, but the rate-
# limit budget at xAI is global to Daniel's account.
_SWEEP_LOCK_PATH: Path = Path("data") / "grok_sweep.lock"


class SweepAlreadyRunning(RuntimeError):
    """Another sweep instance holds the filesystem lock — abort cleanly."""


@contextlib.contextmanager
def _sweep_lock(lock_path: Path = _SWEEP_LOCK_PATH) -> Iterator[None]:
    """fcntl.flock(LOCK_EX | LOCK_NB) on ``lock_path``.

    Raises ``SweepAlreadyRunning`` if another process holds the lock;
    on Windows or any platform without fcntl, this is a no-op (the
    project is macOS-only per CLAUDE.md, so we don't need a Windows
    fallback today). The lock is released on context exit even if the
    sweep raises.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "a+")  # noqa: SIM115 — manual close in finally
    try:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise SweepAlreadyRunning(
                    "Another grok_discovery_sweep is already running "
                    f"(lock held on {lock_path}). Skipping this run."
                ) from exc
            raise
        # Record PID + start time inside the lock file so an operator
        # tail can see who holds it.
        try:
            fp.seek(0)
            fp.truncate()
            fp.write(f"pid={os.getpid()} started={time.time():.0f}\n")
            fp.flush()
        except OSError:
            pass
        yield
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fp.close()


def _read_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read every settings row this sweep depends on (§29.4 + §29.12)."""
    return {
        "grok_api_enabled": settings_io.get_bool(
            conn, "grok_api_enabled", True
        ),
        "grok_query_list_json": settings_io.get_json(
            conn, "grok_query_list_json", []
        ),
        "engagement_surface_floor_likes": settings_io.get_int(
            conn, "engagement_surface_floor_likes", 15
        ),
        "engagement_surface_pct_of_author": float(
            settings_io.get_json(conn, "engagement_surface_pct_of_author", 0.001)
        ),
        "engagement_surface_high_floor_likes": settings_io.get_int(
            conn, "engagement_surface_high_floor_likes", 50
        ),
        "engagement_surface_high_pct": float(
            settings_io.get_json(conn, "engagement_surface_high_pct", 0.005)
        ),
    }


def _extract_public_metrics(tweet: dict[str, Any]) -> dict[str, Any]:
    """Pull the X API public_metrics + non_public_metrics into a flat dict.

    P9R-48 split helper. Returns a dict shaped for the reply_targets
    INSERT — like_count / reply_count / repost_count / quote_count /
    bookmark_count / impression_count. NULLable for the two
    non_public columns since X API only returns them for tweets the
    authenticated user owns.
    """
    public = tweet.get("public_metrics") or {}
    return {
        "like_count": int(public.get("like_count") or 0),
        "reply_count": int(public.get("reply_count") or 0),
        "repost_count": int(public.get("retweet_count") or 0),
        "quote_count": int(public.get("quote_count") or 0),
        "bookmark_count": public.get("bookmark_count"),
        "impression_count": public.get("impression_count"),
    }


def _extract_author_fields(
    author: dict[str, Any] | None,
) -> tuple[int | None, str | None]:
    """Pull (follower_count, canonical_handle) from the includes.users entry.

    P9R-48 split helper. P9R-3 + P9R-38: live follower_count and the
    current username, both straight from the X API expansion. Returns
    (None, None) when X API omitted the expansion — the sweep falls
    back to §29.4 absolute floors + the URL-derived handle.
    """
    if not author or not isinstance(author, dict):
        return (None, None)
    follower_count: int | None = None
    canonical_handle: str | None = None
    metrics = author.get("public_metrics") or {}
    if isinstance(metrics, dict):
        fc = metrics.get("followers_count")
        if isinstance(fc, int):
            follower_count = fc
    u = author.get("username")
    if isinstance(u, str) and u.strip():
        canonical_handle = u.strip()
    return (follower_count, canonical_handle)


def _compute_age_minutes(created_at: str | None) -> int | None:
    """Convert an X API created_at string into minutes-since-now or None.

    P9R-48 split helper. parse_x_api_datetime tolerates both the
    Phase-7 ISO-T shape and the legacy fixed-width SQLite shape.
    """
    if not created_at:
        return None
    created_dt = parse_x_api_datetime(created_at)
    if created_dt is None:
        return None
    return int(
        (datetime.now(timezone.utc) - created_dt).total_seconds() / 60.0
    )


def _build_score_block(
    tweet: dict[str, Any],
    *,
    follower_count: int | None,
    canonical_handle: str | None,
    settings_dict: dict[str, Any],
) -> dict[str, Any]:
    """Run §29.3/§29.4 scoring against extracted X API metrics.

    P9R-48 split helper. All three scoring dimensions (engagement
    surface, saturation, timing) are derived; relevance_score +
    reply_opportunity_score stay NULL until Daniel reviews the
    candidate per §29.12 'Daniel-judged dimensions stay NULL'.
    """
    metrics = _extract_public_metrics(tweet)
    medium_th, high_th = engagement_surface_thresholds(
        follower_count, settings_dict
    )
    metrics["engagement_surface_score"] = engagement_surface_score(
        metrics["like_count"], medium_th, high_th
    )
    metrics["saturation_score"] = saturation_score(metrics["reply_count"])
    age_minutes = _compute_age_minutes(tweet.get("created_at"))
    # P9R-16: feed real follower count into timing_score.
    metrics["timing_score"] = (
        timing_score(age_minutes, follower_count)
        if age_minutes is not None else None
    )
    metrics["post_age_minutes"] = age_minutes
    metrics["target_text"] = tweet.get("text")
    metrics["target_created_at_utc"] = tweet.get("created_at")
    metrics["target_author_follower_count"] = follower_count
    metrics["target_author_handle"] = canonical_handle
    return metrics


def _verify_and_score(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
    settings_dict: dict[str, Any],
) -> tuple[GrokVerificationResult, dict[str, Any] | None]:
    """Verify a Grok candidate against X API and pre-compute the score block.

    P9R-48: orchestrates four small pure helpers (verify → extract
    metrics → extract author → score). Each step is independently
    testable; this function just routes the data through.

    Returns ``(verification_result, score_block_or_None)``. On 404 or
    any other non-200 outcome the score block is None and the caller
    is expected to log a rejection_reason. Lets the X-API exceptions
    propagate to the sweep's outer loop (rate-limit / 5xx handling).
    """
    result = verify_grok_candidate_against_x_api(
        candidate.target_x_post_id, conn=conn
    )
    if not result.verified or result.tweet is None:
        return (result, None)
    follower_count, canonical_handle = _extract_author_fields(result.author)
    score_block = _build_score_block(
        result.tweet,
        follower_count=follower_count,
        canonical_handle=canonical_handle,
        settings_dict=settings_dict,
    )
    return (result, score_block)


def _insert_candidate(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
    score_block: dict[str, Any],
) -> int | None:
    """Insert a verified Grok candidate into ``reply_targets``.

    Returns the new row id on success, or None if the unique constraint
    on ``target_x_post_id`` / ``target_post_url`` rejected the insert
    (dedupe — first insert wins per §29.11).
    """
    # P9R-38: prefer canonical handle from the X API includes.users[0]
    # over the (possibly stale) handle parsed from the Grok citation
    # URL. Falls back to the URL-derived value when X API didn't carry
    # the expansion.
    #
    # P9R-43: source='grok_firehose' (migration 022 extended the
    # source CHECK enum). The discovered_via column carries the
    # canonical 'grok_semantic' provenance; source now also tells the
    # truth so a future grep for source='paste_url' doesn't surface
    # Grok rows.
    effective_handle = (
        score_block.get("target_author_handle")
        or candidate.target_author_handle
    )
    try:
        cur = conn.execute(
            """
            INSERT INTO reply_targets
                (discovered_via, source, source_platform,
                 target_post_url, target_x_post_id,
                 target_author_handle, target_author_follower_count,
                 target_text,
                 target_created_at_utc, post_age_minutes,
                 like_count, reply_count, repost_count, quote_count,
                 bookmark_count, impression_count,
                 engagement_surface_score, saturation_score,
                 timing_score, status)
            VALUES
                ('grok_semantic', 'grok_firehose', 'x',
                 ?, ?, ?, ?, ?, ?, ?,
                 ?, ?, ?, ?, ?, ?,
                 ?, ?, ?, 'candidate')
            RETURNING id
            """,
            (
                candidate.target_post_url,
                candidate.target_x_post_id,
                effective_handle,
                score_block.get("target_author_follower_count"),  # P9R-3
                score_block.get("target_text"),
                score_block.get("target_created_at_utc"),
                score_block.get("post_age_minutes"),
                score_block["like_count"],
                score_block["reply_count"],
                score_block["repost_count"],
                score_block["quote_count"],
                score_block.get("bookmark_count"),
                score_block.get("impression_count"),
                score_block["engagement_surface_score"],
                score_block["saturation_score"],
                score_block.get("timing_score"),
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except sqlite3.IntegrityError as exc:
        # Either target_post_url or target_x_post_id unique violation —
        # dedupe per §29.11 Grok edge case. Silently drop; the original
        # insert's discovered_via stays in place (manual / agent_score /
        # next_rep_seed / v1.1_api_search / grok_semantic — whichever
        # got there first).
        _LOG.debug(
            "grok_discovery_sweep: dedupe drop x_post_id=%s (%s)",
            candidate.target_x_post_id, exc,
        )
        return None


def run(
    conn: sqlite3.Connection,
    *,
    settings_override: dict[str, Any] | None = None,
    max_results_per_query: int = grok_client.DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Execute one sweep across the configured Grok query list.

    Returns a summary dict suitable for the ``scheduled_job`` audit
    row's ``details_json`` payload. The summary is also the return
    value of ``main()`` for CLI consumption.

    P9R-57: the Streamlit "Run sweep now" button in
    ``app/pages/7_Settings.py`` intentionally calls ``run(conn)``
    with no overrides — the DB is the source of truth for
    ``grok_query_list_json`` + ``grok_api_enabled``. Don't add a
    settings-override kwarg pass-through from the UI without
    discussing first; the UI surface is "save settings → click run"
    and the run is meant to honor exactly what's in the DB.

    ``settings_override`` is a test-only escape hatch so unit tests
    don't have to write to the settings table. Production callers
    (the launchd cron, the Streamlit button) pass None and let
    ``_read_settings`` resolve from the DB.
    """
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "queries_run": 0,
        "candidates_discovered": 0,
        "candidates_verified": 0,
        "candidates_rejected_404": 0,
        "candidates_rejected_other": 0,  # P9R-8
        "candidates_dedupe_dropped": 0,
        "candidates_inserted": 0,
        "candidates_dropped_wall_clock": 0,  # P9R-50
        "rate_limit_pauses": 0,
        "rate_limit_aborts": 0,
        "cost_ceiling_aborts": 0,
        "server_errors": 0,
        "grok_client_errors": 0,  # P9R-2 — non-429/non-5xx Grok 4xx
        "x_api_server_errors": 0,  # P9R-27
        "error": None,
        "elapsed_seconds": 0.0,
    }

    settings_dict = settings_override or _read_settings(conn)

    if not settings_dict.get("grok_api_enabled"):
        summary["error"] = "grok_api_enabled=FALSE; sweep aborted"
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    query_list = settings_dict.get("grok_query_list_json") or []
    if not isinstance(query_list, list):
        summary["error"] = "grok_query_list_json not a JSON array; sweep aborted"
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    queries: list[str] = [
        str(q).strip() for q in query_list if isinstance(q, str) and q.strip()
    ]
    if not queries:
        summary["error"] = "grok_query_list_json empty; nothing to do"
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    deferred_queries = 0
    for query in queries:
        # P9R-6 + P9R-12: wall-clock guard so launchd's ExitTimeOut never
        # gets to SIGKILL us mid-INSERT. If we're near the limit, write
        # the audit row for what we did and exit cleanly. P9R-12 sets
        # summary['error'] so main()'s success=… computation truthfully
        # reflects "we ran out of time" rather than reporting success.
        if (time.perf_counter() - started) > _MAX_SWEEP_SECONDS:
            deferred_queries = len(queries) - summary["queries_run"]
            _LOG.warning(
                "grok_discovery_sweep: wall-clock budget exhausted after "
                "%d queries; deferring %d to next sweep",
                summary["queries_run"], deferred_queries,
            )
            summary["error"] = (
                f"wall-clock budget {_MAX_SWEEP_SECONDS}s exhausted after "
                f"{summary['queries_run']} queries; {deferred_queries} "
                f"deferred to next sweep"
            )
            summary["deferred_queries"] = deferred_queries
            break

        try:
            candidates = grok_client.search(
                query, conn=conn, max_results=max_results_per_query
            )
        except grok_client.GrokCostCeilingError:
            summary["cost_ceiling_aborts"] += 1
            summary["error"] = (
                "§28.6 combined Anthropic + xAI ceiling reached; "
                "sweep aborted mid-run"
            )
            break
        except grok_client.GrokRateLimitError as rate:
            wait = max(1.0, float(rate.retry_after_seconds or 60.0))
            if wait > _MAX_RATE_LIMIT_WAIT_SECONDS:
                summary["rate_limit_aborts"] += 1
                summary["error"] = (
                    f"Grok rate-limited; retry_after={wait}s exceeds "
                    f"in-job cap ({_MAX_RATE_LIMIT_WAIT_SECONDS}s) — "
                    f"next sweep will retry"
                )
                break
            _LOG.warning(
                "grok_discovery_sweep rate-limited; sleeping %.0fs", wait
            )
            summary["rate_limit_pauses"] += 1
            time.sleep(wait)
            # Drop this query's in-flight work and move on — re-discovery
            # next sweep is fine per §29.11.
            continue
        except grok_client.GrokServerError as srv:
            summary["server_errors"] += 1
            _LOG.warning(
                "grok_discovery_sweep: Grok server error for query=%r: %s",
                query, srv,
            )
            continue
        except grok_client.GrokUnavailable as uv:
            summary["error"] = f"Grok unavailable: {uv}"
            break
        except grok_client.GrokError as exc:
            # P9R-2: catch-all for bare GrokError (any non-429/non-5xx 4xx
            # — 400/401/403). Without this clause the sweep would crash
            # uncaught, skipping the final audit_log.log() in main() and
            # leaving NO scheduled_job row, defeating §28.30. Tally a
            # generic counter and continue with the next query so a
            # single bad-payload query doesn't kill the whole sweep.
            summary["grok_client_errors"] = (
                summary.get("grok_client_errors", 0) + 1
            )
            _LOG.warning(
                "grok_discovery_sweep: Grok client error (status=%s) for "
                "query=%r: %s",
                getattr(exc, "status_code", None), query, exc,
            )
            continue

        summary["queries_run"] += 1
        summary["candidates_discovered"] += len(candidates)

        for candidate_idx, candidate in enumerate(candidates):
            # P9R-6: wall-clock guard inside the per-candidate loop too.
            # _MAX_SWEEP_SECONDS=240 is only meaningful if checked
            # between candidates — one query with 50 candidates × 30s
            # xurl timeout can blow past launchd's 300s ExitTimeOut.
            # Drop the remainder of this query's candidates and abort
            # outer loop on the next iteration via the existing guard.
            if (time.perf_counter() - started) > _MAX_SWEEP_SECONDS:
                remaining = len(candidates) - candidate_idx
                summary["candidates_dropped_wall_clock"] += remaining  # P9R-50
                _LOG.warning(
                    "grok_discovery_sweep: wall-clock budget exhausted "
                    "mid-query; dropping %d candidates for query=%r",
                    remaining, query[:80],
                )
                if summary["error"] is None:
                    summary["error"] = (
                        f"wall-clock budget {_MAX_SWEEP_SECONDS}s "
                        f"exhausted mid-query; {remaining} candidates "
                        f"deferred to next sweep"
                    )
                break

            # X-API verification + scoring. Rate-limit / 5xx errors
            # from the X API bubble up and pause the loop the same way
            # the Grok call paths do.
            try:
                result, score_block = _verify_and_score(
                    conn, candidate=candidate, settings_dict=settings_dict
                )
            except x_client.XApiRateLimited as rate:
                wait = max(1.0, float(rate.retry_after_seconds or 60.0))
                if wait > _MAX_RATE_LIMIT_WAIT_SECONDS:
                    summary["rate_limit_aborts"] += 1
                    summary["error"] = (
                        f"X API rate-limited during verification; "
                        f"retry_after={wait}s exceeds in-job cap"
                    )
                    return _finalize_summary(summary, started)
                _LOG.warning(
                    "X API verify rate-limited; sleeping %.0fs", wait
                )
                summary["rate_limit_pauses"] += 1
                time.sleep(wait)
                continue
            except x_client.XApiUnavailable as uv:
                summary["error"] = f"X API unavailable during verify: {uv}"
                return _finalize_summary(summary, started)
            except x_client.XApiServerError as srv:
                # P9R-27: dedicated branch for X API 5xx during verify.
                # Pre-fix this collapsed into the generic XApiError catch
                # with no counter and no targeted handling. Now: bump
                # x_api_server_errors and continue (the candidate is
                # dropped — re-discovered next sweep).
                summary["x_api_server_errors"] += 1
                _LOG.warning(
                    "X API 5xx during verify for x_post_id=%s: %s",
                    candidate.target_x_post_id, srv,
                )
                continue
            except x_client.XApiError as exc:
                _LOG.warning(
                    "X API verify error for x_post_id=%s: %s",
                    candidate.target_x_post_id, exc,
                )
                continue

            if not result.verified:
                if result.status_code == 404:
                    summary["candidates_rejected_404"] += 1
                    _log_verification_404(conn, candidate=candidate)
                else:
                    # P9R-8: non-404 verification failures (body not a
                    # dict, 2xx with no data object, X-API contract
                    # drift) were silently dropped pre-fix with no
                    # audit row and no counter. Track them and log to
                    # grok_api_responses so the Settings "Recent Grok
                    # failures" panel surfaces them.
                    summary["candidates_rejected_other"] += 1
                    _log_verification_rejection(
                        conn,
                        candidate=candidate,
                        status_code=result.status_code,
                        error=result.error,
                    )
                continue

            assert score_block is not None  # contract of _verify_and_score

            # P9R-9: candidates_verified counts "passed §29.2 verification",
            # NOT "got inserted". The old placement (inside `else: insert
            # succeeded` branch) under-counted by skipping dedupe-dropped
            # candidates that DID pass verification. Increment here so
            # the audit row tells the truth.
            summary["candidates_verified"] += 1

            new_id = _insert_candidate(
                conn, candidate=candidate, score_block=score_block
            )
            if new_id is None:
                summary["candidates_dedupe_dropped"] += 1
            else:
                summary["candidates_inserted"] += 1

    return _finalize_summary(summary, started)


def _finalize_summary(summary: dict[str, Any], started: float) -> dict[str, Any]:
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return summary


def run_once_locked(
    conn: sqlite3.Connection,
    *,
    max_results_per_query: int = grok_client.DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Run one sweep behind the shared filesystem lock.

    Used by both the scheduled-job CLI and the foreground Agent Ops action so
    launchd, Streamlit, and the native sidecar cannot double-spend the same
    Grok/X API budget concurrently.
    """
    started = time.perf_counter()
    try:
        with _sweep_lock():
            return run(conn, max_results_per_query=max_results_per_query)
    except SweepAlreadyRunning as locked:
        summary = {
            "queries_run": 0,
            "candidates_discovered": 0,
            "candidates_verified": 0,
            "candidates_inserted": 0,
            "candidates_rejected_404": 0,
            "error": str(locked),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "concurrent_skip": True,
        }
        _LOG.info("grok_discovery_sweep: %s", locked)
        return summary


def _log_verification_404(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
) -> None:
    """§29.2 verification 404 audit row. Thin wrapper around
    ``_log_verification_rejection`` to preserve the public name the
    Phase 9 callsites expect."""
    _log_verification_rejection(
        conn, candidate=candidate, status_code=404,
        error="X API /2/tweets/{id} returned 404",
    )


def _log_verification_rejection(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
    status_code: int | None,
    error: str | None,
) -> None:
    """Write a ``grok_api_responses`` row marking the candidate as rejected.

    P9R-42 (DRY): delegates to ``grok_client._log_grok_response`` so the
    audit-insert shape, broad-except handling, and JSONL sidecar
    fallback live in exactly one place. Picks ``rejection_reason``
    based on status_code — 404 → 'verification_404', anything else →
    'http_error_other'.

    P9R-37: ``duration_ms=0`` (the helper requires int). The
    verification call's actual elapsed time isn't threaded through to
    this logger today; the column is dropped from the Settings panel's
    failures-table display, so the 0 isn't user-visible.
    """
    rejection_reason = (
        "verification_404" if status_code == 404 else "http_error_other"
    )
    grok_client._log_grok_response(
        conn,
        query=f"§29.2 verification: x_post_id={candidate.target_x_post_id}",
        request_payload={
            "phase": "verification",
            "target_x_post_id": candidate.target_x_post_id,
            "target_post_url": candidate.target_post_url,
            "target_author_handle": candidate.target_author_handle,
        },
        response_status_code=status_code,
        response_body={"error": error or "verification failed"},
        rate_snapshot=None,
        rejection_reason=rejection_reason,
        duration_ms=0,
    )


def format_sweep_summary_for_ui(summary: dict[str, Any]) -> tuple[str, str]:
    """Format a sweep summary for the Settings 'Run sweep now' toast.

    P9R-49: extracted from the Streamlit button's inline body so a
    unit test can pin the formatting without driving a Streamlit
    AppTest. Returns ``(severity, message)`` where severity is
    ``'success'`` / ``'warning'`` / ``'error'`` and message is the
    one-line summary Daniel sees.

    The Settings page renders this via st.success / st.warning /
    st.error based on the severity.
    """
    if summary.get("error"):
        return (
            "warning",
            f"sweep finished with note: {summary['error']} "
            f"(discovered={summary.get('candidates_discovered', 0)}, "
            f"inserted={summary.get('candidates_inserted', 0)})",
        )
    return (
        "success",
        f"sweep OK · queries_run={summary.get('queries_run', 0)} · "
        f"discovered={summary.get('candidates_discovered', 0)} · "
        f"verified={summary.get('candidates_verified', 0)} · "
        f"inserted={summary.get('candidates_inserted', 0)} · "
        f"rejected_404={summary.get('candidates_rejected_404', 0)}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Path to the SQLite file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=grok_client.DEFAULT_MAX_RESULTS,
        help="Max candidates per Grok query.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    conn = connect(args.db_path) if args.db_path else connect()
    started = time.perf_counter()
    # P9R-7: pre-allocate a "did-not-start" summary so the finally
    # block can ALWAYS write the scheduled_job audit row even if run()
    # raises an unexpected exception. Defeats the §28.30 audit-invariant
    # gap where a hard crash inside run() left no audit_log row at all.
    summary: dict[str, Any] = {
        "queries_run": 0,
        "candidates_discovered": 0,
        "error": "did_not_start",
        "elapsed_seconds": 0.0,
    }
    unhandled_exc: BaseException | None = None
    try:
        # P9R-59: filesystem advisory lock so two concurrent sweeps
        # (e.g. launchd cadence overlapping with "Run sweep now")
        # can't double-spend at xAI. A second sweep attempting to
        # enter the lock raises SweepAlreadyRunning and writes a
        # short audit row instead.
        summary = run_once_locked(conn, max_results_per_query=args.max_results)
    except BaseException as exc:  # noqa: BLE001 — catch-all for audit safety
        unhandled_exc = exc
        summary["error"] = f"unhandled exception in run(): {exc!r}"[:500]
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        _LOG.exception("grok_discovery_sweep: unhandled exception in run()")
    finally:
        try:
            success = summary.get("error") is None
            audit_log.log(
                conn,
                event_category="scheduled_job",
                event_type="grok_discovery_sweep",
                target_type="job",
                target_id="grok_discovery_sweep",
                details=summary,
                success=success,
                error_message=summary.get("error"),
            )
            _LOG.info("grok_discovery_sweep summary: %s", summary)
        except Exception:  # noqa: BLE001 — audit-write best-effort
            _LOG.exception("grok_discovery_sweep: failed to write audit row")
        elapsed = round(time.perf_counter() - started, 3)
        _LOG.info("grok_discovery_sweep completed in %ss", elapsed)
        conn.close()
    if unhandled_exc is not None:
        raise unhandled_exc
    return 0 if summary.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
