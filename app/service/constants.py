"""Sidecar service constants."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version

from app.agent.client import AgentClient
from app.db import connect
from app.paths import resolve_db_path

ConnFactory = Callable[[], sqlite3.Connection]
AgentClientFactory = Callable[[], AgentClient]

SERVICE_NAME = "x-growth-dashboard-service"


def _service_version() -> str:
    try:
        return version("x-growth-dashboard")
    except PackageNotFoundError:
        return "0.0.0"


SERVICE_VERSION = _service_version()
TAURI_WEBVIEW_ORIGIN_REGEX = r"^(tauri://localhost|https?://tauri\.localhost)$"


def default_conn_factory():

    return connect(resolve_db_path())
