"""Campaigns + campaign items — Phase 5.11 §28.26.

Multi-week themed pushes. A campaign carries a hypothesis + date
range + dual-stream success criteria + a set of items (planned,
drafted, shipped, skipped). Distinct from `experiments`
(hypothesis-only, no item planning) and from `weekly_reviews`
(retrospective, one week).

Schema discipline (load-bearing):

1. ``success_criteria_json`` MUST contain ≥1 distribution metric AND
   ≥1 validation metric. ``create_campaign`` raises
   :class:`InvalidSuccessCriteriaError` otherwise. This enforces §1's
   dual-stream discipline at campaign granularity — a "follower-
   focused campaign" with no validation lever is exactly what §1 was
   written to prevent.
2. A campaign cannot be ``completed`` without all success-criteria
   actuals + a lesson + a counterfactual_note.
   ``complete_campaign`` raises :class:`RetroIncompleteError` otherwise.

State machine (see §28.26):

    campaigns.status:
      planning → active        (when start_date <= today AND "Activate")
      active → completed       (when "Complete" + retro form filled)
      active → abandoned       (when "Abandon" + abandon_reason required)
      planning → abandoned     (planning a campaign you decide not to run is fine)

    campaign_items.status:
      planned → drafted        (when agent_draft_id is populated)
      planned → shipped        (manual; direct publish)
      drafted → shipped        (when linked posts.published_to_x_at populates)
      planned → skipped        (Daniel-decided)
      drafted → skipped        (Daniel-decided)

Every state transition and every campaign / item create lands one
``audit_logs`` row via :mod:`app.agent.audit_log` (§28.30 write-
through point).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from app.agent import audit_log as _audit_log
from app.db import transaction


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class CampaignError(RuntimeError):
    """Base for campaigns-module errors."""


class CampaignNotFoundError(CampaignError):
    """Raised when a campaign_id doesn't resolve."""


class CampaignItemNotFoundError(CampaignError):
    """Raised when a campaign_item_id doesn't resolve."""


class InvalidSuccessCriteriaError(CampaignError):
    """Raised when success_criteria_json lacks ≥1 distribution AND ≥1 validation metric."""


class InvalidDateRangeError(CampaignError):
    """Raised when start_date > end_date or either field can't be parsed."""


class InvalidStatusTransitionError(CampaignError):
    """Raised when a state machine move isn't permitted (§28.26)."""


class RetroIncompleteError(CampaignError):
    """Raised when ``complete_campaign`` is missing actuals, lesson, or counterfactual."""


# ---------------------------------------------------------------------------
# State machine reference.
# ---------------------------------------------------------------------------
VALID_STATUSES: frozenset[str] = frozenset(
    {"planning", "active", "completed", "abandoned"}
)

VALID_ITEM_STATUSES: frozenset[str] = frozenset(
    {"planned", "drafted", "shipped", "skipped"}
)

VALID_ITEM_TYPES: frozenset[str] = frozenset(
    {"post", "reply", "event", "milestone", "reminder"}
)

# (from_status, to_status) allowed transitions.
_CAMPAIGN_STATUS_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("planning", "active"),
        ("planning", "abandoned"),
        ("active", "completed"),
        ("active", "abandoned"),
    }
)

_ITEM_STATUS_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("planned", "drafted"),
        ("planned", "shipped"),
        ("planned", "skipped"),
        ("drafted", "shipped"),
        ("drafted", "skipped"),
    }
)


# ---------------------------------------------------------------------------
# Dataclasses for ergonomic returns.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Campaign:
    id: int
    name: str
    theme: str | None
    hypothesis: str | None
    start_date: str
    end_date: str
    status: str
    success_criteria: Mapping[str, Any]
    parent_experiment_id: int | None
    pillar: str | None
    content_type: str | None
    notes: str | None
    created_at_utc: str
    completed_at_utc: str | None
    abandon_reason: str | None
    lesson: str | None
    counterfactual_note: str | None


