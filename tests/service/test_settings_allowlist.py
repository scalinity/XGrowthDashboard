"""Settings allowlist and type validation tests for the FastAPI sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.forms import get_setting
from app.service.app import create_app
from scripts.seed_settings import seed_settings

TOKEN = "test-token-abc123"
SENTINEL = "__XGROWTH_SECRET_SENTINEL__"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "settings.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    conn.close()

    def factory():
        return connect(db_path)

    return TestClient(create_app(token=TOKEN, conn_factory=factory))


def test_unknown_setting_key_rejected(client: TestClient) -> None:
    resp = client.put(
        "/settings/not_a_real_key",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": "nope"},
    )
    assert resp.status_code == 400
    assert "Unknown setting key" in resp.json()["detail"]


def test_wrong_setting_type_rejected(client: TestClient) -> None:
    resp = client.put(
        "/settings/daily_post_target",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": "not-an-int"},
    )
    assert resp.status_code == 422


def test_invalid_enum_rejected(client: TestClient) -> None:
    resp = client.put(
        "/settings/data_collection_mode",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": "cloud"},
    )
    assert resp.status_code == 422
    assert "manual" in resp.json()["detail"]


def test_unknown_secret_name_rejected(client: TestClient) -> None:
    resp = client.put(
        "/settings/secrets/NOPE",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": SENTINEL},
    )
    assert resp.status_code == 400


def test_secret_value_not_returned_from_secrets_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.service.app as svc

    store: dict[str, str] = { "ANTHROPIC_API_KEY": SENTINEL }
    monkeypatch.setattr(svc, "store_secret", lambda name, value: store.update({name: value}))
    monkeypatch.setattr(svc, "resolve_secret", store.get)

    resp = client.get("/settings/secrets", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.text
    assert SENTINEL not in body
    assert resp.json()["secrets"]["ANTHROPIC_API_KEY"]["present"] is True


def test_valid_setting_update_still_works(client: TestClient, tmp_path: Path) -> None:
    db_path = tmp_path / "settings.db"
    resp = client.put(
        "/settings/data_collection_mode",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"value": "manual"},
    )
    assert resp.status_code == 200
    conn = connect(db_path)
    try:
        assert get_setting(conn, "data_collection_mode") == "manual"
    finally:
        conn.close()
