"""Phase 9 — prune grok_api_responses to bound disk growth.

P9R-23: pre-fix, grok_api_responses was append-only forever. At ~10
sweeps/day × ~5 queries × ~50 verifications = ~25k rows/year, with
each row holding up to 64KB of response_body_json — ~1 GB/year worst
case. This script keeps the table bounded.

Retention rule (Phase 9 default):

  * Rows with rejection_reason IS NOT NULL OR status >= 400 are kept
    for 90 days — they're the operational debug surface.
  * Successful rows (rejection_reason IS NULL AND status < 400) are
    kept for 30 days — once §28.6 monthly spend has rolled over, the
    individual rate_snapshot_json rows are no longer load-bearing.

Both retentions are configurable via settings rows:

  * grok_api_responses_retention_failures_days  (default 90)
  * grok_api_responses_retention_success_days   (default 30)

Run manually:
    uv run python -m scripts.prune_grok_api_responses
    uv run python -m scripts.prune_grok_api_responses --dry-run

Or on a launchd cadence — there's no separate plist for it today;
Daniel can chain it onto the Phase 7 audit-log prune job when he
sets that up.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import audit_log, settings_io  # noqa: E402
from app.db import connect  # noqa: E402

_log = logging.getLogger(__name__)

_DEFAULT_FAILURES_DAYS: int = 90
_DEFAULT_SUCCESS_DAYS: int = 30


def prune(
    conn: sqlite3.Connection,
    *,
    failures_days: int | None = None,
    success_days: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Run one prune pass. Returns counts (rows_to_delete, etc.)."""
    if failures_days is None:
        failures_days = settings_io.get_int(
            conn, "grok_api_responses_retention_failures_days",
            _DEFAULT_FAILURES_DAYS,
        )
    if success_days is None:
        success_days = settings_io.get_int(
            conn, "grok_api_responses_retention_success_days",
            _DEFAULT_SUCCESS_DAYS,
        )

    counts: dict[str, int] = {
        "failures_older_than_days": failures_days,
        "success_older_than_days": success_days,
        "failures_pruned": 0,
        "success_pruned": 0,
        "total_pruned": 0,
        "table_size_before": 0,
        "table_size_after": 0,
    }
    counts["table_size_before"] = int(
        conn.execute("SELECT COUNT(*) FROM grok_api_responses").fetchone()[0]
    )

    # Failures branch: rejection_reason IS NOT NULL OR status >= 400.
    if dry_run:
        n = conn.execute(
            """
            SELECT COUNT(*) FROM grok_api_responses
             WHERE (rejection_reason IS NOT NULL
                    OR (response_status_code IS NOT NULL AND response_status_code >= 400))
               AND created_at_utc < datetime('now', ?)
            """,
            (f"-{failures_days} days",),
        ).fetchone()[0]
        counts["failures_pruned"] = int(n)
    else:
        cur = conn.execute(
            """
            DELETE FROM grok_api_responses
             WHERE (rejection_reason IS NOT NULL
                    OR (response_status_code IS NOT NULL AND response_status_code >= 400))
               AND created_at_utc < datetime('now', ?)
            """,
            (f"-{failures_days} days",),
        )
        counts["failures_pruned"] = cur.rowcount or 0

    # Success branch: rejection_reason IS NULL AND status < 400.
    if dry_run:
        n = conn.execute(
            """
            SELECT COUNT(*) FROM grok_api_responses
             WHERE rejection_reason IS NULL
               AND (response_status_code IS NULL OR response_status_code < 400)
               AND created_at_utc < datetime('now', ?)
            """,
            (f"-{success_days} days",),
        ).fetchone()[0]
        counts["success_pruned"] = int(n)
    else:
        cur = conn.execute(
            """
            DELETE FROM grok_api_responses
             WHERE rejection_reason IS NULL
               AND (response_status_code IS NULL OR response_status_code < 400)
               AND created_at_utc < datetime('now', ?)
            """,
            (f"-{success_days} days",),
        )
        counts["success_pruned"] = cur.rowcount or 0

    counts["total_pruned"] = counts["failures_pruned"] + counts["success_pruned"]
    counts["table_size_after"] = int(
        conn.execute("SELECT COUNT(*) FROM grok_api_responses").fetchone()[0]
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--failures-days", type=int, default=None)
    parser.add_argument("--success-days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    conn = connect(args.db_path) if args.db_path else connect()
    try:
        counts = prune(
            conn,
            failures_days=args.failures_days,
            success_days=args.success_days,
            dry_run=args.dry_run,
        )
        action = "dry-run" if args.dry_run else "applied"
        _log.info("grok_api_responses prune (%s): %s", action, counts)
        if not args.dry_run:
            audit_log.log(
                conn,
                event_category="scheduled_job",
                event_type="prune_grok_api_responses",
                target_type="job",
                target_id="prune_grok_api_responses",
                details=counts,
                success=True,
            )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
