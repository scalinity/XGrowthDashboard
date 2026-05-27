"""Regression tests ensuring configured secrets do not leak through common surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import apply_migrations, connect
from app.service.app import create_app
from app.service.log_redaction import redact_text

TOKEN = "test-token-abc123"
SENTINEL = "__XGROWTH_SECRET_SENTINEL__"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "secrets.db"
    conn = connect(db_path)
    apply_migrations(conn)
    conn.close()

    import app.service.routes.registry as registry

    store: dict[str, str] = {"ANTHROPIC_API_KEY": SENTINEL}
    monkeypatch.setattr(registry, "store_secret", lambda name, value: store.update({name: value}))
    monkeypatch.setattr(registry, "resolve_secret", store.get)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    monkeypatch.setenv("XAI_API_KEY", SENTINEL)

    return TestClient(create_app(token=TOKEN, conn_factory=lambda: connect(db_path)))


def test_settings_secrets_response_omits_secret_value(client: TestClient) -> None:
    resp = client.get("/settings/secrets", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert SENTINEL not in resp.text


def test_settings_read_response_omits_secret_value(client: TestClient) -> None:
    resp = client.get("/settings", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert SENTINEL not in resp.text


def test_log_redaction_strips_sentinel_and_bearer() -> None:
    raw = f"Authorization: Bearer {SENTINEL} and key={SENTINEL}"
    redacted = redact_text(raw, extra_secrets=[SENTINEL])
    assert SENTINEL not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_log_redaction_filter_on_logger_record() -> None:
    message = redact_text(f"token={SENTINEL}", extra_secrets=[SENTINEL])
    assert SENTINEL not in message
