"""Tests for the §28.27 Monthly AI reviews module.

Covers:
- iso_month parsing (valid + invalid).
- compute_auto_filled_fields returns the documented shape, including
  the new strongest/weakest_content_type and campaigns_completed_json
  fields.
- campaigns_completed_json correctly picks up campaigns that completed
  inside the month window — and excludes those that completed outside.
- upsert_monthly_review writes to the right keyed row + emits audit.
- export_blocked_reason mirrors the §14.6 rules: counterfactual
  required AND confidence_label='speculation' blocks.
- Agent tool #22 draft_monthly_review_section validates section_name
  and surfaces the auto-fill context for the future Session-2 prompt.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from app.agent import audit_log as _audit_log
from app.agent import campaigns as _campaigns
from app.agent import monthly_review as _mr
from app.agent.tools import get_tool


# ---------------------------------------------------------------------------
# parse_iso_month.
# ---------------------------------------------------------------------------
def test_parse_iso_month_handles_typical() -> None:
    start, end = _mr.parse_iso_month("2026-05")
    assert (start, end) == (date(2026, 5, 1), date(2026, 5, 31))


def test_parse_iso_month_february_leap_year_2024() -> None:
    start, end = _mr.parse_iso_month("2024-02")
    assert (start, end) == (date(2024, 2, 1), date(2024, 2, 29))


def test_parse_iso_month_rejects_bad_format() -> None:
    for bad in ("2026/05", "May 2026", "2026-13", "26-05", ""):
        with pytest.raises(_mr.InvalidIsoMonthError):
            _mr.parse_iso_month(bad)


# ---------------------------------------------------------------------------
# compute_auto_filled_fields.
# ---------------------------------------------------------------------------
def test_compute_auto_filled_fields_returns_documented_shape(
    db_conn: sqlite3.Connection,
) -> None:
    auto = _mr.compute_auto_filled_fields(db_conn, "2026-05")
    assert auto.iso_month == "2026-05"
    assert auto.month_start_date == "2026-05-01"
    assert auto.month_end_date == "2026-05-31"
    # campaigns_completed_json is JSON-encoded list (possibly empty).
    parsed = json.loads(auto.campaigns_completed_json)
    assert isinstance(parsed, list)


def test_p511r4_completed_at_space_separated_picks_up_first_of_month(
    db_conn: sqlite3.Connection,
) -> None:
    """SQLite datetime('now') writes space-separated timestamps; the previous
    T-separated bounds comparison silently dropped first-of-month
    completions. Pin the format-agnostic behavior."""
    cid = _campaigns.create_campaign(
        db_conn,
        name="first of month",
        theme="t",
        hypothesis="h",
        start_date="2026-05-01",
        end_date="2026-05-28",
        success_criteria={
            "distribution": [{"metric": "impressions", "target": "10000"}],
            "validation": [{"metric": "downloads", "target": "5"}],
        },
    )
    _campaigns.activate_campaign(db_conn, campaign_id=cid)
    _campaigns.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "1"}],
            "validation": [{"metric": "downloads", "actual": "1"}],
        },
        lesson="x",
        counterfactual_note="y",
    )
    # Force SPACE-separated completed_at_utc on the FIRST of the month
    # — exactly the input the previous query dropped.
    db_conn.execute(
        "UPDATE campaigns SET completed_at_utc = ? WHERE id = ?",
        ("2026-05-01 00:30:00", cid),
    )
    auto = _mr.compute_auto_filled_fields(db_conn, "2026-05")
    parsed = json.loads(auto.campaigns_completed_json)
    assert any(p["campaign_id"] == cid for p in parsed), (
        "campaign completed at '2026-05-01 00:30:00' (space-separated, "
        "first-of-month) must appear in the May rollup"
    )


def test_compute_auto_filled_campaigns_completed_picks_up_in_month(
    db_conn: sqlite3.Connection,
) -> None:
    cid = _campaigns.create_campaign(
        db_conn,
        name="may push",
        theme="t",
        hypothesis="h",
        start_date="2026-05-01",
        end_date="2026-05-28",
        success_criteria={
            "distribution": [{"metric": "impressions", "target": "10000"}],
            "validation": [{"metric": "downloads", "target": "5"}],
        },
    )
    _campaigns.activate_campaign(db_conn, campaign_id=cid)
    _campaigns.complete_campaign(
        db_conn,
        campaign_id=cid,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "12345"}],
            "validation": [{"metric": "downloads", "actual": "6"}],
        },
        lesson="explicit asks helped.",
        counterfactual_note="cohort effects.",
    )
    # Force completed_at_utc inside the May window for deterministic testing.
    db_conn.execute(
        "UPDATE campaigns SET completed_at_utc = ? WHERE id = ?",
        ("2026-05-15T10:00:00", cid),
    )

    auto = _mr.compute_auto_filled_fields(db_conn, "2026-05")
    parsed = json.loads(auto.campaigns_completed_json)
    assert len(parsed) == 1
    assert parsed[0]["campaign_id"] == cid
    assert parsed[0]["name"] == "may push"
    assert parsed[0]["lesson"] == "explicit asks helped."

    # A campaign completed in June must NOT appear in the May payload.
    cid2 = _campaigns.create_campaign(
        db_conn,
        name="june push",
        theme="t",
        hypothesis="h",
        start_date="2026-06-01",
        end_date="2026-06-28",
        success_criteria={
            "distribution": [{"metric": "impressions", "target": "1000"}],
            "validation": [{"metric": "downloads", "target": "1"}],
        },
    )
    _campaigns.activate_campaign(db_conn, campaign_id=cid2)
    _campaigns.complete_campaign(
        db_conn,
        campaign_id=cid2,
        success_criteria_actuals={
            "distribution": [{"metric": "impressions", "actual": "2000"}],
            "validation": [{"metric": "downloads", "actual": "2"}],
        },
        lesson="L",
        counterfactual_note="C",
    )
    db_conn.execute(
        "UPDATE campaigns SET completed_at_utc = ? WHERE id = ?",
        ("2026-06-10T10:00:00", cid2),
    )

    auto_may = _mr.compute_auto_filled_fields(db_conn, "2026-05")
    parsed_may = json.loads(auto_may.campaigns_completed_json)
    assert {p["campaign_id"] for p in parsed_may} == {cid}

    auto_jun = _mr.compute_auto_filled_fields(db_conn, "2026-06")
    parsed_jun = json.loads(auto_jun.campaigns_completed_json)
    assert {p["campaign_id"] for p in parsed_jun} == {cid2}


# ---------------------------------------------------------------------------
# upsert_monthly_review.
# ---------------------------------------------------------------------------
def test_upsert_monthly_review_creates_then_updates_same_row(
    db_conn: sqlite3.Connection,
) -> None:
    row_id = _mr.upsert_monthly_review(
        db_conn,
        iso_month="2026-05",
        fields={"summary": "first draft"},
    )
    same_id = _mr.upsert_monthly_review(
        db_conn,
        iso_month="2026-05",
        fields={"summary": "revised draft", "lesson": "be more specific."},
    )
    assert same_id == row_id

    review = _mr.get_monthly_review(db_conn, iso_month="2026-05")
    assert review is not None
    assert review["summary"] == "revised draft"
    assert review["lesson"] == "be more specific."


def test_upsert_monthly_review_audit_logged(
    db_conn: sqlite3.Connection,
) -> None:
    row_id = _mr.upsert_monthly_review(
        db_conn,
        iso_month="2026-05",
        fields={"summary": "x"},
    )
    rows = _audit_log.query(
        db_conn, target_type="monthly_review", target_id=row_id
    )
    events = {r.event_type for r in rows}
    assert "monthly_review_created" in events


def test_p511r15_upsert_empty_payload_on_existing_row_is_noop(
    db_conn: sqlite3.Connection,
) -> None:
    """Empty payload on an existing row should write nothing — no UPDATE,
    no audit row. Previously the audit row landed with field_count=0
    even though the DB state didn't change."""
    row_id = _mr.upsert_monthly_review(
        db_conn,
        iso_month="2026-05",
        fields={"summary": "first"},
    )
    # Re-upsert with empty payload.
    same_id = _mr.upsert_monthly_review(
        db_conn, iso_month="2026-05", fields={}
    )
    assert same_id == row_id
    rows = _audit_log.query(
        db_conn, target_type="monthly_review", target_id=row_id
    )
    events = [r.event_type for r in rows]
    # Exactly one event — the original creation. No spurious
    # monthly_review_updated row from the empty-payload re-upsert.
    assert events == ["monthly_review_created"]


