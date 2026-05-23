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
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

_log = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


# launchd ExitTimeOut is 300s; we keep the per-sweep wall-clock guard
# below that so the sweep can always finish its audit-log row even when
# Grok or the X API are slow.
_MAX_SWEEP_SECONDS: float = 240.0

# Bounded ceiling on the in-job sleep when Grok rate-limits us. Same
# rationale as ``app/x_client.py::_MAX_RATE_LIMIT_WAIT_SECONDS``.
_MAX_RATE_LIMIT_WAIT_SECONDS: float = 90.0


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


def _verify_and_score(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
    settings_dict: dict[str, Any],
) -> tuple[GrokVerificationResult, dict[str, Any] | None]:
    """Verify a Grok candidate against X API and pre-compute the score block.

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

    public = result.tweet.get("public_metrics") or {}
    like_count = int(public.get("like_count") or 0)
    reply_count = int(public.get("reply_count") or 0)
    repost_count = int(public.get("retweet_count") or 0)
    quote_count = int(public.get("quote_count") or 0)
    bookmark_count = public.get("bookmark_count")
    impression_count = public.get("impression_count")

    # P9R-3: pull follower count from the includes.users expansion the
    # verify call requests. NULL falls back to the §29.4 absolute
    # floors — same conservative behavior as a Phase-5.6 manual paste
    # whose author follower_count is unknown — only on X API responses
    # that omit the expansion (no test cassette currently does so).
    follower_count: int | None = None
    canonical_handle: str | None = None
    if result.author and isinstance(result.author, dict):
        author_metrics = result.author.get("public_metrics") or {}
        if isinstance(author_metrics, dict):
            fc = author_metrics.get("followers_count")
            if isinstance(fc, int):
                follower_count = fc
        # P9R-38: prefer the canonical handle from the X API over the
        # one parsed from the Grok citation URL (handles change).
        u = result.author.get("username")
        if isinstance(u, str) and u.strip():
            canonical_handle = u.strip()

    medium_th, high_th = engagement_surface_thresholds(
        follower_count, settings_dict
    )
    eng = engagement_surface_score(like_count, medium_th, high_th)
    sat = saturation_score(reply_count)

    # Timing requires post age — derive from created_at if present.
    age_minutes: int | None = None
    created_at = result.tweet.get("created_at")
    if created_at:
        created_dt = parse_x_api_datetime(created_at)
        if created_dt is not None:
            age_minutes = int(
                (datetime.now(timezone.utc) - created_dt).total_seconds() / 60.0
            )
    tim: int | None = None
    if age_minutes is not None:
        # P9R-16: feed the real follower count into timing_score too.
        # None still falls back to the small-niche window (correct
        # behavior when follower count truly is unknown).
        tim = timing_score(age_minutes, follower_count)

    score_block = {
        "like_count": like_count,
        "reply_count": reply_count,
        "repost_count": repost_count,
        "quote_count": quote_count,
        "bookmark_count": bookmark_count,
        "impression_count": impression_count,
        "engagement_surface_score": eng,
        "saturation_score": sat,
        "timing_score": tim,
        "post_age_minutes": age_minutes,
        "target_text": result.tweet.get("text"),
        "target_created_at_utc": created_at,
        "target_author_follower_count": follower_count,  # P9R-3
        "target_author_handle": canonical_handle,  # P9R-38 — None = keep URL-derived
    }
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
    # the expansion. P9R-43: source='paste_url' is the closest
    # existing CHECK enum value — the discovered_via column carries
    # the canonical 'grok_semantic' provenance.
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
                ('grok_semantic', 'paste_url', 'x',
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
        _log.debug(
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

    for query in queries:
        # Wall-clock guard so launchd's ExitTimeOut never gets to SIGKILL
        # us mid-INSERT. If we're near the limit, write the audit row
        # for what we did and exit cleanly.
        if (time.perf_counter() - started) > _MAX_SWEEP_SECONDS:
            _log.warning(
                "grok_discovery_sweep: wall-clock budget exhausted after "
                "%d queries; deferring remainder to next sweep",
                summary["queries_run"],
            )
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
            _log.warning(
                "grok_discovery_sweep rate-limited; sleeping %.0fs", wait
            )
            summary["rate_limit_pauses"] += 1
            time.sleep(wait)
            # Drop this query's in-flight work and move on — re-discovery
            # next sweep is fine per §29.11.
            continue
        except grok_client.GrokServerError as srv:
            summary["server_errors"] += 1
            _log.warning(
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
            _log.warning(
                "grok_discovery_sweep: Grok client error (status=%s) for "
                "query=%r: %s",
                getattr(exc, "status_code", None), query, exc,
            )
            continue

        summary["queries_run"] += 1
        summary["candidates_discovered"] += len(candidates)

        for candidate in candidates:
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
                _log.warning(
                    "X API verify rate-limited; sleeping %.0fs", wait
                )
                summary["rate_limit_pauses"] += 1
                time.sleep(wait)
                continue
            except x_client.XApiUnavailable as uv:
                summary["error"] = f"X API unavailable during verify: {uv}"
                return _finalize_summary(summary, started)
            except x_client.XApiError as exc:
                _log.warning(
                    "X API verify error for x_post_id=%s: %s",
                    candidate.target_x_post_id, exc,
                )
                continue

            if not result.verified:
                if result.status_code == 404:
                    summary["candidates_rejected_404"] += 1
                    _log_verification_404(conn, candidate=candidate)
                continue

            assert score_block is not None  # contract of _verify_and_score

            new_id = _insert_candidate(
                conn, candidate=candidate, score_block=score_block
            )
            if new_id is None:
                summary["candidates_dedupe_dropped"] += 1
            else:
                summary["candidates_inserted"] += 1
                summary["candidates_verified"] += 1

    return _finalize_summary(summary, started)


def _finalize_summary(summary: dict[str, Any], started: float) -> dict[str, Any]:
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return summary


def _log_verification_404(
    conn: sqlite3.Connection,
    *,
    candidate: grok_client.GrokCandidate,
) -> None:
    """Write a ``grok_api_responses`` row marking the candidate as rejected.

    Mirrors the ``grok_client._log_grok_response`` shape but anchored to
    the verification step (not the original Grok call). This row is the
    machine-readable record that §29.2 verification dropped this
    candidate — the Settings "Recent Grok failures" panel surfaces it.
    """
    try:
        conn.execute(
            """
            INSERT INTO grok_api_responses
              (query, request_payload_json, response_status_code,
               response_body_json, rate_snapshot_json,
               rejection_reason, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"§29.2 verification: x_post_id={candidate.target_x_post_id}",
                json.dumps(
                    {
                        "phase": "verification",
                        "target_x_post_id": candidate.target_x_post_id,
                        "target_post_url": candidate.target_post_url,
                        "target_author_handle": candidate.target_author_handle,
                    }
                ),
                404,
                json.dumps({"error": "X API /2/tweets/{id} returned 404"}),
                None,
                "verification_404",
                0,
            ),
        )
    except sqlite3.OperationalError as exc:
        _log.warning(
            "grok_api_responses verification_404 insert failed (suppressed): %s",
            exc,
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
    try:
        summary = run(conn, max_results_per_query=args.max_results)
        success = summary["error"] is None
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
        _log.info("grok_discovery_sweep summary: %s", summary)
        return 0 if success else 1
    finally:
        elapsed = round(time.perf_counter() - started, 3)
        _log.info("grok_discovery_sweep completed in %ss", elapsed)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
