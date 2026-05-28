"""HTTP error responses must not echo configured secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from scripts.seed_settings import seed_settings

SENTINEL = "__XGROWTH_SECRET_SENTINEL__"
TOKEN = "test-token-abc123"


def test_http_exception_detail_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    db_path = tmp_path / "err.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    client = TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))
    resp = client.put(
        "/settings/not_a_real_key",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": SENTINEL},
    )
    assert resp.status_code in {400, 422}
    assert SENTINEL not in resp.text


def test_form_validation_preserves_field_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "form_err.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()
    client = TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))
    resp = client.post(
        "/forms/snapshot",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"username": "x"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "field_errors" in detail
    assert "snapshot_date" in detail["field_errors"]