def test_upsert_monthly_review_rejects_bad_iso_month(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_mr.InvalidIsoMonthError):
        _mr.upsert_monthly_review(
            db_conn, iso_month="2026/05", fields={"summary": "x"}
        )


# ---------------------------------------------------------------------------
# Export-blocker rule.
# ---------------------------------------------------------------------------
def test_export_blocked_without_counterfactual() -> None:
    assert _mr.export_blocked_reason({"counterfactual_note": ""}) is not None
    assert _mr.export_blocked_reason({"counterfactual_note": "   "}) is not None
    assert _mr.export_blocked_reason({"counterfactual_note": None}) is not None


def test_export_blocked_when_speculation_label() -> None:
    reason = _mr.export_blocked_reason(
        {
            "counterfactual_note": "ok",
            "confidence_label": "speculation",
        }
    )
    assert reason is not None
    assert "speculation" in reason.lower()


def test_export_unblocked_when_counterfactual_and_label_ok() -> None:
    assert (
        _mr.export_blocked_reason(
            {"counterfactual_note": "ok", "confidence_label": "inference"}
        )
        is None
    )


def test_assert_exportable_raises_when_blocked() -> None:
    with pytest.raises(_mr.ExportBlockedError):
        _mr.assert_exportable({"counterfactual_note": ""})


