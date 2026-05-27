"""Content performance read model."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.service.legacy_handlers import _content_performance_slice


def build_content_performance_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    return _content_performance_slice(conn)
