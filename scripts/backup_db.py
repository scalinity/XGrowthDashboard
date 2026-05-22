"""CLI wrapper for ``app.backup.backup_database``.

The importable backup runner lives at ``app/backup.py`` per the project
CLAUDE.md "Issue tracking and review-fix workflow" section (and the
broader rule that ``scripts/`` is for operational one-shots, not a
library presentation code can reach into). This file is the CLI shim:
it parses flags, calls ``backup_database`` from ``app.backup``, and prints
a structured JSON result.

Usage
-----
::

    uv run python -m scripts.backup_db
    uv run python -m scripts.backup_db --db-path data/dashboard.db --backups-dir data/backups

Defaults are read from the ``settings`` table:
    backup_dir              → backups directory (default ``data/backups``)
    backup_retention_days   → prune files older than N days (default 30)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make ``app.*`` importable when invoked as ``python -m scripts.backup_db``
# from a fresh shell. Mirrors the shim in ``scripts/init_db.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backup import (  # noqa: E402  — re-exported for callers that still import from here
    BACKUP_FILENAME_GLOB,
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    DEFAULT_BACKUPS_DIR,
    DEFAULT_RETENTION_DAYS,
    BackupIntegrityError,
    BackupResult,
    backup_database,
)
from app.db import DEFAULT_DB_PATH  # noqa: E402

__all__ = [
    "BACKUP_FILENAME_GLOB",
    "BACKUP_FILENAME_PREFIX",
    "BACKUP_FILENAME_SUFFIX",
    "DEFAULT_BACKUPS_DIR",
    "DEFAULT_RETENTION_DAYS",
    "BackupIntegrityError",
    "BackupResult",
    "backup_database",
    "main",
]


def _format_result(result: BackupResult) -> str:
    payload = asdict(result)
    payload["path"] = str(result.path)
    payload["pruned"] = [str(p) for p in result.pruned]
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a VACUUM INTO backup of the X Growth Dashboard SQLite DB.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Source DB path (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--backups-dir",
        type=Path,
        default=None,
        help="Backups directory (default: settings.backup_dir, falling back to data/backups).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Retention in days (default: settings.backup_retention_days, falling back to 30).",
    )
    args = parser.parse_args(argv)

    try:
        result = backup_database(
            source_path=args.db_path,
            backups_dir=args.backups_dir,
            retention_days=args.retention_days,
        )
    except (FileNotFoundError, BackupIntegrityError, RuntimeError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print(_format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
