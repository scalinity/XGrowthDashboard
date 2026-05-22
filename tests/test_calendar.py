"""Tests for the §28.28 Content Calendar module.

Covers:
- ``get_calendar_window`` reads all four provenances correctly.
- AM/PM classification respects the ``calendar_am_cutoff_hour`` setting.
- Filters (pillar, content_type, campaign_id) compose with AND.
- ``get_active_campaigns_in_window`` returns campaigns whose date range
  overlaps the window AND have status planning/active (completed /
  abandoned excluded).
- Cell shape stays stable: provenance tag + source_id + date + slot.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app.agent import calendar as _cal
from app.agent import campaigns as _campaigns
from app.forms import set_setting


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _insert_post(
    conn: sqlite3.Connection,
    *,
    created_date: str,
    text: str = "hello",
    published_to_x_at: str | None = None,
    manual_confirmation_status: str = "confirmed",
    created_in_app_at: str | None = None,
    content_type: str | None = None,
) -> int:
    if created_in_app_at is None:
        created_in_app_at = f"{created_date}T10:00:00"
    # posts.content_type is NOT NULL DEFAULT 'unspecified' (migration
    # 012). Including it in the INSERT only when the caller asked,
    # otherwise letting the default win.
    if content_type is None:
        cur = conn.execute(
            """
            INSERT INTO posts
              (created_date, text, type, posted_via,
               manual_confirmation_status, created_in_app_at,
               published_to_x_at)
            VALUES (?, ?, 'standalone', 'manual', ?, ?, ?)
            RETURNING id
            """,
            (
                created_date,
                text,
                manual_confirmation_status,
                created_in_app_at,
                published_to_x_at,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO posts
              (created_date, text, type, posted_via,
               manual_confirmation_status, created_in_app_at,
               published_to_x_at, content_type)
            VALUES (?, ?, 'standalone', 'manual', ?, ?, ?, ?)
            RETURNING id
            """,
            (
                created_date,
                text,
                manual_confirmation_status,
                created_in_app_at,
                published_to_x_at,
                content_type,
            ),
        )
    return int(cur.fetchone()[0])


def _make_campaign(
    conn: sqlite3.Connection,
    *,
    name: str,
    start: str,
    end: str,
    pillar: str | None = None,
) -> int:
    return _campaigns.create_campaign(
        conn,
        name=name,
        theme=None,
        hypothesis=None,
        start_date=start,
        end_date=end,
        success_criteria={
            "distribution": [{"metric": "impressions", "target": "10000"}],
            "validation": [{"metric": "downloads", "target": "5"}],
        },
        pillar=pillar,
    )


# ---------------------------------------------------------------------------
# AM/PM classification.
# ---------------------------------------------------------------------------
def test_am_pm_classification_respects_cutoff_setting(
    db_conn: sqlite3.Connection,
) -> None:
    _insert_post(
        db_conn,
        created_date="2026-05-15",
        published_to_x_at="2026-05-15T09:00:00",
    )
    _insert_post(
        db_conn,
        created_date="2026-05-15",
        published_to_x_at="2026-05-15T15:00:00",
    )
    cells = _cal.get_calendar_window(
        db_conn, start_date="2026-05-15", end_date="2026-05-15"
    )
    slots = sorted(c.slot for c in cells)
    assert slots == ["am", "pm"]

    # Drop the cutoff to 8 → 09:00 now reads PM (09 >= 8), 15:00 stays
    # PM (15 >= 8). Setting is honored at query time, not cached.
    set_setting(db_conn, "calendar_am_cutoff_hour", 8)
    cells2 = _cal.get_calendar_window(
        db_conn, start_date="2026-05-15", end_date="2026-05-15"
    )
    slots2 = sorted(c.slot for c in cells2)
    assert slots2 == ["pm", "pm"]


# ---------------------------------------------------------------------------
# POSTED provenance.
# ---------------------------------------------------------------------------
def test_posted_cells_pick_up_published_posts(
    db_conn: sqlite3.Connection,
) -> None:
    post_id = _insert_post(
        db_conn,
        created_date="2026-05-15",
        text="shipped post",
        published_to_x_at="2026-05-15T10:00:00",
        content_type="value",
    )
    cells = _cal.get_calendar_window(
        db_conn, start_date="2026-05-01", end_date="2026-05-31"
    )
    posted = [c for c in cells if c.provenance == "posted"]
    assert len(posted) == 1
    assert posted[0].source_id == post_id
    assert posted[0].content_type == "value"
    assert posted[0].date == "2026-05-15"


def test_posted_cells_filter_by_content_type(
    db_conn: sqlite3.Connection,
) -> None:
    _insert_post(
        db_conn,
        created_date="2026-05-15",
        text="value post",
        published_to_x_at="2026-05-15T10:00:00",
        content_type="value",
    )
    _insert_post(
        db_conn,
        created_date="2026-05-16",
        text="growth post",
        published_to_x_at="2026-05-16T10:00:00",
        content_type="growth",
    )
    cells = _cal.get_calendar_window(
        db_conn,
        start_date="2026-05-01",
        end_date="2026-05-31",
        content_type="value",
    )
    assert len(cells) == 1
    assert cells[0].content_type == "value"


