"""FastAPI application factory for the sidecar (§31.3)."""

from __future__ import annotations


from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.agent import invariants
from app.agent.client import AgentClient
from app.db import apply_migrations
from app.service.constants import (
    SERVICE_VERSION,
    TAURI_WEBVIEW_ORIGIN_REGEX,
    AgentClientFactory,
    ConnFactory,
    default_conn_factory,
)
from app.service.diagnostics import record_failed_request
from app.service.dependencies import make_get_conn
from app.service.routes.registry import register_routes
from app.service.security import BearerTokenAuth


def create_app(
    *,
    token: str,
    conn_factory: ConnFactory | None = None,
    agent_client_factory: AgentClientFactory | None = None,
    run_invariants: bool = True,
    dev_cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the sidecar FastAPI app."""
    factory = conn_factory or default_conn_factory
    agent_factory = agent_client_factory or (lambda: AgentClient())

    boot_conn = factory()
    try:
        apply_migrations(boot_conn)
    finally:
        boot_conn.close()

    if run_invariants:
        invariants.run_all()

    app = FastAPI(title="X Growth Dashboard — local service", version=SERVICE_VERSION)
    auth = BearerTokenAuth(token)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request, exc: HTTPException):  # type: ignore[no-untyped-def]
        if exc.status_code >= 400:
            record_failed_request(
                request.url.path,
                exc.status_code,
                str(exc.detail),
            )
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    if dev_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=dev_cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=TAURI_WEBVIEW_ORIGIN_REGEX,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    get_conn = make_get_conn(factory)
    register_routes(app, auth, get_conn, agent_factory, factory)
    return app
