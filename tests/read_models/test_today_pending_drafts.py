"""Today read model — pending draft date alignment (local today_iso)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db import apply_migrations, connect
from app.read_models.today import build_today_read_model
from scripts.seed_settings import seed_settings


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "today_drafts.db"
    with connect(db_path) as c:
        apply_migrations(c)
        seed_settings(c)
        yield c


def test_pending_drafts_filter_matches_local_today_iso(conn) -> None:
    today_iso = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO agent_drafts (text, draft_kind, status, created_at)
        VALUES ('Draft for today', 'standalone', 'proposed', ?)
        """,
        (f"{today_iso}T15:30:00",),
    )
    y_iso = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        """
        INSERT INTO agent_drafts (text, draft_kind, status, created_at)
        VALUES ('Yesterday draft', 'standalone', 'proposed', ?)
        """,
        (f"{y_iso}T12:00:00",),
    )
    conn.commit()

    model = build_today_read_model(conn)
    previews = [d["text_preview"] for d in model["pending_drafts"]]

    assert any("Draft for today" in p for p in previews)
    assert not any("Yesterday draft" in p for p in previews)
