"""Phase 7 / §17 Phase 7 job #4 — hourly reply-target metrics refresh.

Cron / launchd cadence: every ``reply_target_metrics_refresh_interval_minutes``
(default 60).

Steps per sweep:

1. SELECT ``reply_targets`` WHERE ``status='candidate'`` AND
   ``target_x_post_id IS NOT NULL`` AND ``last_checked_at_utc`` is stale.
   Order oldest-first within the configured stale window.

2. Batch up to 100 IDs into ``/2/tweets?ids=<batch>&tweet.fields=public_metrics``.

3. For each returned tweet:

   * Insert one ``reply_target_snapshots`` row with current counters +
     computed_likes/replies_per_hour + computed_velocity_delta.
   * UPDATE ``reply_targets.last_checked_at_utc`` + the denormalized
     counters + ``velocity_score`` + ``timing_score``.
   * Re-run the base resolver + ``apply_velocity_timing_modifiers``;
     persist the adjusted ``engagement_surface_score`` (or its modifier
     bump) + ``recommended_action_label`` + ``recommended_action_score``.

4. For tweets the X API returned 404 on (omitted from the batch
   response or surfaced via the ``errors`` envelope): transition
   ``reply_targets.status='target_deleted'`` and set the corresponding
   ``audit_logs`` row.

5. 429 + Retry-After is respected by ``app.x_client.batch_request``; if
   that retry also fails, the sweep aborts with rate_limit_hits=1 and
   the per-candidate ``last_checked_at_utc`` stays at its previous value
   (no silent score drift per §29.11).

Manual fallback: ``data_collection_mode='manual'`` → no-op. The Queue's
manual "Refresh metrics" affordance remains available.
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
from app.agent.reply_targets import (  # noqa: E402
    ACTION_TO_SCORE,
    ReplyTargetSnapshot,
    apply_velocity_timing_modifiers,
    engagement_surface_score,
    engagement_surface_thresholds,
    resolve_recommended_action,
    saturation_score,
    timing_score,
    velocity_score,
)
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402

_log = logging.getLogger(__name__)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

_BATCH_LIMIT: int = 100

# RV2-15: minimum elapsed minutes for a velocity-rate computation. Below
# this threshold the per-hour rate amplifies sub-minute counter jitter
# into spurious gains/decays. One full minute is the floor where SQLite's
# YYYY-MM-DD HH:MM:SS resolution gives a stable delta.
_MIN_SAMPLE_MINUTES: float = 1.0

# /2/tweets?ids=… is the canonical batch endpoint; we add public_metrics
# only (reply-target candidates are third-party posts, so non_public_metrics
# /organic_metrics aren't authorized to Daniel's app).
_METRICS_ENDPOINT_TEMPLATE = (
    "/2/tweets?ids={ids}&tweet.fields=public_metrics,created_at"
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


def _select_stale_candidates(
    conn: sqlite3.Connection, *, limit: int, stale_minutes: int
) -> list[sqlite3.Row]:
    """Return up to ``limit`` candidate rows whose last refresh is older
    than ``stale_minutes``. Oldest-first within the stale set.
    """
    return conn.execute(
        """
        SELECT *
          FROM reply_targets
         WHERE status = 'candidate'
           AND target_x_post_id IS NOT NULL
           AND (
               last_checked_at_utc IS NULL
               OR julianday('now') - julianday(last_checked_at_utc) >= ?
           )
         ORDER BY last_checked_at_utc ASC
         LIMIT ?
        """,
        (stale_minutes / (24.0 * 60.0), int(limit)),
    ).fetchall()


def _load_snapshots(
    conn: sqlite3.Connection, reply_target_id: int, limit: int = 5
) -> list[ReplyTargetSnapshot]:
    """Read the most recent ``limit`` snapshots oldest→newest."""
    rows = conn.execute(
        """
        SELECT checked_at_utc, computed_likes_per_hour, computed_replies_per_hour
          FROM reply_target_snapshots
         WHERE reply_target_id = ?
         ORDER BY checked_at_utc DESC
         LIMIT ?
        """,
        (int(reply_target_id), int(limit)),
    ).fetchall()
    return [
        ReplyTargetSnapshot(
            checked_at_utc=str(r["checked_at_utc"]),
            computed_likes_per_hour=(
                float(r["computed_likes_per_hour"])
                if r["computed_likes_per_hour"] is not None
                else None
            ),
            computed_replies_per_hour=(
                float(r["computed_replies_per_hour"])
                if r["computed_replies_per_hour"] is not None
                else None
            ),
        )
        for r in reversed(rows)
    ]


def _compute_rates(
    *,
    new_like_count: int,
    new_reply_count: int,
    previous_like_count: int | None,
    previous_reply_count: int | None,
    minutes_since_previous: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Return (likes/hour, replies/hour, velocity_delta).

    The first snapshot for a candidate has no previous baseline; all
    three return None. Once a prior snapshot exists the rate is
    ``(new - prev) / hours_elapsed``.
    """
    # RV2-15: clamp sub-minute deltas. Two consecutive snapshots
    # inserted within the same minute would produce a tiny positive
    # ``minutes_since_previous`` (e.g. 0.05) which we'd multiply by 60×
    # to per-hour, amplifying counter jitter into noise. The
    # `< _MIN_SAMPLE_MINUTES` floor ensures we only compute a rate when
    # at least a minute has elapsed; below that the rate is undefined.
    if (
        previous_like_count is None
        or previous_reply_count is None
        or minutes_since_previous is None
        or minutes_since_previous < _MIN_SAMPLE_MINUTES
    ):
        return None, None, None
    hours = minutes_since_previous / 60.0
    likes_per_hour = (new_like_count - previous_like_count) / hours
    replies_per_hour = (new_reply_count - previous_reply_count) / hours
    velocity_delta = likes_per_hour  # simple proxy; matches §29.3 likes-keyed rubric
    return likes_per_hour, replies_per_hour, velocity_delta


