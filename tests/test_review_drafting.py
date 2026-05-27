"""Tests for model-backed review drafting."""

from __future__ import annotations

import sqlite3

import pytest

from app.agent.review_drafting import (
    draft_monthly_review_section,
    draft_weekly_review_section,
)
from app.db import apply_migrations, connect
from scripts.seed_settings import seed_settings


@pytest.fixture
def conn() -> sqlite3.Connection:
    db = connect(":memory:")
    apply_migrations(db)
    seed_settings(db)
    db.execute(
        """
        INSERT INTO weekly_reviews (week_start_date, week_end_date)
        VALUES ('2026-05-19', '2026-05-25')
        """
    )
    db.commit()
    yield db
    db.close()


def test_weekly_review_missing_key_degraded(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    week_id = conn.execute("SELECT id FROM weekly_reviews LIMIT 1").fetchone()[0]
    result = draft_weekly_review_section(
        conn, section_name="interpretation", week_id=week_id
    )
    assert result["status"] == "degraded"
    assert result["draft_text"] is None
    assert "manual_fallback" in result


def test_weekly_review_success_with_mock(conn: sqlite3.Connection) -> None:
    def fake_caller(system: str, user: str, model: str) -> tuple[str, int, int]:
        _ = (system, user, model)
        return ("Weekly draft with [confidence: fact] insight.", 12, 18)

    week_id = conn.execute("SELECT id FROM weekly_reviews LIMIT 1").fetchone()[0]
    result = draft_weekly_review_section(
        conn,
        section_name="interpretation",
        week_id=week_id,
        model_caller=fake_caller,
    )
    assert result["status"] == "success"
    assert "Weekly draft" in result["draft_text"]
    assert result["input_tokens"] == 12


def test_monthly_review_success_with_mock(conn: sqlite3.Connection) -> None:
    def fake_caller(system: str, user: str, model: str) -> tuple[str, int, int]:
        _ = (system, user, model)
        return ("Draft with [confidence: tentative] body.", 10, 20)

    result = draft_monthly_review_section(
        conn,
        section_name="interpretation",
        iso_month="2026-05",
        model_caller=fake_caller,
    )
    assert result["status"] == "success"
    assert "Draft with" in result["draft_text"]
    assert result["input_tokens"] == 10
