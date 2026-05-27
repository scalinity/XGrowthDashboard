"""Reply queue read model."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.service.legacy_handlers import _reply_queue_slice


def build_reply_queue_read_model(conn: sqlite3.Connection) -> dict[str, Any]:
    return _reply_queue_slice(conn)
