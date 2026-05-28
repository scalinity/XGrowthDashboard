"""Health and diagnostics routes for the FastAPI sidecar."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from app.service.constants import SERVICE_NAME, SERVICE_VERSION
from app.service.diagnostics import (
    build_diagnostics_payload,
    build_health_details,
    format_diagnostics_text,
)
from app.service.models import DiagnosticsCopyResponse, HealthDetailsResponse


def build_health_router(auth, get_conn) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, Any]:
        """Liveness probe for the Tauri shell's sidecar handshake. Unauthenticated."""
        return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    @router.get(
        "/health/details",
        dependencies=[Depends(auth)],
        response_model=HealthDetailsResponse,
    )
    def health_details(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """Structured non-sensitive sidecar readiness details for the native shell."""
        return build_health_details(conn, service_version=SERVICE_VERSION)

    @router.get(
        "/diagnostics/copy",
        dependencies=[Depends(auth)],
        response_model=DiagnosticsCopyResponse,
    )
    def diagnostics_copy(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        payload = build_diagnostics_payload(conn, service_version=SERVICE_VERSION)
        return {
            "diagnostics": payload,
            "text": format_diagnostics_text(payload),
        }

    return router