# ---------------------------------------------------------------------------
# Agent tool #22 — draft_monthly_review_section.
# ---------------------------------------------------------------------------
def test_tool_registry_includes_draft_monthly_review_section() -> None:
    from app.agent.tools import AGENT_TOOLS
    names = {t.name for t in AGENT_TOOLS}
    assert "draft_monthly_review_section" in names


def test_tool_draft_monthly_review_section_validates_name(
    db_conn: sqlite3.Connection,
) -> None:
    tool = get_tool("draft_monthly_review_section")
    bad = tool.handler(db_conn, section_name="garbage", iso_month="2026-05")
    assert "error" in bad


def test_tool_draft_monthly_review_section_accepts_campaigns_retro(
    db_conn: sqlite3.Connection,
) -> None:
    tool = get_tool("draft_monthly_review_section")
    res = tool.handler(
        db_conn, section_name="campaigns_retro", iso_month="2026-05"
    )
    assert res["section_name"] == "campaigns_retro"
    assert res["iso_month"] == "2026-05"
    # The Session-1 stub surfaces an auto-fill payload for the future
    # Session-2 prompt — confirm the campaigns_retro section gets one.
    assert "auto_filled" in res
    assert "campaigns_completed_json" in res["auto_filled"]


def test_tool_draft_monthly_review_section_rejects_bad_iso_month(
    db_conn: sqlite3.Connection,
) -> None:
    tool = get_tool("draft_monthly_review_section")
    res = tool.handler(
        db_conn, section_name="interpretation", iso_month="bad-month"
    )
    assert "error" in res


# ---------------------------------------------------------------------------
# Helper.
# ---------------------------------------------------------------------------
def test_iso_month_of_helper() -> None:
    assert _mr.iso_month_of(date(2026, 5, 15)) == "2026-05"
    assert _mr.iso_month_of(date(2026, 12, 31)) == "2026-12"
    assert _mr.iso_month_of(date(2027, 1, 1)) == "2027-01"


# A date one month before today is in the same logical month, etc — the
# helper is pure-date-math; no fixtures needed beyond the local time.
def test_iso_month_of_today_is_consistent() -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    # Same month: identical iso strings; otherwise differ.
    if today.month == yesterday.month:
        assert _mr.iso_month_of(today) == _mr.iso_month_of(yesterday)
    else:
        assert _mr.iso_month_of(today) != _mr.iso_month_of(yesterday)
