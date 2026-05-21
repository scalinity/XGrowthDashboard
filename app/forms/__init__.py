"""Manual entry form layer — see spec.md §15.

Each module in this package exposes:

- **pure submit/validate functions** that take a ``sqlite3.Connection`` and a
  payload dict, do the DB write, and return either the new row id or a
  structured error. These are exercised by ``tests/test_forms_*.py`` without
  Streamlit.
- a **``render(conn)`` Streamlit fragment** that wraps the submit function in
  an ``st.form`` for use from ``app/pages/8_Manual_Entry.py`` and the
  context-aware pages that land in Phase 3.

The split is the only way to honor the project's "derive, don't sync"
Streamlit rule (§ CLAUDE.md "Streamlit side-effects discipline"): the pure
functions know nothing about ``st.session_state`` and never mutate it; the
render functions own the session-state lifecycle.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any


class FormError(ValueError):
    """Raised by submit functions when validation fails.

    Carries a ``field_errors`` mapping ``{field_name: message}`` so the render
    layer can show inline errors next to each widget. The base ``ValueError``
    message is a human-readable summary used in tests and as a fallback in
    pages that don't have per-field UI.
    """

    def __init__(self, message: str, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.field_errors: dict[str, str] = field_errors or {}


def today_iso() -> str:
    """ISO-8601 date string for today's date (system local). UI default."""
    return date.today().isoformat()


def now_utc_iso() -> str:
    """ISO-8601 UTC timestamp with seconds precision. DB column default."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Read a JSON-decoded settings value by key (or ``default`` if missing).

    The ``settings`` table stores values as JSON text (see
    ``scripts/seed_settings.py``); this helper centralizes the decode so form
    code can call ``get_setting(conn, "counterfactual_required")`` and get
    back a real Python bool.
    """
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Upsert a settings row, JSON-encoding ``value``. Used by the Settings page."""
    conn.execute(
        """
        INSERT INTO settings (key, value_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(value)),
    )
