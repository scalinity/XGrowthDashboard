"""Phase 7 / §17 Phase 7 job #1 — daily account snapshot via X API.

Cron / launchd cadence: every day at 09:00 America/New_York.

Calls ``xurl /2/users/me?user.fields=public_metrics`` once, parses the
``data.public_metrics`` block, and inserts one row into
``account_snapshots`` with ``source='api'`` and ``data_quality='exact'``.

Duplicate-day handling: if an ``account_snapshots`` row already exists
for today's date AND ``source IN ('manual', 'csv_import')`` the manual
entry wins and the job logs an audit row noting the skip. Same-day
``source='api'`` rows are NOT skipped (re-running the job after
correcting auth, for example, should reflect the latest pull).

Manual fallback discipline: when ``data_collection_mode='manual'`` the
job exits 0 without making any API call and writes a ``scheduled_job``
audit row noting the no-op. The Today view's pinned manual snapshot
form is the always-available alternative.

Usage:

    uv run python -m scripts.collect_account_snapshot

Exit codes:

* 0 — snapshot recorded (or skipped per manual-fallback / duplicate-day rule).
* 1 — unrecoverable failure (xurl missing, auth expired, network error,
      schema mismatch). The audit row carries the failure reason.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import x_client  # noqa: E402
from app.agent import audit_log  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402

_log = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


_USER_ME_ENDPOINT = "/2/users/me?user.fields=public_metrics,description"


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


def _already_have_manual_snapshot_today(
    conn: sqlite3.Connection, today_iso: str, username: str
) -> bool:
    """True iff a manually-entered snapshot for today already exists."""
    row = conn.execute(
        """
        SELECT 1 FROM account_snapshots
        WHERE snapshot_date = ?
          AND username = ?
          AND source IN ('manual', 'csv_import')
        LIMIT 1
        """,
        (today_iso, username),
    ).fetchone()
    return row is not None


def run(conn: sqlite3.Connection, *, today_iso: str | None = None) -> dict[str, Any]:
    """Execute the account-snapshot job. Returns a summary dict.

    Caller is responsible for opening + closing the DB connection. This
    function is the dashboard's foreground entry point too — the
    Settings → "Refresh account snapshot now" button calls it directly.
    """
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "snapshot_inserted": False,
        "skipped_reason": None,
        "rate_limit_hits": 0,
        "raw_response_id": None,
        "error": None,
    }
    today = today_iso or time.strftime("%Y-%m-%d", time.gmtime())

    mode = _get_setting(conn, "data_collection_mode", default="api")
    if mode == "manual":
        summary["skipped_reason"] = "data_collection_mode=manual"
        return summary

    # RV2-12: normalize the stored handle. Daniel sometimes edits
    # ``x_handle`` to ``@dannyscalant`` via the Settings UI; the manual
    # account_snapshots rows store the bare ``dannyscalant`` shape, so
    # an unnormalized query would miss the duplicate-day guard and
    # break the §17 'manual entry wins' contract.
    username_raw = _get_setting(conn, "x_handle", default="dannyscalant")
    username = str(username_raw or "dannyscalant").lstrip("@").strip()
    if _already_have_manual_snapshot_today(conn, today, username):
        summary["skipped_reason"] = "duplicate_day_manual_entry_present"
        return summary

    try:
        resp = x_client.request(
            _USER_ME_ENDPOINT,
            method="GET",
            conn=conn,
            log_source="xurl",
            log_notes="collect_account_snapshot daily",
        )
    except x_client.XApiRateLimited as rate:
        summary["rate_limit_hits"] = 1
        summary["error"] = (
            f"rate-limited; retry_after={rate.retry_after_seconds}s"
        )
        # Do NOT insert a partial row. Manual form remains the fallback.
        return summary
    except x_client.XApiError as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    summary["raw_response_id"] = resp.raw_response_id
    body = resp.body if isinstance(resp.body, dict) else {}
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        summary["error"] = "X API response missing 'data' object"
        return summary
    metrics = data.get("public_metrics") or {}
    baseline = int(_get_setting(conn, "baseline_followers", default=61) or 61)
    profile_url = _get_setting(
        conn, "profile_url", default=f"https://x.com/{username}"
    )

    try:
        conn.execute(
            """
            INSERT INTO account_snapshots
              (snapshot_date, collected_at_utc, x_user_id, username, profile_url,
               followers_count, following_count, post_count, listed_count,
               like_count, media_count, bio_text, baseline_followers,
               source, data_quality, raw_response_id)
            VALUES
              (?, datetime('now'), ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?,
               'api', 'exact', ?)
            """,
            (
                today,
                str(data.get("id") or "") or None,
                data.get("username") or username,
                profile_url,
                int(metrics.get("followers_count") or 0),
                int(metrics.get("following_count") or 0),
                int(metrics.get("tweet_count") or 0),
                int(metrics.get("listed_count") or 0),
                metrics.get("like_count"),
                metrics.get("media_count"),
                data.get("description"),
                baseline,
                summary["raw_response_id"],
            ),
        )
        summary["snapshot_inserted"] = True
    except sqlite3.IntegrityError as exc:
        # The unique index on (x_user_id, collected_at_utc) or (username,
        # collected_at_utc) only fires if the SAME nanosecond produced
        # two inserts; in practice the duplicate-day case is handled
        # above. Re-raise as a clean error.
        summary["error"] = f"IntegrityError on insert: {exc}"
        return summary

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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    conn = connect(args.db_path) if args.db_path else connect()
    started = time.perf_counter()
    try:
        summary = run(conn)
        success = summary["error"] is None
        audit_log.log(
            conn,
            event_category="scheduled_job",
            event_type="collect_account_snapshot",
            target_type="job",
            target_id="collect_account_snapshot",
            details=summary,
            success=success,
            error_message=summary.get("error"),
        )
        _log.info("collect_account_snapshot summary: %s", summary)
        return 0 if success else 1
    finally:
        elapsed = round(time.perf_counter() - started, 3)
        _log.info("collect_account_snapshot completed in %ss", elapsed)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
