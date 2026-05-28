"""Golden JSON snapshots for read models (detect shape drift)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import apply_migrations, connect
from app.read_models.content_performance import build_content_performance_read_model
from app.read_models.progress import build_progress_read_model
from app.read_models.reply_queue import build_reply_queue_read_model
from app.read_models.today import build_today_read_model
from app.read_models.weekly_review import build_weekly_review_read_model
from scripts.seed_settings import seed_settings

SNAPSHOT_DIR = Path(__file__).parent / "__snapshots__"

BUILDERS = {
    "today": build_today_read_model,
    "progress": build_progress_read_model,
    "weekly_review": build_weekly_review_read_model,
    "content_performance": build_content_performance_read_model,
    "reply_queue": build_reply_queue_read_model,
}


@pytest.fixture
def seeded_conn():
    conn = connect(":memory:")
    apply_migrations(conn)
    seed_settings(conn)
    yield conn
    conn.close()


VOLATILE_KEYS: dict[str, set[str]] = {
    "today": {"today_iso"},
    "weekly_review": {"week_start", "week_end"},
}


def _stabilize(name: str, payload: dict) -> dict:
    cleaned = dict(payload)
    for key in VOLATILE_KEYS.get(name, set()):
        cleaned.pop(key, None)
    return cleaned


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_read_model_golden_snapshot(name: str, seeded_conn) -> None:
    builder = BUILDERS[name]
    payload = _stabilize(name, builder(seeded_conn))
    snapshot_path = SNAPSHOT_DIR / f"{name}.json"
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not snapshot_path.exists():
        snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload == expected
