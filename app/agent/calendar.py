"""Content Calendar — Phase 5.11 §28.28.

Visual planning grid that aggregates four provenances:

1. ``posts.published_to_x_at`` populated → POSTED.
2. ``posts.manual_confirmation_status = 'draft'`` AND
   ``created_in_app_at`` in the future → DRAFTED-FOR-FUTURE (pairs with
   §19 item 11 scheduled drafts).
3. ``agent_drafts.status IN ('proposed','accepted_with_edits')`` AND
   no linked ``posts`` row → AGENT-DRAFTED.
4. ``campaign_items.status = 'planned'`` AND non-NULL
   ``planned_for_date`` → PLANNED.

AM/PM split: a slot is AM when its time-of-day < ``calendar_am_cutoff_hour``
(default 12 local time), PM otherwise. Planned items without a time-of-
day default to PM unless Daniel overrides via the row's ``notes`` /
manual override (out of scope for MVP; the schema is forward-compatible).

The module is read-only. The "+ schedule slot" Daniel-action lives in
the §14.11 page and routes through either :func:`add_item` on the
campaigns module (campaign-scoped path) or the existing post-draft
form (ad-hoc path); both write-through to ``audit_logs`` already.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal


Provenance = Literal["posted", "drafted_for_future", "agent_drafted", "planned"]
Slot = Literal["am", "pm"]


@dataclass(frozen=True, slots=True)
class CalendarCell:
    """One renderable cell on the calendar.

    ``source_id`` is the primary key of the originating table (``posts``
    for the first two provenances, ``agent_drafts`` for the third,
    ``campaign_items`` for the fourth). The view layer uses
    ``provenance`` to dispatch click-through to the correct detail page.
    """

    provenance: Provenance
    source_id: int
    date: str  # ISO-8601 date
    slot: Slot
    pillar: str | None
    content_type: str | None
    title: str
    campaign_id: int | None


def _am_cutoff_hour(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'calendar_am_cutoff_hour'"
    ).fetchone()
    if row is None:
        return 12
    try:
        return int(json.loads(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 12


def _classify_slot(timestamp: str | None, cutoff_hour: int) -> Slot:
    """Map an ISO-8601 datetime → 'am' / 'pm' per ``cutoff_hour``.

    Tolerates BOTH formats the project actually writes:
    - Python ``datetime.isoformat()`` → ``"2026-05-15T10:30:00"`` (T separator).
    - SQLite ``datetime('now')`` and ``publish.py::_utcnow_iso()`` →
      ``"2026-05-15 10:30:00"`` (space separator).
    Splitting only on "T" silently bucketed every space-separated row as PM
    (the IndexError fallback) in production — P511R-1.
    """
    if timestamp is None:
        return "pm"
    sep = "T" if "T" in timestamp else " "
    try:
        hour_part = timestamp.split(sep, 1)[1]
        hour = int(hour_part.split(":", 1)[0])
    except (IndexError, ValueError):
        return "pm"
    return "am" if hour < cutoff_hour else "pm"


def _date_only(timestamp: str | None) -> str:
    """Return the ``YYYY-MM-DD`` prefix of either ISO format.

    P511R-1: the first 10 characters of both ``"2026-05-15T10:30:00"`` and
    ``"2026-05-15 10:30:00"`` are the date; splitting on "T" left the whole
    string on space-separated rows, so the cell's ``date`` field never
    matched ``day.isoformat()`` in the page's grid lookup. Result: silently
    empty calendar for every shipped post in production.
    """
    if not timestamp:
        return ""
    return str(timestamp)[:10]


def _short(text: str | None, *, n: int = 60) -> str:
    if not text:
        return "(no title)"
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Main query.
# ---------------------------------------------------------------------------
def get_calendar_window(
    conn: sqlite3.Connection,
    *,
    start_date: str | date,
    end_date: str | date,
    pillar: str | None = None,
    content_type: str | None = None,
    campaign_id: int | None = None,
) -> list[CalendarCell]:
    """Return every CalendarCell whose date falls in [start_date, end_date].

    Filters compose with AND. ``pillar`` matches against the latest
    classification (POSTED rows) or directly against
    ``agent_drafts.pillar`` (AGENT-DRAFTED rows). ``content_type`` is
    matched on the respective tables' content_type columns.
    ``campaign_id`` filters POSTED / AGENT-DRAFTED rows by their
    campaign_items linkage, and PLANNED rows directly.
    """
    if isinstance(start_date, date):
        start_iso = start_date.isoformat()
    else:
        start_iso = start_date
    if isinstance(end_date, date):
        end_iso = end_date.isoformat()
    else:
        end_iso = end_date

    cutoff = _am_cutoff_hour(conn)
    cells: list[CalendarCell] = []

    # ----- 1. POSTED -----
    # P511R-16 + P511R-17: consolidate the three near-identical
    # correlated-subquery + Python-side filter patterns. Each provenance
    # now does a LEFT JOIN on campaign_items + optional SQL-side
    # campaign_id filter, replacing the per-row Python `continue` skip.
    # Reduces drift hazard (the linkage rule lives in ONE place per
    # provenance) and aligns with the PLANNED branch which was already
    # SQL-filtered.
    posted_sql = """
        SELECT p.id, p.text, p.content_type, p.published_to_x_at,
               v.pillar AS metric_pillar,
               ci.campaign_id AS campaign_id
        FROM posts p
        LEFT JOIN v_post_latest_metrics v ON v.post_id = p.id
        LEFT JOIN campaign_items ci ON ci.post_id = p.id
        WHERE p.published_to_x_at IS NOT NULL
          AND date(p.published_to_x_at) BETWEEN ? AND ?
    """
    posted_params: list[object] = [start_iso, end_iso]
    if pillar is not None:
        posted_sql += " AND v.pillar = ?"
        posted_params.append(pillar)
    if content_type is not None:
        posted_sql += " AND p.content_type = ?"
        posted_params.append(content_type)
    if campaign_id is not None:
        posted_sql += " AND ci.campaign_id = ?"
        posted_params.append(int(campaign_id))
    for r in conn.execute(posted_sql, posted_params):
        cells.append(
            CalendarCell(
                provenance="posted",
                source_id=int(r["id"]),
                date=_date_only(r["published_to_x_at"]),
                slot=_classify_slot(r["published_to_x_at"], cutoff),
                pillar=r["metric_pillar"],
                content_type=r["content_type"],
                title=_short(r["text"]),
                campaign_id=r["campaign_id"],
            )
        )

    # ----- 2. DRAFTED-FOR-FUTURE -----
    # Future-dated manual draft posts. We treat any draft (regardless of
    # date) that hasn't been published yet as a future-schedule signal
    # when its created_in_app_at falls in the window. The view's "+
    # schedule slot" inline form lands rows here when Daniel picks the
    # ad-hoc path.
    #
    # Pillar lives on post_classifications joined via v_post_latest_metrics
    # which requires a published row; future-drafts have NULL pillar, so
    # when a pillar filter is requested we short-circuit to an empty result.
    if pillar is None:
        drafted_sql = """
            SELECT p.id, p.text, p.content_type, p.created_in_app_at,
                   ci.campaign_id AS campaign_id
            FROM posts p
            LEFT JOIN campaign_items ci ON ci.post_id = p.id
            WHERE p.manual_confirmation_status = 'draft'
              AND p.published_to_x_at IS NULL
              AND date(p.created_in_app_at) BETWEEN ? AND ?
        """
        drafted_params: list[object] = [start_iso, end_iso]
        if content_type is not None:
            drafted_sql += " AND p.content_type = ?"
            drafted_params.append(content_type)
        if campaign_id is not None:
            drafted_sql += " AND ci.campaign_id = ?"
            drafted_params.append(int(campaign_id))
        for r in conn.execute(drafted_sql, drafted_params):
            cells.append(
                CalendarCell(
                    provenance="drafted_for_future",
                    source_id=int(r["id"]),
                    date=_date_only(r["created_in_app_at"]),
                    slot=_classify_slot(r["created_in_app_at"], cutoff),
                    pillar=None,
                    content_type=r["content_type"],
                    title=_short(r["text"]),
                    campaign_id=r["campaign_id"],
                )
            )

    # ----- 3. AGENT-DRAFTED -----
    agent_sql = """
        SELECT ad.id, ad.text, ad.content_type, ad.pillar, ad.created_at,
               ci.campaign_id AS campaign_id
        FROM agent_drafts ad
        LEFT JOIN campaign_items ci ON ci.agent_draft_id = ad.id
        WHERE ad.status IN ('proposed', 'accepted_with_edits')
          AND ad.final_post_id IS NULL
          AND date(ad.created_at) BETWEEN ? AND ?
    """
    agent_params: list[object] = [start_iso, end_iso]
    if pillar is not None:
        agent_sql += " AND ad.pillar = ?"
        agent_params.append(pillar)
    if content_type is not None:
        agent_sql += " AND ad.content_type = ?"
        agent_params.append(content_type)
    if campaign_id is not None:
        agent_sql += " AND ci.campaign_id = ?"
        agent_params.append(int(campaign_id))
    for r in conn.execute(agent_sql, agent_params):
        cells.append(
            CalendarCell(
                provenance="agent_drafted",
                source_id=int(r["id"]),
                date=_date_only(r["created_at"]),
                slot=_classify_slot(r["created_at"], cutoff),
                pillar=r["pillar"],
                content_type=r["content_type"],
                title=_short(r["text"]),
                campaign_id=r["campaign_id"],
            )
        )

    # ----- 4. PLANNED (campaign_items) -----
    planned_sql = """
        SELECT ci.id, ci.planned_for_date, ci.planned_text, ci.item_type,
               ci.campaign_id, c.pillar AS campaign_pillar,
               c.content_type AS campaign_content_type
        FROM campaign_items ci
        JOIN campaigns c ON c.id = ci.campaign_id
        WHERE ci.status = 'planned'
          AND ci.planned_for_date IS NOT NULL
          AND ci.planned_for_date BETWEEN ? AND ?
    """
    planned_params: list[object] = [start_iso, end_iso]
    if pillar is not None:
        planned_sql += " AND c.pillar = ?"
        planned_params.append(pillar)
    if content_type is not None:
        planned_sql += " AND c.content_type = ?"
        planned_params.append(content_type)
    if campaign_id is not None:
        planned_sql += " AND ci.campaign_id = ?"
        planned_params.append(int(campaign_id))
    for r in conn.execute(planned_sql, planned_params):
        cells.append(
            CalendarCell(
                provenance="planned",
                source_id=int(r["id"]),
                date=str(r["planned_for_date"]),
                slot="pm",  # planned default; future override hook elsewhere
                pillar=r["campaign_pillar"],
                content_type=r["campaign_content_type"],
                title=_short(r["planned_text"]) if r["planned_text"]
                else f"{r['item_type']} (planned)",
                campaign_id=int(r["campaign_id"]),
            )
        )

    return cells


def get_active_campaigns_in_window(
    conn: sqlite3.Connection,
    *,
    start_date: str | date,
    end_date: str | date,
) -> list[dict[str, object]]:
    """Return campaigns whose [start_date, end_date] overlaps the window.

    Used by §14.11's "Active campaigns running through this window" strip.
    """
    if isinstance(start_date, date):
        start_iso = start_date.isoformat()
    else:
        start_iso = start_date
    if isinstance(end_date, date):
        end_iso = end_date.isoformat()
    else:
        end_iso = end_date
    rows = conn.execute(
        """
        SELECT id, name, start_date, end_date, status,
               (SELECT items_shipped FROM v_campaign_progress
                WHERE campaign_id = campaigns.id) AS items_shipped,
               (SELECT items_planned FROM v_campaign_progress
                WHERE campaign_id = campaigns.id) AS items_planned
        FROM campaigns
        WHERE status IN ('planning', 'active')
          AND date(start_date) <= ?
          AND date(end_date) >= ?
        ORDER BY start_date
        """,
        (end_iso, start_iso),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "status": r["status"],
            "items_shipped": int(r["items_shipped"] or 0),
            "items_planned": int(r["items_planned"] or 0),
        }
        for r in rows
    ]


__all__: Iterable[str] = (
    "CalendarCell",
    "Provenance",
    "Slot",
    "get_active_campaigns_in_window",
    "get_calendar_window",
)