def _minutes_between_iso(earlier: str | None, later: str | None) -> float | None:
    """Return ``later - earlier`` in minutes, or None if either is unparseable.

    RV2-14: delegates to the named ``minutes_between_sqlite`` helper so
    the SQLite-shape assumption is explicit at the call site. The X API
    shape (RFC 3339 with T separator) has its own ``parse_x_api_datetime``
    helper in app/agent/timeparse.py — never mix the two.
    """
    from app.agent.timeparse import minutes_between_sqlite

    return minutes_between_sqlite(earlier, later)


def _process_one_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_row: sqlite3.Row,
    tweet: dict[str, Any],
    raw_response_id: int | None,
    settings_dict: dict[str, Any],
) -> None:
    """Apply one X API response to one ``reply_targets`` row.

    Inserts the snapshot, updates the denormalized counters + scores +
    recommended_action, persists velocity / timing.
    """
    rt_id = int(candidate_row["id"])
    public = tweet.get("public_metrics") or {}
    new_like = int(public.get("like_count") or 0)
    new_reply = int(public.get("reply_count") or 0)
    new_repost = int(public.get("retweet_count") or 0)
    new_quote = int(public.get("quote_count") or 0)
    new_bookmark = public.get("bookmark_count")
    new_impression = public.get("impression_count")

    # Compute differential rate against the most-recent previous snapshot.
    prev_row = conn.execute(
        """
        SELECT checked_at_utc, like_count, reply_count
          FROM reply_target_snapshots
         WHERE reply_target_id = ?
         ORDER BY checked_at_utc DESC
         LIMIT 1
        """,
        (rt_id,),
    ).fetchone()
    prev_like = prev_row["like_count"] if prev_row else None
    prev_reply = prev_row["reply_count"] if prev_row else None
    prev_at = prev_row["checked_at_utc"] if prev_row else None
    now_iso_row = conn.execute("SELECT datetime('now')").fetchone()
    now_iso = str(now_iso_row[0]) if now_iso_row else None
    minutes_since = _minutes_between_iso(prev_at, now_iso)

    likes_per_hour, replies_per_hour, velocity_delta = _compute_rates(
        new_like_count=new_like,
        new_reply_count=new_reply,
        previous_like_count=prev_like,
        previous_reply_count=prev_reply,
        minutes_since_previous=minutes_since,
    )

    conn.execute(
        """
        INSERT INTO reply_target_snapshots
          (reply_target_id, checked_at_utc, like_count, reply_count,
           repost_count, quote_count, bookmark_count, impression_count,
           computed_likes_per_hour, computed_replies_per_hour,
           computed_velocity_delta, raw_response_id)
        VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rt_id,
            new_like,
            new_reply,
            new_repost,
            new_quote,
            new_bookmark,
            new_impression,
            likes_per_hour,
            replies_per_hour,
            velocity_delta,
            raw_response_id,
        ),
    )

    # Recompute dimension scores from the fresh snapshot + history.
    medium_th, high_th = engagement_surface_thresholds(
        candidate_row["target_author_follower_count"], settings_dict
    )
    base_eng = engagement_surface_score(new_like, medium_th, high_th)
    base_sat = saturation_score(new_reply)
    snapshots = _load_snapshots(conn, rt_id, limit=5)
    vel = velocity_score(snapshots)
    # post_age_minutes: prefer the candidate's recorded created_at over now.
    age_minutes = candidate_row["post_age_minutes"]
    if age_minutes is None:
        # Fall back to computing from target_created_at_utc if present.
        # RV2-14: use the named X API parser — target_created_at_utc is
        # stored in RFC 3339 shape (T separator, Z suffix) per the
        # /2/tweets created_at field shape.
        from datetime import datetime, timezone

        from app.agent.timeparse import parse_x_api_datetime
        created_dt = parse_x_api_datetime(candidate_row["target_created_at_utc"])
        if created_dt is not None:
            age_minutes = int(
                (datetime.now(timezone.utc) - created_dt).total_seconds() / 60.0
            )
        else:
            age_minutes = 0
    tim = timing_score(int(age_minutes), candidate_row["target_author_follower_count"])

    # Base resolver runs first; modifiers apply post-hoc per §29.3.
    base_rel = candidate_row["relevance_score"]
    base_opp = candidate_row["reply_opportunity_score"]
    if base_rel is None or base_opp is None:
        # Candidate hasn't been scored on the Daniel-judged dimensions
        # yet — preserve the existing label; only update raw counters
        # and dimension scores that came from this refresh.
        conn.execute(
            """
            UPDATE reply_targets
               SET like_count = ?, reply_count = ?, repost_count = ?,
                   quote_count = ?, bookmark_count = ?, impression_count = ?,
                   engagement_surface_score = ?, saturation_score = ?,
                   velocity_score = ?, timing_score = ?,
                   post_age_minutes = ?,
                   last_checked_at_utc = datetime('now')
             WHERE id = ?
            """,
            (
                new_like, new_reply, new_repost, new_quote,
                new_bookmark, new_impression,
                base_eng, base_sat, vel, tim,
                int(age_minutes), rt_id,
            ),
        )
        return

    base_action = resolve_recommended_action(
        int(base_rel), int(base_eng), int(base_sat), int(base_opp)
    )
    adj_eng, adj_action = apply_velocity_timing_modifiers(
        base_engagement_surface=int(base_eng),
        base_recommended_action=base_action,
        velocity=vel,
        timing=tim,
    )

    conn.execute(
        """
        UPDATE reply_targets
           SET like_count = ?, reply_count = ?, repost_count = ?,
               quote_count = ?, bookmark_count = ?, impression_count = ?,
               engagement_surface_score = ?, saturation_score = ?,
               velocity_score = ?, timing_score = ?,
               recommended_action_label = ?, recommended_action_score = ?,
               post_age_minutes = ?,
               last_checked_at_utc = datetime('now')
         WHERE id = ?
        """,
        (
            new_like, new_reply, new_repost, new_quote,
            new_bookmark, new_impression,
            adj_eng, base_sat, vel, tim,
            adj_action, ACTION_TO_SCORE[adj_action],
            int(age_minutes), rt_id,
        ),
    )


def _detect_404_candidates(
    conn: sqlite3.Connection,
    *,
    expected_x_post_ids: set[str],
    returned_x_post_ids: set[str],
    api_errors: list[dict[str, Any]],
) -> set[str]:
    """Identify candidate IDs the X API returned 404 on.

    The /2/tweets?ids endpoint omits missing IDs from ``data`` and
    surfaces them via the ``errors`` array. We treat any expected ID
    that's absent from ``data`` AND mentioned in an "Not Found" error
    as deleted.
    """
    missing = expected_x_post_ids - returned_x_post_ids
    if not missing:
        return set()
    deleted: set[str] = set()
    for err in api_errors:
        if not isinstance(err, dict):
            continue
        title = (err.get("title") or "").lower()
        if "not found" not in title:
            continue
        # The error envelope's "value" or "resource_id" carries the ID.
        for key in ("value", "resource_id", "id"):
            v = err.get(key)
            if v and str(v) in missing:
                deleted.add(str(v))
    return deleted


def _transition_target_deleted(
    conn: sqlite3.Connection, x_post_id: str
) -> int:
    """UPDATE reply_targets to ``status='target_deleted'``. Returns the row id (0 if none)."""
    row = conn.execute(
        "SELECT id FROM reply_targets WHERE target_x_post_id = ? LIMIT 1",
        (x_post_id,),
    ).fetchone()
    if row is None:
        return 0
    rt_id = int(row[0])
    conn.execute(
        """
        UPDATE reply_targets
           SET status = 'target_deleted',
               last_checked_at_utc = datetime('now')
         WHERE id = ?
        """,
        (rt_id,),
    )
    return rt_id


def run(
    conn: sqlite3.Connection,
    *,
    batch_limit: int = _BATCH_LIMIT,
) -> dict[str, Any]:
    """Execute one sweep. Returns a summary dict."""
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "candidates_considered": 0,
        "candidates_refreshed": 0,
        "candidates_marked_deleted": 0,
        "rate_limit_hits": 0,
        "raw_response_ids": [],
        "skipped_reason": None,
        "error": None,
    }

    if _get_setting(conn, "data_collection_mode", default="api") == "manual":
        summary["skipped_reason"] = "data_collection_mode=manual"
        return summary

    stale_minutes = int(
        _get_setting(
            conn, "reply_target_metrics_refresh_interval_minutes", default=60
        )
        or 60
    )
    candidates = _select_stale_candidates(
        conn, limit=batch_limit, stale_minutes=stale_minutes
    )
    summary["candidates_considered"] = len(candidates)
    if not candidates:
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    by_x_id: dict[str, sqlite3.Row] = {}
    for c in candidates:
        x_id = str(c["target_x_post_id"] or "").strip()
        if x_id:
            by_x_id[x_id] = c
    expected_ids = set(by_x_id.keys())

    settings_dict: dict[str, Any] = {
        "engagement_surface_floor_likes": _get_setting(
            conn, "engagement_surface_floor_likes", default=15
        ),
        "engagement_surface_pct_of_author": _get_setting(
            conn, "engagement_surface_pct_of_author", default=0.001
        ),
        "engagement_surface_high_floor_likes": _get_setting(
            conn, "engagement_surface_high_floor_likes", default=50
        ),
        "engagement_surface_high_pct": _get_setting(
            conn, "engagement_surface_high_pct", default=0.005
        ),
    }

    try:
        responses = x_client.batch_request(
            _METRICS_ENDPOINT_TEMPLATE,
            list(expected_ids),
            conn=conn,
            batch_size=100,
            log_source="xurl",
        )
    except x_client.XApiRateLimited as rate:
        summary["rate_limit_hits"] = 1
        summary["error"] = f"rate-limited; retry_after={rate.retry_after_seconds}s"
        # last_checked_at_utc stays at its previous value for every candidate.
        return summary
    except x_client.XApiError as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    returned_ids: set[str] = set()
    api_errors_combined: list[dict[str, Any]] = []
    for resp in responses:
        summary["raw_response_ids"].append(resp.raw_response_id)
        body = resp.body if isinstance(resp.body, dict) else {}
        rows = body.get("data") if isinstance(body, dict) else None
        errs = body.get("errors") if isinstance(body, dict) else None
        if isinstance(errs, list):
            for e in errs:
                if isinstance(e, dict):
                    api_errors_combined.append(e)
        if not isinstance(rows, list):
            continue
        for tweet in rows:
            if not isinstance(tweet, dict):
                continue
            x_post_id = str(tweet.get("id") or "").strip()
            if not x_post_id or x_post_id not in by_x_id:
                continue
            returned_ids.add(x_post_id)
            try:
                _process_one_candidate(
                    conn,
                    candidate_row=by_x_id[x_post_id],
                    tweet=tweet,
                    raw_response_id=resp.raw_response_id,
                    settings_dict=settings_dict,
                )
                summary["candidates_refreshed"] += 1
            except sqlite3.IntegrityError as exc:
                _log.warning(
                    "reply_target_snapshots insert failed for %s: %s",
                    x_post_id,
                    exc,
                )

    # 404 detection — IDs we expected but didn't get back AND that the
    # API explicitly errored on.
    deleted_ids = _detect_404_candidates(
        conn,
        expected_x_post_ids=expected_ids,
        returned_x_post_ids=returned_ids,
        api_errors=api_errors_combined,
    )
    for x_id in deleted_ids:
        rt_id = _transition_target_deleted(conn, x_id)
        if rt_id:
            audit_log.log(
                conn,
                event_category="data",
                event_type="reply_target_marked_deleted",
                target_type="reply_target",
                target_id=str(rt_id),
                details={"target_x_post_id": x_id, "detected_via": "x_api_404"},
                success=True,
            )
            summary["candidates_marked_deleted"] += 1

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
        "--batch-limit",
        type=int,
        default=_BATCH_LIMIT,
        help="Candidates per sweep.",
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
            event_type="reply_target_metrics_refresh",
            target_type="job",
            target_id="reply_target_metrics_refresh",
            details=summary,
            success=success,
            error_message=summary.get("error"),
        )
        _log.info("reply_target_metrics_refresh summary: %s", summary)
        return 0 if success else 1
    finally:
        elapsed = round(time.perf_counter() - started, 3)
        _log.info(
            "reply_target_metrics_refresh completed in %ss", elapsed
        )
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
