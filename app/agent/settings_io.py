"""Shared settings reader for the agent modules (P59A-W8).

Five Phase 5.9 modules (niche, content_types, personality_lore,
velocity, session) had their own JSON-decode-with-default helpers.
This module is the single source of truth so future drift across
modules isn't possible.

Each helper accepts a connection, the settings key, and a default.
Malformed JSON or a missing row both return the default — quiet
fallback is the right behavior here because every caller is rendering
a UI that already handles "default" gracefully, and a raised exception
on a corrupted settings row would block the page.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

_LOG = logging.getLogger(__name__)


def _raw_value_json(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return row["value_json"]


def get_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    raw = _raw_value_json(conn, key)
    if raw is None:
        return default
    try:
        return int(json.loads(raw))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        _LOG.warning("settings[%r] unparseable as int (%r); using default %r",
                     key, exc, default)
        return default


def get_str(conn: sqlite3.Connection, key: str, default: str) -> str:
    raw = _raw_value_json(conn, key)
    if raw is None:
        return default
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        _LOG.warning("settings[%r] unparseable as str (%r); using default %r",
                     key, exc, default)
        return default
    if val is None:
        return default
    return str(val)


def get_bool(conn: sqlite3.Connection, key: str, default: bool) -> bool:
    """Return the boolean stored at ``settings[key]`` or ``default``.

    P9R-34: tighten acceptance to literal JSON booleans + numeric 0/1
    only. Pre-fix, ``bool(json.loads(raw))`` returned True for any
    truthy JSON value — including lists, dicts, and strings — so a
    stray ``"yes"`` or ``[0]`` could silently flip a kill switch.
    Reject those shapes and fall back to ``default`` with a WARNING.

    Accepted shapes:
      * ``true`` / ``false`` (JSON literal)
      * ``0`` / ``1`` (JSON number — common settings convention)
    Anything else logs a warning and returns ``default``.
    """
    raw = _raw_value_json(conn, key)
    if raw is None:
        return default
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        _LOG.warning("settings[%r] unparseable as bool (%r); using default %r",
                     key, exc, default)
        return default
    if isinstance(parsed, bool):
        return parsed
    if isinstance(parsed, int) and parsed in (0, 1):
        return bool(parsed)
    _LOG.warning(
        "settings[%r] rejected as bool — got %r (type=%s); using default %r",
        key, parsed, type(parsed).__name__, default,
    )
    return default


def get_json(
    conn: sqlite3.Connection, key: str, default: Any = None
) -> Any:
    raw = _raw_value_json(conn, key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        _LOG.warning("settings[%r] unparseable as json (%r); using default %r",
                     key, exc, default)
        return default


__all__ = ["get_bool", "get_int", "get_json", "get_str"]
