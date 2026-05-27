"""Route layout regression tests."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.service.app import create_app

EXPECTED_PATHS = {
    "/health",
    "/health/details",
    "/diagnostics/copy",
    "/views/today",
    "/views/next-rep",
    "/views/progress",
    "/views/content-performance",
    "/views/validation",
    "/views/weekly-review",
    "/views/reply-queue",
    "/views/content-calendar",
    "/views/campaigns",
    "/views/inspiration",
    "/views/blogs",
    "/views/brain-dump",
    "/views/account-researcher",
    "/settings",
    "/settings/secrets",
    "/agent/conversations",
    "/publish",
}


def test_create_app_registers_expected_paths() -> None:
    app = create_app(token="layout-test", conn_factory=lambda: __import__("sqlite3").connect(":memory:"))
    paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    missing = sorted(EXPECTED_PATHS - paths)
    assert not missing, f"missing routes: {missing}"