# ---------------------------------------------------------------------------
# DRAFTED-FOR-FUTURE provenance.
# ---------------------------------------------------------------------------
def test_drafted_for_future_picked_up(db_conn: sqlite3.Connection) -> None:
    draft_id = _insert_post(
        db_conn,
        created_date="2026-05-20",
        text="future draft",
        manual_confirmation_status="draft",
        created_in_app_at="2026-05-20T14:00:00",
    )
    cells = _cal.get_calendar_window(
        db_conn, start_date="2026-05-20", end_date="2026-05-20"
    )
    drafted = [c for c in cells if c.provenance == "drafted_for_future"]
    assert len(drafted) == 1
    assert drafted[0].source_id == draft_id
    assert drafted[0].slot == "pm"


# ---------------------------------------------------------------------------
# PLANNED (campaign_items).
# ---------------------------------------------------------------------------
def test_planned_campaign_items_appear_in_window(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(
        db_conn, name="p", start="2026-05-01", end="2026-05-28", pillar="build"
    )
    db_conn.execute(
        """
        INSERT INTO campaign_items
          (campaign_id, item_type, planned_for_date, status, planned_text, sort_order)
        VALUES (?, 'post', '2026-05-22', 'planned', 'planned post', 0)
        """,
        (cid,),
    )
    cells = _cal.get_calendar_window(
        db_conn, start_date="2026-05-22", end_date="2026-05-22"
    )
    planned = [c for c in cells if c.provenance == "planned"]
    assert len(planned) == 1
    assert planned[0].title == "planned post"
    assert planned[0].campaign_id == cid
    assert planned[0].pillar == "build"


def test_planned_items_outside_window_excluded(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(
        db_conn, name="p", start="2026-05-01", end="2026-06-28"
    )
    db_conn.execute(
        """
        INSERT INTO campaign_items
          (campaign_id, item_type, planned_for_date, status, sort_order)
        VALUES (?, 'post', '2026-06-15', 'planned', 0)
        """,
        (cid,),
    )
    cells = _cal.get_calendar_window(
        db_conn, start_date="2026-05-01", end_date="2026-05-31"
    )
    assert all(c.provenance != "planned" for c in cells)


def test_campaign_filter_narrows_planned_cells(
    db_conn: sqlite3.Connection,
) -> None:
    cid1 = _make_campaign(db_conn, name="a", start="2026-05-01", end="2026-05-28")
    cid2 = _make_campaign(db_conn, name="b", start="2026-05-01", end="2026-05-28")
    for cid in (cid1, cid2):
        db_conn.execute(
            """
            INSERT INTO campaign_items
              (campaign_id, item_type, planned_for_date, status, sort_order)
            VALUES (?, 'post', '2026-05-15', 'planned', 0)
            """,
            (cid,),
        )
    cells = _cal.get_calendar_window(
        db_conn,
        start_date="2026-05-01",
        end_date="2026-05-31",
        campaign_id=cid1,
    )
    planned = [c for c in cells if c.provenance == "planned"]
    assert {c.campaign_id for c in planned} == {cid1}


# ---------------------------------------------------------------------------
# Active campaigns strip.
# ---------------------------------------------------------------------------
def test_active_campaigns_overlap_with_window(
    db_conn: sqlite3.Connection,
) -> None:
    inside = _make_campaign(
        db_conn, name="inside", start="2026-05-01", end="2026-05-28"
    )
    earlier = _make_campaign(
        db_conn, name="earlier", start="2026-04-01", end="2026-04-15"
    )
    later = _make_campaign(
        db_conn, name="later", start="2026-07-01", end="2026-07-15"
    )
    rows = _cal.get_active_campaigns_in_window(
        db_conn, start_date="2026-05-01", end_date="2026-05-31"
    )
    ids = {r["id"] for r in rows}
    assert inside in ids
    assert earlier not in ids
    assert later not in ids


def test_active_campaigns_excludes_completed(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn, name="x", start="2026-05-01", end="2026-05-28")
    _campaigns.activate_campaign(db_conn, campaign_id=cid)
    _campaigns.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "1"}],
            "validation": [{"metric": "downloads", "actual": "1"}],
        },
        lesson="L",
        counterfactual_note="C",
    )
    rows = _cal.get_active_campaigns_in_window(
        db_conn, start_date="2026-05-01", end_date="2026-05-31"
    )
    assert cid not in {r["id"] for r in rows}


# ---------------------------------------------------------------------------
# Empty-window sanity.
# ---------------------------------------------------------------------------
def test_empty_window_returns_empty_list(db_conn: sqlite3.Connection) -> None:
    cells = _cal.get_calendar_window(
        db_conn, start_date="2030-01-01", end_date="2030-01-07"
    )
    assert cells == []


def test_window_accepts_date_objects(db_conn: sqlite3.Connection) -> None:
    # Both date and str work; uses tolerant ISO parsing internally.
    cells = _cal.get_calendar_window(
        db_conn, start_date=date(2030, 1, 1), end_date=date(2030, 1, 7)
    )
    assert cells == []


