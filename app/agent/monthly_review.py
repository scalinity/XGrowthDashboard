"""Monthly AI reviews — Phase 5.11 §28.27.

Cadence companion to ``weekly_reviews``. Same epistemic discipline
(counterfactual required, speculation blocks export, agent-drafted
sections emit ``<confidence>`` tags per §28.14), with month-granularity
auto-fill and additional `content_type` axis fields per §28.17.

New auto-filled fields vs. weekly:

* ``strongest_content_type`` / ``weakest_content_type`` per §28.17
  V/G/P/P axis.
* ``campaigns_completed_json`` — JSON array of campaigns that completed
  in this month with their success-criteria actuals.
* ``follower_delta`` over the month rather than the week.

Why not collapse weekly + monthly into one table: different cadences
imply different auto-fill semantics, different sample-size confidence
thresholds, different retro questions. A single ``cadence`` enum would
carry mode-aware logic in every consumer. Two tables, one shared UI
shell — see §28.27.
"""

from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from app.agent import audit_log as _audit_log
from app.db import transaction


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class MonthlyReviewError(RuntimeError):
    """Base for monthly_review module errors."""


class InvalidIsoMonthError(MonthlyReviewError):
    """Raised when iso_month isn't a YYYY-MM string."""


class ExportBlockedError(MonthlyReviewError):
    """Raised when an attempted export would violate the §28.27 rules."""


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def parse_iso_month(iso_month: str) -> tuple[date, date]:
    """Validate and parse ``YYYY-MM`` → (first_day, last_day) of that month."""
    if not isinstance(iso_month, str) or len(iso_month) != 7 or iso_month[4] != "-":
        raise InvalidIsoMonthError(
            f"iso_month must be YYYY-MM; got {iso_month!r}"
        )
    try:
        year = int(iso_month[:4])
        month = int(iso_month[5:7])
    except ValueError as exc:
        raise InvalidIsoMonthError(f"bad iso_month {iso_month!r}: {exc}") from exc
    if not 1 <= month <= 12:
        raise InvalidIsoMonthError(
            f"month must be 1-12; got {month} in {iso_month!r}"
        )
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def iso_month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ---------------------------------------------------------------------------
# Auto-fill computation (§28.27).
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AutoFilledFields:
    iso_month: str
    month_start_date: str
    month_end_date: str
    followers_start: int | None
    followers_end: int | None
    follower_delta: int | None
    posts_shipped: int
    replies_shipped: int
    reply_sessions_completed: int
    daily_reps_days_completed: int
    downloads: int
    qualified_icp_testers: int
    strongest_pillar_candidate: str | None
    weakest_pillar_candidate: str | None
    strongest_content_type: str | None
    weakest_content_type: str | None
    campaigns_completed_json: str


