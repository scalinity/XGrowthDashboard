"""Today view read model."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.service.legacy_handlers import _today_slice


def build_today_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    return _today_slice(conn)
