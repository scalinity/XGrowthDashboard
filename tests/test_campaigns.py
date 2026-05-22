"""Tests for the §28.26 Campaigns module.

Load-bearing invariants tested here:

- ``create_campaign`` rejects a single-stream success_criteria
  (distribution-only or validation-only) — §1 dual-stream rule.
- ``complete_campaign`` refuses when actuals, lesson, or
  counterfactual_note are missing — §28.26 retro discipline.
- Item state transitions follow §28.26 rules (no shipped→drafted etc.).
- ``v_campaign_progress`` math matches what ``analyze_progress`` returns.
- Every create / transition lands an audit_logs row (write-through).
- Agent tool #21 ``analyze_campaign_progress`` returns a dict with the
  documented shape.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from app.agent import audit_log as _audit_log
from app.agent import campaigns as _c
from app.agent.tools import get_tool


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _dual_stream_criteria() -> dict[str, list[dict[str, str]]]:
    return {
        "distribution": [{"metric": "impressions", "target": "10000"}],
        "validation": [{"metric": "downloads", "target": "5"}],
    }


def _make_campaign(conn: sqlite3.Connection, *, name: str = "p") -> int:
    return _c.create_campaign(
        conn,
        name=name,
        theme="t",
        hypothesis="h",
        start_date=date.today().isoformat(),
        end_date=(date.today() + timedelta(days=14)).isoformat(),
        success_criteria=_dual_stream_criteria(),
    )


# ---------------------------------------------------------------------------
# create_campaign — dual-stream rule (§28.26 load-bearing).
# ---------------------------------------------------------------------------
def test_create_campaign_rejects_distribution_only_criteria(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_c.InvalidSuccessCriteriaError, match="validation"):
        _c.create_campaign(
            db_conn,
            name="dist-only",
            theme="t",
            hypothesis="h",
            start_date="2026-05-01",
            end_date="2026-05-28",
            success_criteria={
                "distribution": [{"metric": "impressions", "target": "10000"}],
                "validation": [],
            },
        )


def test_create_campaign_rejects_validation_only_criteria(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_c.InvalidSuccessCriteriaError, match="distribution"):
        _c.create_campaign(
            db_conn,
            name="val-only",
            theme="t",
            hypothesis="h",
            start_date="2026-05-01",
            end_date="2026-05-28",
            success_criteria={
                "distribution": [],
                "validation": [{"metric": "downloads", "target": "5"}],
            },
        )


def test_create_campaign_rejects_missing_metric_or_target(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_c.InvalidSuccessCriteriaError, match="metric"):
        _c.create_campaign(
            db_conn,
            name="bad",
            theme="t",
            hypothesis="h",
            start_date="2026-05-01",
            end_date="2026-05-28",
            success_criteria={
                "distribution": [{"metric": "", "target": "10000"}],
                "validation": [{"metric": "downloads", "target": "5"}],
            },
        )


def test_create_campaign_rejects_end_before_start(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_c.InvalidDateRangeError):
        _c.create_campaign(
            db_conn,
            name="inverted",
            theme="t",
            hypothesis="h",
            start_date="2026-06-01",
            end_date="2026-05-01",
            success_criteria=_dual_stream_criteria(),
        )


def test_create_campaign_persists_dual_stream_criteria(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    camp = _c.get_campaign(db_conn, campaign_id=cid)
    assert camp.status == "planning"
    assert camp.success_criteria["distribution"][0]["metric"] == "impressions"
    assert camp.success_criteria["validation"][0]["metric"] == "downloads"


def test_create_campaign_emits_audit_row(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    rows = _audit_log.query(db_conn, target_type="campaign", target_id=cid)
    assert any(r.event_type == "campaign_created" for r in rows)


# ---------------------------------------------------------------------------
# State machine — campaign-level.
# ---------------------------------------------------------------------------
def test_activate_then_complete_campaign(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    assert _c.get_campaign(db_conn, campaign_id=cid).status == "active"

    _c.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "12345"}],
            "validation": [{"metric": "downloads", "actual": "6"}],
        },
        lesson="explicit asks reliably moved downloads.",
        counterfactual_note="cohort effects + Stir launch could explain part of it.",
        lesson_lands_in="weekly review 2026-05-25",
    )
    camp = _c.get_campaign(db_conn, campaign_id=cid)
    assert camp.status == "completed"
    assert camp.lesson is not None
    assert camp.counterfactual_note is not None


def test_complete_campaign_blocks_without_lesson(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    with pytest.raises(_c.RetroIncompleteError, match="lesson"):
        _c.complete_campaign(
            db_conn,
            campaign_id=cid,
            success_criteria_actuals={
                "distribution": [{"metric": "impressions", "actual": "1"}],
                "validation": [{"metric": "downloads", "actual": "1"}],
            },
            lesson="",
            counterfactual_note="something",
        )


def test_complete_campaign_blocks_without_counterfactual(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    with pytest.raises(_c.RetroIncompleteError, match="counterfactual"):
        _c.complete_campaign(
            db_conn,
            campaign_id=cid,
            success_criteria_actuals={
                "distribution": [{"metric": "impressions", "actual": "1"}],
                "validation": [{"metric": "downloads", "actual": "1"}],
            },
            lesson="lesson",
            counterfactual_note="",
        )


def test_complete_campaign_blocks_missing_actuals(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    with pytest.raises(_c.RetroIncompleteError, match="missing actuals"):
        _c.complete_campaign(
            db_conn,
            campaign_id=cid,
            success_criteria_actuals={
                "distribution": [{"metric": "impressions", "actual": "1"}],
                # validation actuals missing
                "validation": [],
            },
            lesson="l",
            counterfactual_note="c",
        )


def test_planning_can_skip_active_to_abandoned(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.abandon_campaign(db_conn, campaign_id=cid, reason="lost interest")
    camp = _c.get_campaign(db_conn, campaign_id=cid)
    assert camp.status == "abandoned"
    assert camp.abandon_reason == "lost interest"


def test_completed_cannot_be_reactivated(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    _c.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "1"}],
            "validation": [{"metric": "downloads", "actual": "1"}],
        },
        lesson="x",
        counterfactual_note="y",
    )
    with pytest.raises(_c.InvalidStatusTransitionError):
        _c.activate_campaign(db_conn, campaign_id=cid)


# ---------------------------------------------------------------------------
# Items + state machine.
# ---------------------------------------------------------------------------
def test_add_item_then_transition_through_states(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    item_id = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    _c.transition_item_status(db_conn, item_id=item_id, new_status="drafted")
    _c.transition_item_status(db_conn, item_id=item_id, new_status="shipped")
    items = _c.list_items(db_conn, campaign_id=cid)
    assert items[0].status == "shipped"
    assert items[0].completed_at_utc is not None


def test_invalid_item_transition_blocked(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    item_id = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    _c.transition_item_status(db_conn, item_id=item_id, new_status="shipped")
    # shipped → planned is not in the state machine.
    with pytest.raises(_c.InvalidStatusTransitionError):
        _c.transition_item_status(db_conn, item_id=item_id, new_status="planned")


def test_add_item_appends_sort_order_in_sequence(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    a = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    b = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    c = _c.add_item(db_conn, campaign_id=cid, item_type="reply")
    items = _c.list_items(db_conn, campaign_id=cid)
    assert [it.id for it in items] == [a, b, c]
    assert [it.sort_order for it in items] == [0, 1, 2]


def test_add_item_unknown_campaign_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(_c.CampaignNotFoundError):
        _c.add_item(db_conn, campaign_id=999999, item_type="post")


def test_add_item_unknown_type_raises(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    with pytest.raises(_c.CampaignError, match="item_type"):
        _c.add_item(db_conn, campaign_id=cid, item_type="podcast")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# analyze_progress + v_campaign_progress agreement.
# ---------------------------------------------------------------------------
def test_analyze_progress_reflects_two_thirds_shipped(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    a = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    b = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    _c.add_item(db_conn, campaign_id=cid, item_type="post")  # stays planned
    _c.transition_item_status(db_conn, item_id=a, new_status="shipped")
    _c.transition_item_status(db_conn, item_id=b, new_status="shipped")

    payload = _c.analyze_progress(db_conn, campaign_id=cid)
    assert payload["progress"]["shipped"] == 2
    assert payload["progress"]["planned"] == 1
    assert abs(payload["progress"]["percent_shipped"] - (2 / 3)) < 1e-9
    assert payload["name"] == "p"
    assert payload["status"] == "active"


def test_analyze_progress_marks_on_track_when_actual_exceeds_target(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    _c.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "20000"}],
            "validation": [{"metric": "downloads", "actual": "10"}],
        },
        lesson="x",
        counterfactual_note="y",
    )
    payload = _c.analyze_progress(db_conn, campaign_id=cid)
    by_metric = {p["metric"]: p for p in payload["success_criteria_progress"]}
    assert by_metric["impressions"]["on_track"] is True
    assert by_metric["downloads"]["on_track"] is True


# ---------------------------------------------------------------------------
# Audit-log write-through coverage.
# ---------------------------------------------------------------------------
def test_state_transitions_emit_audit_rows(db_conn: sqlite3.Connection) -> None:
    cid = _make_campaign(db_conn)
    _c.activate_campaign(db_conn, campaign_id=cid)
    item_id = _c.add_item(db_conn, campaign_id=cid, item_type="post")
    _c.transition_item_status(db_conn, item_id=item_id, new_status="shipped")

    camp_rows = _audit_log.query(db_conn, target_type="campaign", target_id=cid)
    camp_events = {r.event_type for r in camp_rows}
    assert "campaign_created" in camp_events
    assert "campaign_activated" in camp_events

    item_rows = _audit_log.query(
        db_conn, target_type="campaign_item", target_id=item_id
    )
    item_events = {r.event_type for r in item_rows}
    assert "campaign_item_added" in item_events
    assert "campaign_item_status_shipped" in item_events


# ---------------------------------------------------------------------------
# Agent tool #21 — analyze_campaign_progress wrapper.
# ---------------------------------------------------------------------------
def test_tool_analyze_campaign_progress_returns_payload(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _make_campaign(db_conn)
    tool = get_tool("analyze_campaign_progress")
    payload = tool.handler(db_conn, campaign_id=cid)
    assert payload["campaign_id"] == cid
    assert "progress" in payload
    assert "success_criteria_progress" in payload


def test_tool_registry_includes_analyze_campaign_progress() -> None:
    from app.agent.tools import AGENT_TOOLS

    names = {t.name for t in AGENT_TOOLS}
    assert "analyze_campaign_progress" in names
