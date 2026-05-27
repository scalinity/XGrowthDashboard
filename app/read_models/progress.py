"""Progress view read model."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.service.legacy_handlers import _progress_slice


def build_progress_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    return _progress_slice(conn)
