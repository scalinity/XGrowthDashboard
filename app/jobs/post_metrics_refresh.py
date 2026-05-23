"""Phase 7 / §17 Phase 7 job #3 — hourly post metrics refresh.

Cron / launchd cadence: every ``post_metrics_refresh_interval_minutes``
(default 60). One sweep processes up to ``_BATCH_LIMIT`` posts, prioritized
by staleness:

* Daily refresh for posts < 14 days old.
* Weekly refresh for posts 14–90 days old.
* Monthly refresh for posts > 90 days old.

The priority is implemented via the SQL ``ORDER BY`` — the freshest
posts whose ``last_metrics_refresh_at_utc`` is older than its tier's
window come first. Each batch of up to 100 IDs hits
``/2/tweets?ids=<batch>&tweet.fields=public_metrics,non_public_metrics``
and writes the response to ``post_metric_snapshots`` while updating
``posts.last_metrics_refresh_at_utc``.

Rate-limit handling: 429 + Retry-After is respected via
``app.x_client.batch_request``; the second-attempt failure is surfaced
as an audit row with ``rate_limit_hits=1`` and the next sweep tries again.

Manual fallback: when ``data_collection_mode='manual'`` the job no-ops.
Manual post-metric snapshots remain available via the Today view's
metric-correction form.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app import x_client  # noqa: E402
from app.agent import audit_log  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402

_log = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Maximum posts per sweep. X API caps the /2/tweets?ids endpoint at 100
# IDs per call; we send one batch per sweep so the hourly job's
# wall-clock stays bounded.
_BATCH_LIMIT: int = 100

# Endpoint template — ``{ids}`` is interpolated by batch_request.
_METRICS_ENDPOINT_TEMPLATE = (
    "/2/tweets?ids={ids}"
    "&tweet.fields=public_metrics,non_public_metrics,organic_metrics"
)


def _get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return default


def _select_stale_post_ids(conn: sqlite3.Connection, limit: int) -> list[str]:
    """Return up to ``limit`` ``x_post_id`` strings due for refresh.

    Priority via SQL ``ORDER BY``: posts with a NULL
    ``last_metrics_refresh_at_utc`` come first (never refreshed), then
    rows whose age tier's window has elapsed, ordered by oldest-pulled
    first within the tier.
    """
    rows = conn.execute(
        """
        WITH staleness AS (
            SELECT
                x_post_id,
                created_date,
                last_metrics_refresh_at_utc,
                CASE
                    WHEN last_metrics_refresh_at_utc IS NULL THEN 0
                    WHEN julianday(date('now')) - julianday(created_date) <= 14
                         AND julianday('now') - julianday(last_metrics_refresh_at_utc) >= 1
                        THEN 1
                    WHEN julianday(date('now')) - julianday(created_date) <= 90
                         AND julianday('now') - julianday(last_metrics_refresh_at_utc) >= 7
                        THEN 2
                    WHEN julianday(date('now')) - julianday(created_date) > 90
                         AND julianday('now') - julianday(last_metrics_refresh_at_utc) >= 30
                        THEN 3
                    ELSE NULL
                END AS tier_due
            FROM posts
            WHERE x_post_id IS NOT NULL
        )
        SELECT x_post_id
        FROM staleness
        WHERE tier_due IS NOT NULL
        ORDER BY tier_due ASC,
                 COALESCE(last_metrics_refresh_at_utc, '0000') ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [str(r[0]) for r in rows if r[0]]


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    x_post_id: str,
    metrics: dict[str, Any],
    raw_response_id: int | None,
) -> bool:
    """Insert one ``post_metric_snapshots`` row and bump
    ``posts.last_metrics_refresh_at_utc``. Returns True on insert."""
    row = conn.execute(
        "SELECT id FROM posts WHERE x_post_id = ? LIMIT 1", (x_post_id,)
    ).fetchone()
    if row is None:
        return False
    post_id = int(row[0])
    public = metrics.get("public_metrics") or {}
    non_public = metrics.get("non_public_metrics") or {}
    organic = metrics.get("organic_metrics") or {}
    likes = public.get("like_count")
    replies = public.get("reply_count")
    reposts = public.get("retweet_count")
    quotes = public.get("quote_count")
    bookmarks = public.get("bookmark_count")
    impressions = (
        non_public.get("impression_count")
        or organic.get("impression_count")
        or public.get("impression_count")
    )
    engagements_total = None
    if all(v is not None for v in (likes, replies, reposts, quotes)):
        engagements_total = sum(int(v or 0) for v in (likes, replies, reposts, quotes))
    profile_clicks = non_public.get("user_profile_clicks") or organic.get(
        "user_profile_clicks"
    )
    url_link_clicks = non_public.get("url_link_clicks") or organic.get("url_link_clicks")

    data_quality = "exact" if impressions is not None else "partial"
    conn.execute(
        """
        INSERT INTO post_metric_snapshots
          (post_id, x_post_id, collected_at_utc,
           impressions, likes, replies, reposts, quotes, bookmarks,
           engagements_total, profile_clicks, url_link_clicks,
           source, data_quality, raw_response_id)
        VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', ?, ?)
        """,
        (
            post_id,
            x_post_id,
            impressions,
            likes,
            replies,
            reposts,
            quotes,
            bookmarks,
            engagements_total,
            profile_clicks,
            url_link_clicks,
            data_quality,
            raw_response_id,
        ),
    )
    conn.execute(
        "UPDATE posts SET last_metrics_refresh_at_utc = datetime('now') WHERE id = ?",
        (post_id,),
    )
    return True


