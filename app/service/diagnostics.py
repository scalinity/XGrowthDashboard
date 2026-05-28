"""Non-sensitive health and diagnostics helpers for the FastAPI sidecar."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from app.paths import (
    LEGACY_DATA_DIR,
    RESOURCE_ROOT,
    application_support_dir,
    resolve_data_dir,
    resolve_db_path,
)
from app.secret_store import resolve_secret
from app.service.log_redaction import redact_detail, redact_text, sidecar_log_path
from app.service.settings_schema import MANAGED_SECRETS

# In-memory ring buffer of recent failed requests (path/status only).
_FAILED_REQUESTS: list[dict[str, Any]] = []
_MAX_FAILED_REQUESTS = 20


def record_failed_request(path: str, status_code: int, detail: Any) -> None:
    entry = {
        "path": path,
        "status_code": status_code,
        "detail": redact_detail(detail),
    }
    _FAILED_REQUESTS.append(entry)
    if len(_FAILED_REQUESTS) > _MAX_FAILED_REQUESTS:
        del _FAILED_REQUESTS[0 : len(_FAILED_REQUESTS) - _MAX_FAILED_REQUESTS]


def data_dir_source_label() -> str:
    if os.environ.get("XGROWTH_DATA_DIR"):
        return "XGROWTH_DATA_DIR override"
    if (application_support_dir() / "dashboard.db").exists():
        return "Application Support"
    if LEGACY_DATA_DIR.exists():
        return "legacy ./data"
    return "legacy ./data (not yet created)"


def latest_migration(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def configured_secrets_status() -> dict[str, dict[str, bool]]:
    return {
        name: {"present": bool(resolve_secret(name)), "configured": bool(resolve_secret(name))}
        for name in sorted(MANAGED_SECRETS)
    }


def read_recent_log_lines(limit: int = 200) -> list[str]:
    path = sidecar_log_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-limit:]
    return [redact_text(line) for line in tail]


def build_health_details(conn: sqlite3.Connection, *, service_version: str) -> dict[str, Any]:
    return {
        "ready": True,
        "sidecar_phase": "ready",
        "app_version": service_version,
        "service_version": service_version,
        "db_path": str(resolve_db_path()),
        "latest_migration": latest_migration(conn),
        "data_dir_source": data_dir_source_label(),
        "resource_root": str(RESOURCE_ROOT),
        "capabilities": {
            "anthropic_configured": bool(resolve_secret("ANTHROPIC_API_KEY")),
            "native_data_home": str(resolve_data_dir()),
        },
    }


def build_diagnostics_payload(conn: sqlite3.Connection, *, service_version: str) -> dict[str, Any]:
    health = build_health_details(conn, service_version=service_version)
    return {
        **health,
        "secrets": configured_secrets_status(),
        "recent_log_lines": read_recent_log_lines(),
        "recent_failed_requests": list(_FAILED_REQUESTS),
    }


def format_diagnostics_text(payload: dict[str, Any]) -> str:
    lines = [
        "X Growth Dashboard diagnostics",
        f"app_version: {payload.get('app_version')}",
        f"service_version: {payload.get('service_version')}",
        f"ready: {payload.get('ready')}",
        f"sidecar_phase: {payload.get('sidecar_phase')}",
        f"db_path: {payload.get('db_path')}",
        f"latest_migration: {payload.get('latest_migration')}",
        f"data_dir_source: {payload.get('data_dir_source')}",
        "secrets:",
    ]
    for name, status in (payload.get("secrets") or {}).items():
        lines.append(f"  {name}: present={status.get('present')}")
    lines.append("recent_failed_requests:")
    for item in payload.get("recent_failed_requests") or []:
        lines.append(f"  {item.get('status_code')} {item.get('path')}: {item.get('detail')}")
    lines.append("recent_log_lines:")
    for line in payload.get("recent_log_lines") or []:
        lines.append(f"  {line}")
    return redact_text("\n".join(lines))
