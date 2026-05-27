"""Weekly review read model."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.service.legacy_handlers import _weekly_review_slice


def build_weekly_review_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    return _weekly_review_slice(conn)
