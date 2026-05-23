"""Phase 7 / §17 Phase 7 job #2 — daily import of recent X posts.

Cron / launchd cadence: every day, immediately after the
``collect_account_snapshot`` job. Idempotent — re-running on the same
day surfaces the same tweets and skips them via the existing
``posts.x_post_id`` UNIQUE constraint.

Two run modes:

* Daily incremental (default) — pulls the latest 100 posts via
  ``GET /2/users/me/tweets?max_results=100`` and inserts any whose
  ``x_post_id`` doesn't already exist. New rows land with
  ``posted_via='api'`` and ``manual_confirmation_status='needs_metrics'``
  so they surface in the "Needs tagging" queue. Daniel's manually-added
  metadata (``pillar`` / ``audience`` / ``hypothesis``) on existing rows
  is NEVER overwritten (skip-existing-``x_post_id`` discipline).

* ``--backfill`` (one-shot at Phase 7 install) — same call but adds an
  ``audit_logs`` row with ``event_category='admin'``,
  ``event_type='phase_7_post_backfill'`` so re-runs are detectable. The
  audit-log row's existence gates re-runs: if it's already present the
  backfill exits 0 with summary['skipped_reason']='already_ran'. The
  X API call itself is the SAME endpoint; the only difference is the
  audit-row gate.

Manual fallback discipline: when ``data_collection_mode='manual'`` the
job exits 0 without API calls; manual entry via the Today form is the
fallback.

Usage:

    uv run python -m scripts.import_recent_posts           # daily
    uv run python -m scripts.import_recent_posts --backfill # one-shot

Exit codes:

* 0 — import completed (or skipped per manual-fallback / backfill-replay).
* 1 — unrecoverable failure. Audit row carries the reason.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import x_client  # noqa: E402
from app.agent import audit_log  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402

_log = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

_RECENT_POSTS_ENDPOINT = (
    "/2/users/me/tweets?max_results=100"
    "&tweet.fields=created_at,conversation_id,in_reply_to_user_id,entities,referenced_tweets"
)
_BACKFILL_AUDIT_EVENT = "phase_7_post_backfill"


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


def _backfill_already_ran(conn: sqlite3.Connection) -> bool:
    """True iff a ``phase_7_post_backfill`` audit row already exists."""
    row = conn.execute(
        """
        SELECT 1 FROM audit_logs
        WHERE event_category = 'admin'
          AND event_type = ?
        LIMIT 1
        """,
        (_BACKFILL_AUDIT_EVENT,),
    ).fetchone()
    return row is not None


def _x_post_id_exists(conn: sqlite3.Connection, x_post_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM posts WHERE x_post_id = ? LIMIT 1", (x_post_id,)
    ).fetchone()
    return row is not None


def _categorize_post(tweet: dict[str, Any]) -> str:
    """Map an X API tweet's referenced_tweets shape to ``posts.type``.

    posts.type CHECK: ('standalone', 'reply', 'quote', 'thread_root',
    'thread_child'). The X API surfaces this via ``referenced_tweets``:

    - ``replied_to`` → 'reply'
    - ``quoted``     → 'quote'
    - empty / none   → 'standalone' (thread roots are also standalones
                       from the API's perspective; thread continuation
                       is a Daniel-level annotation, not a tweet field)
    """
    refs = tweet.get("referenced_tweets") or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind = ref.get("type")
        if kind == "replied_to":
            return "reply"
        if kind == "quoted":
            return "quote"
    return "standalone"


def _normalize_created_at(api_value: Any) -> tuple[str | None, str]:
    """Map X API ``created_at`` (RFC 3339 UTC) to (iso_string, YYYY-MM-DD).

    Used to populate ``posts.created_at_utc`` + ``posts.created_date``.
    On parse failure, falls back to today's UTC date.
    """
    if isinstance(api_value, str) and api_value:
        try:
            dt = datetime.fromisoformat(api_value.replace("Z", "+00:00"))
            return dt.isoformat(), dt.astimezone(timezone.utc).date().isoformat()
        except ValueError:
            pass
    today = datetime.now(timezone.utc).date().isoformat()
    return None, today


def run(
    conn: sqlite3.Connection, *, backfill: bool = False
) -> dict[str, Any]:
    """Execute the import job. Returns a summary dict.

    ``backfill=True`` is the one-shot Phase 7 install backfill. Re-running
    with ``backfill=True`` after the audit row is recorded skips the
    operation entirely so the audit log doesn't get a second
    ``phase_7_post_backfill`` row (idempotency contract).
    """
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "backfill": backfill,
        "posts_inserted": 0,
        "posts_skipped_existing": 0,
        "rate_limit_hits": 0,
        "raw_response_id": None,
        "skipped_reason": None,
        "error": None,
    }

    if _get_setting(conn, "data_collection_mode", default="api") == "manual":
        summary["skipped_reason"] = "data_collection_mode=manual"
        return summary

    if backfill and _backfill_already_ran(conn):
        summary["skipped_reason"] = "already_ran"
        return summary

    try:
        resp = x_client.request(
            _RECENT_POSTS_ENDPOINT,
            method="GET",
            conn=conn,
            log_source="xurl",
            log_notes=(
                "import_recent_posts backfill" if backfill
                else "import_recent_posts daily"
            ),
        )
    except x_client.XApiRateLimited as rate:
        summary["rate_limit_hits"] = 1
        summary["error"] = f"rate-limited; retry_after={rate.retry_after_seconds}s"
        return summary
    except x_client.XApiError as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    summary["raw_response_id"] = resp.raw_response_id
    body = resp.body if isinstance(resp.body, dict) else {}
    tweets = body.get("data") if isinstance(body, dict) else None
    if not isinstance(tweets, list):
        # No data block means the API returned an empty page — not an error.
        tweets = []

    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue
        x_post_id = str(tweet.get("id") or "").strip()
        if not x_post_id:
            continue

        # Skip-existing-x_post_id discipline. Manual metadata never
        # gets overwritten — Daniel's pillar/audience/hypothesis on
        # an existing row is preserved verbatim.
        if _x_post_id_exists(conn, x_post_id):
            summary["posts_skipped_existing"] += 1
            continue

        created_at_iso, created_date = _normalize_created_at(tweet.get("created_at"))
        text = tweet.get("text") or ""
        if not text:
            # Skip rows with no text; posts.text is NOT NULL per 001.
            continue

        post_type = _categorize_post(tweet)
        conversation_id = tweet.get("conversation_id")
        in_reply_to_user_id = tweet.get("in_reply_to_user_id")

        # Extract referenced replied-to id when applicable.
        in_reply_to_post_id: str | None = None
        refs = tweet.get("referenced_tweets") or []
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, dict) and ref.get("type") == "replied_to":
                    in_reply_to_post_id = str(ref.get("id") or "") or None
                    break

        contains_link = 0
        entities = tweet.get("entities") or {}
        if isinstance(entities, dict):
            urls = entities.get("urls") or []
            if isinstance(urls, list) and urls:
                contains_link = 1
        expanded_urls_json: str | None = None
        if isinstance(entities, dict):
            urls = entities.get("urls") or []
            if isinstance(urls, list) and urls:
                expanded_urls_json = json.dumps(
                    [u.get("expanded_url") for u in urls if isinstance(u, dict)]
                )

        try:
            conn.execute(
                """
                INSERT INTO posts
                  (x_post_id, created_at_utc, created_date, text, url, type,
                   conversation_id, in_reply_to_post_id, in_reply_to_user,
                   posted_via, manual_confirmation_status,
                   contains_link, expanded_urls_json, raw_response_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', 'needs_metrics', ?, ?, ?)
                """,
                (
                    x_post_id,
                    created_at_iso,
                    created_date,
                    text,
                    f"https://x.com/i/status/{x_post_id}",
                    post_type,
                    conversation_id,
                    in_reply_to_post_id,
                    str(in_reply_to_user_id) if in_reply_to_user_id else None,
                    contains_link,
                    expanded_urls_json,
                    summary["raw_response_id"],
                ),
            )
            summary["posts_inserted"] += 1
        except sqlite3.IntegrityError as exc:
            # UNIQUE on x_post_id can fire if a parallel insert raced; in
            # that case treat as skipped.
            _log.debug("posts insert race on %s: %s", x_post_id, exc)
            summary["posts_skipped_existing"] += 1

    # On a backfill run, write the audit gate row so re-runs short-circuit.
    if backfill:
        audit_log.log(
            conn,
            event_category="admin",
            event_type=_BACKFILL_AUDIT_EVENT,
            target_type="job",
            target_id="import_recent_posts",
            details={
                "posts_inserted": summary["posts_inserted"],
                "posts_skipped_existing": summary["posts_skipped_existing"],
                "raw_response_id": summary["raw_response_id"],
            },
            success=True,
        )

    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "One-shot backfill of existing post history. Idempotent — "
            "re-runs after the first backfill exit 0 without API calls."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Path to the SQLite file (default: {DEFAULT_DB_PATH}).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    conn = connect(args.db_path) if args.db_path else connect()
    started = time.perf_counter()
    try:
        summary = run(conn, backfill=args.backfill)
        success = summary["error"] is None
        audit_log.log(
            conn,
            event_category="scheduled_job",
            event_type=(
                "import_recent_posts_backfill" if args.backfill
                else "import_recent_posts"
            ),
            target_type="job",
            target_id="import_recent_posts",
            details=summary,
            success=success,
            error_message=summary.get("error"),
        )
        _log.info("import_recent_posts summary: %s", summary)
        return 0 if success else 1
    finally:
        elapsed = round(time.perf_counter() - started, 3)
        _log.info("import_recent_posts completed in %ss", elapsed)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