def compute_auto_filled_fields(
    conn: sqlite3.Connection, iso_month: str
) -> AutoFilledFields:
    """Compute the §28.27 month-granularity auto-fill payload.

    Mirrors the §14.6 weekly auto-fill helper, with month boundaries and
    the additional content-type axis fields. ``campaigns_completed_json``
    is a JSON-encoded list of dicts (one per campaign that completed in
    the month) carrying ``{campaign_id, name, success_criteria}``.
    """
    month_start, month_end = parse_iso_month(iso_month)

    # Follower delta over the month — same tolerant lookup as weekly.
    row = conn.execute(
        """
        SELECT
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date >= ? AND snapshot_date <= ?
              ORDER BY snapshot_date ASC LIMIT 1) AS followers_start,
            (SELECT followers_count FROM v_account_daily
              WHERE snapshot_date <= ? AND snapshot_date >= ?
              ORDER BY snapshot_date DESC LIMIT 1) AS followers_end
        """,
        (
            month_start.isoformat(),
            month_end.isoformat(),
            month_end.isoformat(),
            month_start.isoformat(),
        ),
    ).fetchone()
    followers_start = row["followers_start"] if row else None
    followers_end = row["followers_end"] if row else None
    follower_delta = (
        int(followers_end) - int(followers_start)
        if followers_start is not None and followers_end is not None
        else None
    )

    # Daily reps roll-up over the month.
    reps = conn.execute(
        """
        SELECT
            COALESCE(SUM(posts_shipped), 0)            AS posts_shipped,
            COALESCE(SUM(replies_shipped), 0)          AS replies_shipped,
            COALESCE(SUM(reply_sessions_completed), 0) AS reply_sessions_completed,
            COALESCE(SUM(minimum_reps_completed), 0)   AS daily_reps_days_completed
        FROM v_daily_reps
        WHERE activity_date BETWEEN ? AND ?
        """,
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()

    funnel = conn.execute(
        """
        SELECT
            COALESCE(SUM(downloads), 0)             AS downloads,
            COALESCE(SUM(qualified_icp_testers), 0) AS qualified_icp_testers
        FROM v_funnel_daily
        WHERE event_date BETWEEN ? AND ?
        """,
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()

    # Strongest / weakest pillar — ranked by median impressions among
    # rows whose confidence_label is moderate-or-above. Monthly window
    # needs more posts than weekly for "moderate," but we surface the
    # raw label so Daniel can read the confidence as-is.
    lanes = conn.execute(
        """
        SELECT pillar, median_impressions, confidence_label
        FROM v_lane_performance
        ORDER BY median_impressions DESC NULLS LAST
        """
    ).fetchall()
    eligible_lanes = [
        r
        for r in lanes
        if str(r["confidence_label"] or "").lower() not in {"insufficient", "scatter_only"}
    ]
    strongest_pillar = (
        f"`{eligible_lanes[0]['pillar']}` ({eligible_lanes[0]['confidence_label']})"
        if eligible_lanes
        else None
    )
    weakest_pillar = (
        f"`{eligible_lanes[-1]['pillar']}` ({eligible_lanes[-1]['confidence_label']})"
        if len(eligible_lanes) >= 2
        else None
    )

    # Strongest / weakest content_type — §28.17 V/G/P/P axis.
    strongest_ct, weakest_ct = _content_type_extremes(conn, month_start, month_end)

    # Campaigns that COMPLETED in this month — payload feeds the agent's
    # campaigns_retro section.
    #
    # P511R-4: SQLite's datetime('now') writes space-separated timestamps
    # ('2026-05-15 14:30:00'); the previous WHERE compared against
    # T-separated bounds ('2026-05-15T00:00:00'), and at position 10
    # ' ' (0x20) < 'T' (0x54) so campaigns completed on the FIRST of
    # the month silently dropped out of campaigns_completed_json.
    # Switch to date() on the LHS + date-only ISO params — format-agnostic.
    completed_rows = conn.execute(
        """
        SELECT id, name, success_criteria_json, completed_at_utc, lesson
        FROM campaigns
        WHERE status = 'completed'
          AND date(completed_at_utc) BETWEEN date(?) AND date(?)
        ORDER BY completed_at_utc ASC
        """,
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    campaigns_completed = []
    for r in completed_rows:
        try:
            sc = json.loads(r["success_criteria_json"] or "{}")
        except json.JSONDecodeError:
            sc = {}
        campaigns_completed.append(
            {
                "campaign_id": int(r["id"]),
                "name": r["name"],
                "completed_at_utc": r["completed_at_utc"],
                "lesson": r["lesson"],
                "success_criteria": sc,
            }
        )

    return AutoFilledFields(
        iso_month=iso_month,
        month_start_date=month_start.isoformat(),
        month_end_date=month_end.isoformat(),
        followers_start=followers_start,
        followers_end=followers_end,
        follower_delta=follower_delta,
        posts_shipped=int(reps["posts_shipped"]) if reps else 0,
        replies_shipped=int(reps["replies_shipped"]) if reps else 0,
        reply_sessions_completed=int(reps["reply_sessions_completed"]) if reps else 0,
        daily_reps_days_completed=int(reps["daily_reps_days_completed"]) if reps else 0,
        downloads=int(funnel["downloads"]) if funnel else 0,
        qualified_icp_testers=int(funnel["qualified_icp_testers"]) if funnel else 0,
        strongest_pillar_candidate=strongest_pillar,
        weakest_pillar_candidate=weakest_pillar,
        strongest_content_type=strongest_ct,
        weakest_content_type=weakest_ct,
        campaigns_completed_json=json.dumps(campaigns_completed),
    )


def _content_type_extremes(
    conn: sqlite3.Connection, month_start: date, month_end: date
) -> tuple[str | None, str | None]:
    """Strongest + weakest content_type by median engagement_rate in the month.

    Reads ``v_post_latest_metrics`` joined to ``posts.content_type``.
    Skipped when no posts in the window or content_type axis is empty.

    P511R-9: filter on ``date(p.published_to_x_at)`` — the post's
    PUBLISH date — rather than ``date(v.created_at_utc)`` which is
    the metric-snapshot insert date. A post published last month with
    a fresh metric snapshot taken this month was previously misattributed
    to this month's content-type axis. Mirrors the §28.17 lane-
    performance convention.
    """
    rows = conn.execute(
        """
        SELECT p.content_type, AVG(v.engagement_rate) AS avg_rate, COUNT(*) AS n
        FROM v_post_latest_metrics v
        JOIN posts p ON p.id = v.post_id
        WHERE p.content_type IS NOT NULL
          AND p.published_to_x_at IS NOT NULL
          AND date(p.published_to_x_at) BETWEEN date(?) AND date(?)
        GROUP BY p.content_type
        HAVING n >= 1
        ORDER BY avg_rate DESC NULLS LAST
        """,
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    if not rows:
        return None, None
    strongest = f"`{rows[0]['content_type']}` (n={rows[0]['n']})"
    weakest = (
        f"`{rows[-1]['content_type']}` (n={rows[-1]['n']})"
        if len(rows) >= 2
        else None
    )
    return strongest, weakest


# ---------------------------------------------------------------------------
# Upsert + read.
# ---------------------------------------------------------------------------
_USER_FIELDS: tuple[str, ...] = (
    "summary",
    "key_movements",
    "what_got_stuck",
    "best_post_id",
    "worst_post_id",
    "strongest_pillar",
    "weakest_pillar",
    "strongest_content_type",
    "weakest_content_type",
    "follower_delta",
    "stir_validation_summary",
    "campaigns_completed_json",
    "next_month_experiment",
    "counterfactual_note",
    "lesson",
    "confidence_label",
    "daniel_notes",
)


def upsert_monthly_review(
    conn: sqlite3.Connection,
    *,
    iso_month: str,
    fields: Mapping[str, Any],
) -> int:
    """Upsert a monthly_reviews row keyed on iso_month. Audit-logs the write.

    Only keys in ``_USER_FIELDS`` are honored; unknown keys are ignored
    so a stray UI bug can't write arbitrary columns. The
    ``confidence_label`` value is constrained at the schema level
    (CHECK on monthly_reviews); pass one of fact / inference /
    speculation / mixed / None.
    """
    parse_iso_month(iso_month)  # validates shape; raises otherwise
    payload = {k: fields[k] for k in _USER_FIELDS if k in fields}
    with transaction(conn):
        existing = conn.execute(
            "SELECT id FROM monthly_reviews WHERE iso_month = ?", (iso_month,)
        ).fetchone()
        if existing is None:
            cols = ["iso_month", *payload.keys()]
            placeholders = ", ".join("?" * len(cols))
            values = [iso_month, *payload.values()]
            cur = conn.execute(
                f"INSERT INTO monthly_reviews ({', '.join(cols)}) "
                f"VALUES ({placeholders}) RETURNING id",
                values,
            )
            row_id = int(cur.fetchone()[0])
            event: str | None = "monthly_review_created"
        else:
            row_id = int(existing["id"])
            if payload:
                set_clause = ", ".join(f"{k} = ?" for k in payload)
                conn.execute(
                    f"UPDATE monthly_reviews SET {set_clause} WHERE id = ?",
                    [*payload.values(), row_id],
                )
                event = "monthly_review_updated"
            else:
                # P511R-15: empty-payload update on an existing row is
                # a no-op — neither the UPDATE nor the audit row should
                # land. Mirrors the no-op suppression in set_setting.
                event = None
        if event is not None:
            _audit_log.log(
                conn,
                event_category="data",
                event_type=event,
                target_type="monthly_review",
                target_id=row_id,
                details={"iso_month": iso_month, "field_count": len(payload)},
            )
    return row_id


def get_monthly_review(
    conn: sqlite3.Connection, *, iso_month: str
) -> dict[str, Any] | None:
    parse_iso_month(iso_month)
    row = conn.execute(
        "SELECT * FROM monthly_reviews WHERE iso_month = ?", (iso_month,)
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# Export-blocker rule (§28.27 mirror of §14.6).
# ---------------------------------------------------------------------------
def export_blocked_reason(review: Mapping[str, Any] | None) -> str | None:
    """Return a one-line reason export is blocked, or None if it's clear.

    Two §14.6 rules carry over:
    1. counterfactual_note must be a non-empty string.
    2. confidence_label != 'speculation' (or the operator has
       acknowledged it; that acknowledgment lives in session_state, so
       this pure function reports the underlying state).
    """
    if review is None:
        return (
            "no monthly review row exists yet — fill the form first "
            "(§28.27 mirror of §14.6)."
        )
    cf = review.get("counterfactual_note")
    if not cf or not str(cf).strip():
        return (
            "counterfactual_note required — what couldn't this tool measure "
            "this month? (§28.27 mirror of §14.6)."
        )
    if str(review.get("confidence_label") or "").lower() == "speculation":
        return (
            "confidence_label is 'speculation' — acknowledge by editing the "
            "review or marking 'publishing speculation deliberately' (§28.14)."
        )
    return None


def assert_exportable(review: Mapping[str, Any] | None) -> None:
    """Raise :class:`ExportBlockedError` when the review fails the gate."""
    reason = export_blocked_reason(review)
    if reason:
        raise ExportBlockedError(reason)


__all__: Iterable[str] = (
    "AutoFilledFields",
    "ExportBlockedError",
    "InvalidIsoMonthError",
    "MonthlyReviewError",
    "assert_exportable",
    "compute_auto_filled_fields",
    "export_blocked_reason",
    "get_monthly_review",
    "iso_month_of",
    "parse_iso_month",
    "upsert_monthly_review",
)