@dataclass(frozen=True, slots=True)
class CampaignItem:
    id: int
    campaign_id: int
    item_type: str
    planned_for_date: str | None
    post_id: int | None
    agent_draft_id: int | None
    reply_target_id: int | None
    planned_text: str | None
    status: str
    notes: str | None
    sort_order: int
    created_at_utc: str
    completed_at_utc: str | None


# ---------------------------------------------------------------------------
# Validation helpers.
# ---------------------------------------------------------------------------
def _validate_success_criteria(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce §28.26's dual-stream rule on success_criteria_json shape.

    Returns the normalized dict (with both lists guaranteed present).
    Raises :class:`InvalidSuccessCriteriaError` if either stream is
    missing or empty.
    """
    if not isinstance(payload, Mapping):
        raise InvalidSuccessCriteriaError(
            "success_criteria must be a mapping with 'distribution' and 'validation' keys."
        )
    distribution = payload.get("distribution")
    validation = payload.get("validation")
    if not isinstance(distribution, list) or not distribution:
        raise InvalidSuccessCriteriaError(
            "success_criteria.distribution must be a non-empty list "
            "of {metric, target, actual?} dicts (§28.26 dual-stream rule)."
        )
    if not isinstance(validation, list) or not validation:
        raise InvalidSuccessCriteriaError(
            "success_criteria.validation must be a non-empty list "
            "of {metric, target, actual?} dicts (§28.26 dual-stream rule)."
        )
    for stream_name, stream in (("distribution", distribution), ("validation", validation)):
        for entry in stream:
            if not isinstance(entry, Mapping):
                raise InvalidSuccessCriteriaError(
                    f"success_criteria.{stream_name} entries must be dicts; "
                    f"got {type(entry).__name__}."
                )
            if not entry.get("metric") or not entry.get("target"):
                raise InvalidSuccessCriteriaError(
                    f"success_criteria.{stream_name} entries require "
                    "non-empty 'metric' AND 'target' fields."
                )
    return {
        "distribution": [dict(e) for e in distribution],
        "validation": [dict(e) for e in validation],
    }


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidDateRangeError(f"bad date {value!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Create / read.
# ---------------------------------------------------------------------------
def create_campaign(
    conn: sqlite3.Connection,
    *,
    name: str,
    theme: str | None,
    hypothesis: str | None,
    start_date: str | date,
    end_date: str | date,
    success_criteria: Mapping[str, Any],
    parent_experiment_id: int | None = None,
    pillar: str | None = None,
    content_type: str | None = None,
    notes: str | None = None,
) -> int:
    """Insert a new campaign with dual-stream success-criteria validation.

    Raises :class:`InvalidSuccessCriteriaError` when the §28.26 rule is
    violated; :class:`InvalidDateRangeError` when the dates can't be
    parsed or end_date < start_date. Audit-logs a
    ``data/campaign_created`` row inside the same transaction.
    """
    if not name or not name.strip():
        raise CampaignError("campaign name is required.")
    sc_normalized = _validate_success_criteria(success_criteria)
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if end < start:
        raise InvalidDateRangeError(
            f"end_date {end.isoformat()} precedes start_date {start.isoformat()}."
        )

    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO campaigns
              (name, theme, hypothesis, start_date, end_date,
               status, success_criteria_json, parent_experiment_id,
               pillar, content_type, notes)
            VALUES (?, ?, ?, ?, ?, 'planning', ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                name.strip(),
                theme,
                hypothesis,
                start.isoformat(),
                end.isoformat(),
                json.dumps(sc_normalized),
                parent_experiment_id,
                pillar,
                content_type,
                notes,
            ),
        )
        new_id = int(cur.fetchone()[0])
        _audit_log.log(
            conn,
            event_category="data",
            event_type="campaign_created",
            target_type="campaign",
            target_id=new_id,
            details={
                "name": name.strip(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "pillar": pillar,
                "content_type": content_type,
            },
        )
    return new_id


def get_campaign(conn: sqlite3.Connection, *, campaign_id: int) -> Campaign:
    row = conn.execute(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise CampaignNotFoundError(f"campaign id={campaign_id} not found.")
    try:
        sc = json.loads(row["success_criteria_json"] or "{}")
    except json.JSONDecodeError:
        sc = {}
    return Campaign(
        id=int(row["id"]),
        name=row["name"],
        theme=row["theme"],
        hypothesis=row["hypothesis"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        status=row["status"],
        success_criteria=sc,
        parent_experiment_id=row["parent_experiment_id"],
        pillar=row["pillar"],
        content_type=row["content_type"],
        notes=row["notes"],
        created_at_utc=row["created_at_utc"],
        completed_at_utc=row["completed_at_utc"],
        abandon_reason=row["abandon_reason"],
        lesson=row["lesson"],
        counterfactual_note=row["counterfactual_note"],
    )


def list_campaigns(
    conn: sqlite3.Connection, *, status: str | None = None
) -> list[Campaign]:
    """Return campaigns. When ``status`` is given, filter to that status."""
    if status is not None:
        if status not in VALID_STATUSES:
            raise CampaignError(f"unknown status {status!r}")
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE status = ? ORDER BY start_date DESC, id DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY start_date DESC, id DESC"
        ).fetchall()
    return [get_campaign(conn, campaign_id=int(r["id"])) for r in rows]


# ---------------------------------------------------------------------------
# State transitions on campaign + items.
# ---------------------------------------------------------------------------
def activate_campaign(conn: sqlite3.Connection, *, campaign_id: int) -> None:
    """Move planning → active. Idempotent on already-active campaigns."""
    camp = get_campaign(conn, campaign_id=campaign_id)
    if camp.status == "active":
        return
    if (camp.status, "active") not in _CAMPAIGN_STATUS_TRANSITIONS:
        raise InvalidStatusTransitionError(
            f"cannot move campaign {campaign_id} from {camp.status!r} to 'active'."
        )
    with transaction(conn):
        conn.execute(
            "UPDATE campaigns SET status = 'active' WHERE id = ?", (campaign_id,)
        )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="campaign_activated",
            target_type="campaign",
            target_id=campaign_id,
            details={"from_status": camp.status, "to_status": "active"},
        )


def abandon_campaign(
    conn: sqlite3.Connection, *, campaign_id: int, reason: str
) -> None:
    """Move (planning|active) → abandoned. Reason is required."""
    if not reason or not reason.strip():
        raise CampaignError("abandon_reason is required.")
    camp = get_campaign(conn, campaign_id=campaign_id)
    if camp.status == "abandoned":
        return
    if (camp.status, "abandoned") not in _CAMPAIGN_STATUS_TRANSITIONS:
        raise InvalidStatusTransitionError(
            f"cannot move campaign {campaign_id} from {camp.status!r} to 'abandoned'."
        )
    with transaction(conn):
        conn.execute(
            """
            UPDATE campaigns
            SET status = 'abandoned',
                completed_at_utc = datetime('now'),
                abandon_reason = ?
            WHERE id = ?
            """,
            (reason.strip(), campaign_id),
        )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="campaign_abandoned",
            target_type="campaign",
            target_id=campaign_id,
            details={
                "from_status": camp.status,
                "reason": reason.strip(),
            },
        )


def complete_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    success_criteria_actuals: Mapping[str, Any],
    lesson: str,
    counterfactual_note: str,
    lesson_lands_in: str | None = None,
) -> None:
    """Move active → completed. Refuses unless actuals, lesson, and
    counterfactual_note are present (§28.26 retro discipline).

    ``success_criteria_actuals`` mirrors ``success_criteria`` shape but
    populates each entry's ``actual`` field. Missing entries raise.
    ``lesson_lands_in`` is informational only (free-text label like
    "weekly review 2026-05-25" or "monthly review 2026-05") that gets
    audit-logged so Daniel can trace where each retro lesson landed —
    the actual copy into the weekly/monthly review row is a Daniel-
    click in the §14.12 view, not an automatic write.
    """
    camp = get_campaign(conn, campaign_id=campaign_id)
    if (camp.status, "completed") not in _CAMPAIGN_STATUS_TRANSITIONS:
        raise InvalidStatusTransitionError(
            f"cannot move campaign {campaign_id} from {camp.status!r} to 'completed'."
        )
    if not lesson or not lesson.strip():
        raise RetroIncompleteError("lesson is required to complete a campaign.")
    if not counterfactual_note or not counterfactual_note.strip():
        raise RetroIncompleteError(
            "counterfactual_note is required to complete a campaign."
        )

    # Merge actuals into the stored criteria — every metric must carry
    # a non-null actual or completion is blocked.
    sc = dict(camp.success_criteria)
    if "distribution" not in sc or "validation" not in sc:
        raise RetroIncompleteError(
            "campaign has malformed success_criteria_json; cannot complete."
        )
    actuals_dist = success_criteria_actuals.get("distribution") or []
    actuals_val = success_criteria_actuals.get("validation") or []
    actuals_by_metric: dict[str, str] = {}
    for stream_actuals in (actuals_dist, actuals_val):
        for entry in stream_actuals:
            metric = (entry or {}).get("metric")
            actual = (entry or {}).get("actual")
            if metric and actual is not None and str(actual).strip():
                actuals_by_metric[str(metric)] = str(actual)

    missing: list[str] = []
    for stream in ("distribution", "validation"):
        for entry in sc[stream]:
            metric_name = entry.get("metric")
            if metric_name not in actuals_by_metric:
                missing.append(f"{stream}:{metric_name}")
            else:
                entry["actual"] = actuals_by_metric[metric_name]
    if missing:
        raise RetroIncompleteError(
            f"missing actuals for success criteria: {missing}"
        )

    with transaction(conn):
        conn.execute(
            """
            UPDATE campaigns
            SET status = 'completed',
                completed_at_utc = datetime('now'),
                success_criteria_json = ?,
                lesson = ?,
                counterfactual_note = ?
            WHERE id = ?
            """,
            (
                json.dumps(sc),
                lesson.strip(),
                counterfactual_note.strip(),
                campaign_id,
            ),
        )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="campaign_completed",
            target_type="campaign",
            target_id=campaign_id,
            details={
                "from_status": camp.status,
                "lesson_lands_in": lesson_lands_in,
                "actuals": actuals_by_metric,
            },
        )


# ---------------------------------------------------------------------------
# Campaign items.
# ---------------------------------------------------------------------------
def add_item(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    item_type: str,
    planned_for_date: str | date | None = None,
    post_id: int | None = None,
    agent_draft_id: int | None = None,
    reply_target_id: int | None = None,
    planned_text: str | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
) -> int:
    """Insert one campaign item. Returns the new id.

    When ``sort_order`` is None, the new row is appended to the end
    of the campaign's current item list (max(sort_order) + 1).
    """
    if item_type not in VALID_ITEM_TYPES:
        raise CampaignError(f"unknown item_type {item_type!r}")
    # Foreign-key check for the campaign — raises CampaignNotFoundError
    # rather than surfacing a SQLite IntegrityError on the INSERT path.
    get_campaign(conn, campaign_id=campaign_id)

    planned = None
    if planned_for_date is not None:
        planned = _parse_date(planned_for_date).isoformat()

    if sort_order is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM campaign_items WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        sort_order = int(row[0])

    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO campaign_items
              (campaign_id, item_type, planned_for_date, post_id,
               agent_draft_id, reply_target_id, planned_text, status,
               notes, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)
            RETURNING id
            """,
            (
                campaign_id,
                item_type,
                planned,
                post_id,
                agent_draft_id,
                reply_target_id,
                planned_text,
                notes,
                int(sort_order),
            ),
        )
        new_id = int(cur.fetchone()[0])
        _audit_log.log(
            conn,
            event_category="data",
            event_type="campaign_item_added",
            target_type="campaign_item",
            target_id=new_id,
            details={
                "campaign_id": campaign_id,
                "item_type": item_type,
                "planned_for_date": planned,
            },
        )
    return new_id


