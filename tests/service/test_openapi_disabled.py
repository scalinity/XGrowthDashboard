"""Production sidecar must not expose unauthenticated OpenAPI/docs routes."""

from __future__ import annotations

from app.db import connect
from app.service.app import create_app


def test_openapi_and_docs_disabled_without_dev_cors() -> None:
    app = create_app(token="t", conn_factory=lambda: connect(":memory:"))
    client_paths = {getattr(r, "path", "") for r in app.routes}
    assert "/openapi.json" not in client_paths
    assert "/docs" not in client_paths
    assert "/redoc" not in client_paths


def test_openapi_available_in_dev_mode() -> None:
    app = create_app(
        token="t",
        conn_factory=lambda: connect(":memory:"),
        dev_cors_origins=["http://127.0.0.1:5173"],
    )
    client_paths = {getattr(r, "path", "") for r in app.routes}
    assert "/openapi.json" in client_paths
