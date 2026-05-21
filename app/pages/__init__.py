"""Streamlit pages package — auto-discovered by ``streamlit run app/main.py``.

This module intentionally only houses a thin connection helper. Each page
file (``1_Today.py``, ``8_Manual_Entry.py``, etc.) owns its own rendering.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import DEFAULT_DB_PATH, apply_migrations, connect


def open_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a sqlite3 connection scoped to the current page render.

    Each Streamlit page invokes this once per rerun. SQLite connections are
    cheap and the project is single-user; pooling buys nothing here. The
    project's ``connect()`` wrapper sets WAL mode + foreign-key enforcement
    on every open.

    Migrations are applied once per Streamlit session via the
    ``db_initialized`` session-state flag (set by ``app/main.py``). When the
    flag is absent (direct page entry, tests, or scripts) we apply them
    defensively — they're idempotent so this is a safe fallback.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = connect(path)
    try:
        import streamlit as st
        already_bootstrapped = bool(st.session_state.get("db_initialized"))
    except Exception:  # noqa: BLE001 — running outside Streamlit
        already_bootstrapped = False
    if not already_bootstrapped:
        apply_migrations(conn)
    return conn