def run(conn: sqlite3.Connection, *, batch_limit: int = _BATCH_LIMIT) -> dict[str, Any]:
    """Execute one sweep. Returns a summary dict.

    Caller manages the DB connection. The Settings → "Refresh metrics
    now" button calls this directly with ``batch_limit`` set lower.
    """
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "posts_refreshed": 0,
        "posts_skipped_missing_local": 0,
        "rate_limit_hits": 0,
        "raw_response_ids": [],
        "skipped_reason": None,
        "error": None,
    }

    if _get_setting(conn, "data_collection_mode", default="api") == "manual":
        summary["skipped_reason"] = "data_collection_mode=manual"
        return summary

    stale_ids = _select_stale_post_ids(conn, batch_limit)
    summary["candidates_considered"] = len(stale_ids)
    if not stale_ids:
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    try:
        responses = x_client.batch_request(
            _METRICS_ENDPOINT_TEMPLATE,
            stale_ids,
            conn=conn,
            batch_size=100,
            log_source="xurl",
        )
    except x_client.XApiRateLimited as rate:
        summary["rate_limit_hits"] = 1
        summary["error"] = f"rate-limited; retry_after={rate.retry_after_seconds}s"
        return summary
    except x_client.XApiError as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    for resp in responses:
        summary["raw_response_ids"].append(resp.raw_response_id)
        body = resp.body if isinstance(resp.body, dict) else {}
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            continue
        for tweet in rows:
            if not isinstance(tweet, dict):
                continue
            x_post_id = str(tweet.get("id") or "").strip()
            if not x_post_id:
                continue
            try:
                inserted = _insert_snapshot(
                    conn,
                    x_post_id=x_post_id,
                    metrics=tweet,
                    raw_response_id=resp.raw_response_id,
                )
            except sqlite3.IntegrityError as exc:
                _log.warning(
                    "post_metric_snapshots insert failed for %s: %s",
                    x_post_id,
                    exc,
                )
                continue
            if inserted:
                summary["posts_refreshed"] += 1
            else:
                summary["posts_skipped_missing_local"] += 1

    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Path to the SQLite file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--batch-limit", type=int, default=_BATCH_LIMIT, help="Posts per sweep."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    conn = connect(args.db_path) if args.db_path else connect()
    started = time.perf_counter()
    try:
        summary = run(conn, batch_limit=args.batch_limit)
        success = summary["error"] is None
        audit_log.log(
            conn,
            event_category="scheduled_job",
            event_type="post_metrics_refresh",
            target_type="job",
            target_id="post_metrics_refresh",
            details=summary,
            success=success,
            error_message=summary.get("error"),
        )
        _log.info("post_metrics_refresh summary: %s", summary)
        return 0 if success else 1
    finally:
        elapsed = round(time.perf_counter() - started, 3)
        _log.info("post_metrics_refresh completed in %ss", elapsed)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
