"""Comprehensive append-only audit logging for state-changing events (§28.30).

Distinct from ``app/agent/audit.py`` — which records every
``agent_tool_calls`` row including read-only invocations. This module
records the canonical state-change ledger: what changed in the system,
not what the agent looked at.

The two tables serve different audiences:

* ``agent_tool_calls`` is the agent's own debugging surface (high volume,
  prunable, includes reads).
* ``audit_logs`` is Daniel's debugging + recovery surface (low volume,
  long-retention, state-changes only, agent has NO read or write access).

Write-through points are enumerated in §28.30 of ``spec.md``:

* OAuth connect/disconnect (Phase 5.5+).
* Every publish attempt (success + failure) — see ``app/agent/publish.py``.
* Every settings change — see ``app/forms/__init__.py::set_setting``.
* Every export — see ``app/exports/_audit.py::record_export``.
* Every data deletion / correction (carries
  ``details.snapshot_of_deleted_row``).
* Every backup run — see ``app/backup.py``.
* Every applied migration — see the final statement of each
  ``migrations/NNN_*.sql``.
* Every inspiration plagiarism override — see
  ``app/agent/inspiration.py`` (Phase 5.11).

The agent does NOT have a tool that touches ``audit_logs``. The startup
assertion in ``tools.py`` would fail if anyone added one — see
``test_audit_log_no_agent_tool_exposes_audit_logs``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

_log = logging.getLogger(__name__)

EventCategory = Literal[
    "auth", "x_op", "publish", "settings", "export", "data", "admin", "migration"
]

ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {"auth", "x_op", "publish", "settings", "export", "data", "admin", "migration"}
)


@dataclass(frozen=True, slots=True)
class AuditRow:
    """Decoded row from ``audit_logs`` — for the Settings viewer + tests."""

    id: int
    occurred_at_utc: str
    event_category: str
    event_type: str
    actor: str
    target_type: str | None
    target_id: str | None
    details: Mapping[str, Any] | None
    success: bool
    error_message: str | None


def log(
    conn: sqlite3.Connection,
    *,
    event_category: EventCategory,
    event_type: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: Mapping[str, Any] | None = None,
    success: bool = True,
    error_message: str | None = None,
    actor: str = "daniel",
) -> int:
    """Append one row to ``audit_logs`` and return the new row id.

    This is the canonical write-through. Callers must use this rather
    than handwriting an INSERT — the validation here is the floor that
    prevents the table from drifting from §28.30's category contract.

    The function deliberately uses the caller's connection (no new
    connect()) so it composes with surrounding transactions. When the
    caller is mid-``with transaction(conn):``, this insert lands as
    part of the same commit; when the caller is in autocommit, it
    lands immediately. Either way the append-only invariant holds.
    """
    if event_category not in ALLOWED_CATEGORIES:
        raise ValueError(
            f"Unknown event_category {event_category!r}. "
            f"Allowed: {sorted(ALLOWED_CATEGORIES)}."
        )
    if not event_type:
        raise ValueError("event_type must be a non-empty string.")

    details_json = json.dumps(details, default=_json_safe) if details is not None else None
    target_id_str = None if target_id is None else str(target_id)

    cur = conn.execute(
        """
        INSERT INTO audit_logs
          (event_category, event_type, actor, target_type, target_id,
           details_json, success, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            event_category,
            event_type,
            actor,
            target_type,
            target_id_str,
            details_json,
            1 if success else 0,
            error_message,
        ),
    )
    return int(cur.fetchone()[0])


def _json_safe(obj: Any) -> Any:
    """Fallback for json.dumps — coerce common non-JSON types to strings."""
    try:
        from datetime import date, datetime
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
    except Exception:  # noqa: BLE001 — defensive; never let audit logging itself crash
        pass
    return str(obj)


def query(
    conn: sqlite3.Connection,
    *,
    category: EventCategory | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[AuditRow]:
    """Query ``audit_logs`` for the Settings viewer panel.

    Filters compose with AND. ``since`` and ``until`` are ISO-8601
    strings matched against ``occurred_at_utc``. Rows return newest-first.
    """
    where: list[str] = []
    params: list[Any] = []
    if category is not None:
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"Unknown category {category!r}")
        where.append("event_category = ?")
        params.append(category)
    if target_type is not None:
        where.append("target_type = ?")
        params.append(target_type)
    if target_id is not None:
        where.append("target_id = ?")
        params.append(str(target_id))
    if since is not None:
        where.append("occurred_at_utc >= ?")
        params.append(since)
    if until is not None:
        where.append("occurred_at_utc <= ?")
        params.append(until)

    sql = "SELECT * FROM audit_logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_at_utc DESC, id DESC"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_audit(r) for r in rows]


def _row_to_audit(row: sqlite3.Row) -> AuditRow:
    details_raw = row["details_json"]
    if details_raw:
        try:
            details: Mapping[str, Any] | None = json.loads(details_raw)
        except json.JSONDecodeError:
            _log.warning("audit_logs row %s has un-decodable details_json", row["id"])
            details = None
    else:
        details = None
    return AuditRow(
        id=int(row["id"]),
        occurred_at_utc=str(row["occurred_at_utc"]),
        event_category=str(row["event_category"]),
        event_type=str(row["event_type"]),
        actor=str(row["actor"]),
        target_type=row["target_type"],
        target_id=row["target_id"],
        details=details,
        success=bool(row["success"]),
        error_message=row["error_message"],
    )


# ---------------------------------------------------------------------------
# Retention pruning (§28.30).
# ---------------------------------------------------------------------------
def prune(conn: sqlite3.Connection, *, retention_days: int | None = None) -> int:
    """Delete rows older than the retention window; self-audit the deletion.

    Returns the number of pruned rows. ``retention_days`` defaults to
    the ``audit_log_retention_days`` setting; pass 0 (or explicit None
    when the setting is 0) to disable pruning. The prune itself emits
    one ``admin/audit_logs_pruned`` row carrying the pruned count, so
    the deletion is visible in the same table it just trimmed.
    """
    if retention_days is None:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'audit_log_retention_days'"
        ).fetchone()
        if row is None:
            retention_days = 365  # mirror migration default
        else:
            try:
                retention_days = int(json.loads(row[0]))
            except (TypeError, json.JSONDecodeError, ValueError):
                retention_days = 365

    if not retention_days or retention_days <= 0:
        return 0

    cur = conn.execute(
        """
        DELETE FROM audit_logs
        WHERE occurred_at_utc < datetime('now', ?)
          AND NOT (event_category = 'admin' AND event_type = 'audit_logs_pruned')
        """,
        (f"-{int(retention_days)} days",),
    )
    pruned = cur.rowcount or 0
    log(
        conn,
        event_category="admin",
        event_type="audit_logs_pruned",
        details={"pruned_count": int(pruned), "retention_days": int(retention_days)},
    )
    return int(pruned)


# ---------------------------------------------------------------------------
# Convenience: settings-change diff logging.
# ---------------------------------------------------------------------------
def log_setting_change(
    conn: sqlite3.Connection,
    *,
    key: str,
    old_value: Any,
    new_value: Any,
) -> int:
    """Audit a settings UPDATE with the structured old/new diff per §28.30."""
    return log(
        conn,
        event_category="settings",
        event_type=f"settings_changed_{key}",
        target_type="setting",
        target_id=key,
        details={
            "setting_key": key,
            "old_value": old_value,
            "new_value": new_value,
        },
    )


__all__: Iterable[str] = (
    "ALLOWED_CATEGORIES",
    "AuditRow",
    "EventCategory",
    "log",
    "log_setting_change",
    "prune",
    "query",
)