def transition_item_status(
    conn: sqlite3.Connection, *, item_id: int, new_status: str
) -> None:
    """Move an item through its state machine. Enforces §28.26 transitions.

    Sets ``completed_at_utc`` when moving to a terminal state
    (``shipped`` or ``skipped``).
    """
    if new_status not in VALID_ITEM_STATUSES:
        raise CampaignError(f"unknown item status {new_status!r}")
    row = conn.execute(
        "SELECT campaign_id, status FROM campaign_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise CampaignItemNotFoundError(f"campaign_item id={item_id} not found.")
    current = row["status"]
    if current == new_status:
        return  # idempotent
    if (current, new_status) not in _ITEM_STATUS_TRANSITIONS:
        raise InvalidStatusTransitionError(
            f"cannot move item {item_id} from {current!r} to {new_status!r}."
        )
    terminal = new_status in ("shipped", "skipped")
    with transaction(conn):
        if terminal:
            conn.execute(
                """
                UPDATE campaign_items
                SET status = ?, completed_at_utc = datetime('now')
                WHERE id = ?
                """,
                (new_status, item_id),
            )
        else:
            conn.execute(
                "UPDATE campaign_items SET status = ? WHERE id = ?",
                (new_status, item_id),
            )
        _audit_log.log(
            conn,
            event_category="data",
            event_type=f"campaign_item_status_{new_status}",
            target_type="campaign_item",
            target_id=item_id,
            details={
                "campaign_id": int(row["campaign_id"]),
                "from_status": current,
                "to_status": new_status,
            },
        )


def list_items(
    conn: sqlite3.Connection, *, campaign_id: int
) -> list[CampaignItem]:
    rows = conn.execute(
        "SELECT * FROM campaign_items WHERE campaign_id = ? ORDER BY sort_order, id",
        (campaign_id,),
    ).fetchall()
    return [
        CampaignItem(
            id=int(r["id"]),
            campaign_id=int(r["campaign_id"]),
            item_type=r["item_type"],
            planned_for_date=r["planned_for_date"],
            post_id=r["post_id"],
            agent_draft_id=r["agent_draft_id"],
            reply_target_id=r["reply_target_id"],
            planned_text=r["planned_text"],
            status=r["status"],
            notes=r["notes"],
            sort_order=int(r["sort_order"]),
            created_at_utc=r["created_at_utc"],
            completed_at_utc=r["completed_at_utc"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Analysis (tool #21 `analyze_campaign_progress`).
# ---------------------------------------------------------------------------
def analyze_progress(
    conn: sqlite3.Connection, *, campaign_id: int
) -> dict[str, Any]:
    """Read-only structured progress payload for the agent / UI.

    Returns the §28.26 shape: campaign info + days_remaining +
    progress counters + linked-posts summary + success-criteria
    progress + a structured interpretation hook (the LLM's
    interpretation is appended by the agent tool wrapper, not here).
    """
    camp = get_campaign(conn, campaign_id=campaign_id)
    progress_row = conn.execute(
        """
        SELECT items_total, items_planned, items_drafted, items_shipped,
               items_skipped, percent_shipped, percent_planned_shipped,
               days_until_end, latest_shipped_post_id, latest_shipped_at_utc
        FROM v_campaign_progress WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchone()

    progress: dict[str, Any] = {
        "shipped": 0,
        "planned": 0,
        "drafted": 0,
        "skipped": 0,
        "percent_shipped": None,
    }
    days_remaining: int | None = None
    latest_shipped_post_id: int | None = None
    if progress_row is not None:
        progress = {
            "shipped": int(progress_row["items_shipped"] or 0),
            "planned": int(progress_row["items_planned"] or 0),
            "drafted": int(progress_row["items_drafted"] or 0),
            "skipped": int(progress_row["items_skipped"] or 0),
            "percent_shipped": progress_row["percent_shipped"],
        }
        days_remaining = (
            int(progress_row["days_until_end"])
            if progress_row["days_until_end"] is not None
            else None
        )
        latest_shipped_post_id = progress_row["latest_shipped_post_id"]

    # Linked-posts summary — aggregate impressions + median engagement
    # rate via v_post_latest_metrics. Read-only join; if any item has
    # no linked post yet, it contributes nothing.
    linked_summary: dict[str, Any] = {
        "impressions_total": 0,
        "engagement_rate_median": None,
        "by_pillar": {},
        "by_content_type": {},
    }
    summary_rows = conn.execute(
        """
        SELECT v.pillar, p.content_type, v.impressions, v.engagement_rate
        FROM campaign_items ci
        JOIN posts p ON p.id = ci.post_id
        LEFT JOIN v_post_latest_metrics v ON v.post_id = p.id
        WHERE ci.campaign_id = ? AND ci.post_id IS NOT NULL
        """,
        (campaign_id,),
    ).fetchall()
    if summary_rows:
        impressions_total = sum(int(r["impressions"] or 0) for r in summary_rows)
        rates = [
            float(r["engagement_rate"])
            for r in summary_rows
            if r["engagement_rate"] is not None
        ]
        median = None
        if rates:
            sorted_rates = sorted(rates)
            mid = len(sorted_rates) // 2
            if len(sorted_rates) % 2:
                median = sorted_rates[mid]
            else:
                median = (sorted_rates[mid - 1] + sorted_rates[mid]) / 2
        by_pillar: dict[str, int] = {}
        by_ct: dict[str, int] = {}
        for r in summary_rows:
            if r["pillar"]:
                by_pillar[r["pillar"]] = by_pillar.get(r["pillar"], 0) + 1
            if r["content_type"]:
                by_ct[r["content_type"]] = by_ct.get(r["content_type"], 0) + 1
        linked_summary = {
            "impressions_total": impressions_total,
            "engagement_rate_median": median,
            "by_pillar": by_pillar,
            "by_content_type": by_ct,
        }

    # Per-criterion progress — read the stored shape and surface a
    # simple on_track flag when both target + actual are populated.
    sc_progress: list[dict[str, Any]] = []
    for stream in ("distribution", "validation"):
        for entry in camp.success_criteria.get(stream, []):
            on_track: bool | None = None
            metric = entry.get("metric")
            target = entry.get("target")
            actual = entry.get("actual")
            if actual is not None:
                # Numeric comparison when both parse as floats; otherwise
                # leave on_track None and let the UI/LLM interpret.
                try:
                    on_track = float(actual) >= float(target)
                except (TypeError, ValueError):
                    on_track = None
            sc_progress.append(
                {
                    "stream": stream,
                    "metric": metric,
                    "target": target,
                    "current_actual": actual,
                    "on_track": on_track,
                }
            )

    return {
        "campaign_id": camp.id,
        "name": camp.name,
        "hypothesis": camp.hypothesis,
        "status": camp.status,
        "start_date": camp.start_date,
        "end_date": camp.end_date,
        "days_remaining": days_remaining,
        "pillar": camp.pillar,
        "content_type": camp.content_type,
        "progress": progress,
        "latest_shipped_post_id": latest_shipped_post_id,
        "linked_posts_summary": linked_summary,
        "success_criteria_progress": sc_progress,
    }


__all__: Iterable[str] = (
    "Campaign",
    "CampaignError",
    "CampaignItem",
    "CampaignItemNotFoundError",
    "CampaignNotFoundError",
    "InvalidDateRangeError",
    "InvalidStatusTransitionError",
    "InvalidSuccessCriteriaError",
    "RetroIncompleteError",
    "VALID_ITEM_STATUSES",
    "VALID_ITEM_TYPES",
    "VALID_STATUSES",
    "abandon_campaign",
    "activate_campaign",
    "add_item",
    "analyze_progress",
    "complete_campaign",
    "create_campaign",
    "get_campaign",
    "list_campaigns",
    "list_items",
    "transition_item_status",
)
