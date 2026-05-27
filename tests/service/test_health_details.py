"""Health and diagnostics endpoint tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app

TOKEN = "test-token-abc123"
SENTINEL = "__XGROWTH_SECRET_SENTINEL__"


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "health.db"
    conn = connect(db_path)
    apply_migrations(conn)
    conn.close()
    return TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))


def test_health_details_shape(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/health/details", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["sidecar_phase"] == "ready"
    assert "db_path" in body
    assert "latest_migration" in body
    assert "data_dir_source" in body
    assert SENTINEL not in resp.text


def test_diagnostics_copy_redacts_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    import app.service.routes.registry as registry

    store = {"ANTHROPIC_API_KEY": SENTINEL}
    monkeypatch.setattr(registry, "store_secret", lambda name, value: store.update({name: value}))
    monkeypatch.setattr(registry, "resolve_secret", store.get)

    client = _client(tmp_path)
    resp = client.get("/diagnostics/copy", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert "diagnostics" in body
    assert "text" in body
    assert SENTINEL not in resp.text
    assert body["diagnostics"]["secrets"]["ANTHROPIC_API_KEY"]["present"] is True
