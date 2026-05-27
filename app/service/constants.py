"""Sidecar service constants."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from app.agent.client import AgentClient
from app.db import connect
from app.paths import resolve_db_path

ConnFactory = Callable[[], sqlite3.Connection]
AgentClientFactory = Callable[[], AgentClient]

SERVICE_NAME = "x-growth-dashboard-service"
SERVICE_VERSION = "0.1.0"
TAURI_WEBVIEW_ORIGIN_REGEX = r"^(tauri://localhost|https?://tauri\.localhost)$"


def default_conn_factory():

    return connect(resolve_db_path())
