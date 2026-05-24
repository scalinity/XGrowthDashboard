"""Filesystem path resolution for Streamlit and the native app (spec §31.5).

User-writable data resolves with precedence:

  1. ``XGROWTH_DATA_DIR`` env override (dev / tests).
  2. ``~/Library/Application Support/XGrowthDashboard`` — but only once its DB
     file exists (i.e. the native app has run at least once and migrated).
  3. legacy ``<repo>/data`` — the original location and the Streamlit dev
     default. This is what ``streamlit run`` uses until the native app runs.

The native sidecar calls ``migrate_legacy_db_if_needed()`` once at startup to
COPY (not move) the legacy DB into Application Support — leaving ``./data``
intact so the Streamlit version keeps working during development. The copy
uses SQLite's online backup API so it is consistent even if the source DB is
open (no WAL/SHM corruption).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

APP_NAME = "XGrowthDashboard"
DB_FILENAME = "dashboard.db"

# app/paths.py → app/ → repo root.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR: Path = PROJECT_ROOT / "data"


def application_support_dir() -> Path:
    """``~/Library/Application Support/XGrowthDashboard`` (the native data home)."""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def _env_data_dir() -> Path | None:
    value = os.environ.get("XGROWTH_DATA_DIR")
    return Path(value).expanduser() if value else None


def resolve_data_dir() -> Path:
    """Return the active data directory per the §31.5 precedence."""
    env = _env_data_dir()
    if env is not None:
        return env
    app_support = application_support_dir()
    if (app_support / DB_FILENAME).exists():
        return app_support
    return LEGACY_DATA_DIR


def resolve_db_path() -> Path:
    """Return the active ``dashboard.db`` path."""
    return resolve_data_dir() / DB_FILENAME


def migrate_legacy_db_if_needed() -> Path:
    """COPY the legacy ``./data`` DB into Application Support on first native launch.

    No-op when: an ``XGROWTH_DATA_DIR`` override is set, the Application Support
    DB already exists, or there is no legacy DB to copy. Uses the SQLite online
    backup API for a consistent copy even if the legacy DB is open. Returns the
    resolved DB path after any migration.
    """
    if _env_data_dir() is not None:
        return resolve_db_path()

    app_support = application_support_dir()
    target = app_support / DB_FILENAME
    legacy = LEGACY_DATA_DIR / DB_FILENAME

    if target.exists() or not legacy.exists():
        return resolve_db_path()

    app_support.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(legacy))
    try:
        dst = sqlite3.connect(str(target))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target
