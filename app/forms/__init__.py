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
import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)


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
    except (TypeError, json.JSONDecodeError) as exc:
        # Surface corrupt settings rows. Silent fallback to default could
        # mask a hand-edit of settings.value_json that leaves a toggle
        # mysteriously sticky to its default.
        _log.warning(
            "settings[%r] value_json failed to decode (%s); falling back to default %r.",
            key, exc, default,
        )
        return default


def set_setting(
    conn: sqlite3.Connection,
    key: str,
    value: Any,
    *,
    suppress_audit: bool = False,
) -> None:
    """Upsert a settings row, JSON-encoding ``value``. Used by the Settings page.

    Phase 5.11 (§28.30): captures the prior value first, performs the
    upsert, then appends an ``audit_logs`` ``settings_changed_<key>`` row
    with the structured ``{setting_key, old_value, new_value}`` diff.
    The audit row is suppressed when the new value equals the prior
    value (no-op writes shouldn't pollute the log). The audit append is
    wrapped in a defensive try/except so a missing ``audit_logs`` table
    (e.g. a legacy DB created before migration 015) never blocks the
    underlying settings write.

    P511R-11: ``suppress_audit=True`` skips the audit append entirely.
    Use for system-touched operational telemetry keys (e.g.
    ``last_backup_at_utc`` — the backup itself already audit-logs
    ``admin/backup_run``; a parallel ``settings_changed_last_backup_
    at_utc`` row is pure noise that would accumulate ~365 rows/year).
    Daniel-editable settings should never pass this flag.
    """
    # Capture the prior value BEFORE the upsert so the diff is honest.
    prior_row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if prior_row is None:
        old_value: Any = None
    else:
        try:
            old_value = json.loads(prior_row[0])
        except (TypeError, json.JSONDecodeError):
            old_value = prior_row[0]  # surface the raw text so the diff isn't lost

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

    # Audit-log the change (§28.30 write-through point). Skipped when
    # the value didn't actually change OR when the caller flagged the
    # write as operational telemetry (P511R-11).
    if suppress_audit:
        return
    if old_value != value:
        try:
            # Local import to avoid an import cycle (app.agent.audit_log
            # depends on app.db only; this module is imported by many
            # downstream modules including the agent layer).
            from app.agent import audit_log as _audit_log
            _audit_log.log_setting_change(
                conn, key=key, old_value=old_value, new_value=value
            )
        except sqlite3.OperationalError as exc:
            # Likely "no such table: audit_logs" on a pre-migration-015
            # DB. The settings write has already succeeded; don't roll
            # it back over an audit hiccup. Surface a warning so a real
            # outage isn't silenced.
            _log.warning(
                "audit_log append skipped for settings[%r] change: %s. "
                "Run `uv run python -m scripts.init_db` to apply migration 015.",
                key, exc,
            )
