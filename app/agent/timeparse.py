"""Explicit datetime parse helpers — RV2-14.

Phase 7 jobs cross two timestamp domains:

* SQLite's ``datetime('now')`` shape: ``YYYY-MM-DD HH:MM:SS`` (space
  separator, no timezone). Used by every column populated via
  ``datetime('now')`` inside the SQL — ``last_checked_at_utc``,
  ``collected_at_utc``, ``checked_at_utc``, etc.
* X API's RFC 3339 shape: ``YYYY-MM-DDTHH:MM:SS.sssZ`` (T separator,
  Z timezone suffix, optional fractional seconds). Used by the
  ``created_at`` field on tweets pulled via xurl.

The two are not safely interchangeable: ``datetime.strptime(s[:19],
"%Y-%m-%d %H:%M:%S")`` works on the SQLite shape but chops the ``T``
out of the X API shape. Pre-RV2-14 the parses lived as inline calls
that named the format implicitly; a future refactor could swap them
and the call would still type-check but fail at runtime on the wrong
shape.

These helpers name the format explicitly so future code uses the
right one for each domain.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_sqlite_datetime(value: str | None) -> datetime | None:
    """Parse SQLite's ``datetime('now')`` shape (UTC, space-separated).

    Returns a tz-aware UTC datetime, or ``None`` on parse failure.

    Examples:
        >>> parse_sqlite_datetime("2026-05-22 10:00:00")
        datetime.datetime(2026, 5, 22, 10, 0, tzinfo=datetime.timezone.utc)
        >>> parse_sqlite_datetime(None) is None
        True
        >>> parse_sqlite_datetime("bad") is None
        True
    """
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def parse_x_api_datetime(value: str | None) -> datetime | None:
    """Parse X API's RFC 3339 shape (e.g. ``2026-05-22T15:30:45.000Z``).

    Returns a tz-aware datetime (the X API includes the offset/Z),
    or ``None`` on parse failure.

    Examples:
        >>> parse_x_api_datetime("2026-05-22T15:30:45.000Z").year
        2026
        >>> parse_x_api_datetime("2026-05-22T15:30:45Z").tzinfo is not None
        True
        >>> parse_x_api_datetime(None) is None
        True
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def minutes_between_sqlite(earlier: str | None, later: str | None) -> float | None:
    """Return ``(later − earlier)`` in minutes when both are SQLite shape.

    None on either parse failure or when ``later < earlier``.
    """
    e = parse_sqlite_datetime(earlier)
    later_dt = parse_sqlite_datetime(later)
    if e is None or later_dt is None:
        return None
    delta = (later_dt - e).total_seconds() / 60.0
    return delta
