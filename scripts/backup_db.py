"""VACUUM INTO backup runner for the X Growth Dashboard.

Phase 4 — backup and data hygiene. See ``spec.md`` §7.1, §17, §18 (rule 10),
and §25 Phase 4 checklist. The Streamlit Settings page and any future
cron/launchd schedule both call into this module.

Why VACUUM INTO and not ``cp``
------------------------------
SQLite's on-disk file is mutable while the database is open. A naive
``cp data/dashboard.db data/backups/...`` can capture a partially written
page and yield a backup file that opens but fails ``PRAGMA integrity_check``
under load. ``VACUUM INTO 'path'`` lets the engine produce a *transactionally
consistent* copy while the source DB stays open — the spec calls this out
as the only backup mechanism (``§18`` rule 10).

Usage
-----
::

    uv run python -m scripts.backup_db
    uv run python -m scripts.backup_db --db-path data/dashboard.db --backups-dir data/backups

Defaults are read from the ``settings`` table:
    backup_dir              → backups directory (default ``data/backups``)
    backup_retention_days   → prune files older than N days (default 30)

A successful run:
    1. Opens the source DB through ``app.db.connect()`` (pragmas + aggregates).
    2. Runs ``VACUUM INTO`` against a freshly named target.
    3. Opens the new file in a separate connection and runs
       ``PRAGMA integrity_check``; if the result is not ``ok`` the backup is
       deleted and a ``BackupIntegrityError`` is raised.
    4. Upserts ``settings.last_backup_at_utc``.
    5. Prunes ``x_growth_*.db`` files in the backups directory whose mtime is
       older than the retention window.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Make ``app.*`` importable when invoked as ``python -m scripts.backup_db``
# from a fresh shell. Mirrors the shim in ``scripts/init_db.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import DEFAULT_DB_PATH, connect  # noqa: E402
from app.forms import get_setting, set_setting  # noqa: E402

DEFAULT_BACKUPS_DIR: Path = DEFAULT_DB_PATH.parent / "backups"
DEFAULT_RETENTION_DAYS: int = 30
BACKUP_FILENAME_PREFIX: str = "x_growth_"
BACKUP_FILENAME_SUFFIX: str = ".db"
BACKUP_FILENAME_GLOB: str = f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"


class BackupIntegrityError(RuntimeError):
    """Raised when ``PRAGMA integrity_check`` on a fresh backup is not ``ok``."""


@dataclass(frozen=True)
class BackupResult:
    """Outcome of a single ``backup_database`` invocation."""

    path: Path
    size_bytes: int
    duration_ms: int
    integrity_check_passed: bool
    pruned: list[Path]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backup_filename(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    # ISO-8601-ish sortable filename, second precision. Filename order
    # matches mtime order under normal conditions; retention pruning uses
    # mtime regardless (so a clock-skew restore won't strand files).
    return moment.strftime(f"{BACKUP_FILENAME_PREFIX}%Y-%m-%d_%H%M%S{BACKUP_FILENAME_SUFFIX}")


def _quote_sqlite_path(path: Path) -> str:
    """Return a single-quoted SQL string literal for ``VACUUM INTO``.

    ``VACUUM INTO`` doesn't accept bound parameters, so the target path must
    be inlined as a literal. SQLite escapes embedded single quotes by
    doubling them — see https://sqlite.org/lang_keywords.html.
    """
    return "'" + str(path).replace("'", "''") + "'"


def _open_backup_for_integrity_check(backup_path: Path) -> str:
    """Open the freshly-written backup file in its own connection and run
    ``PRAGMA integrity_check``. Returns the first result row ('ok' on
    success). Uses a vanilla sqlite3 connection rather than the project
    wrapper so the check is independent of any custom aggregates."""
    import sqlite3

    check_conn = sqlite3.connect(str(backup_path))
    try:
        row = check_conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else ""
    finally:
        check_conn.close()


def _prune_old_backups(backups_dir: Path, retention_days: int) -> list[Path]:
    """Delete ``x_growth_*.db`` files older than ``retention_days`` by mtime.

    Returns the list of paths that were deleted. retention_days < 0 is
    treated as "keep forever" and is a no-op; retention_days == 0 prunes
    everything older than `now` (i.e. every file in the directory).
    """
    if retention_days < 0:
        return []
    threshold = time.time() - (retention_days * 86400)
    pruned: list[Path] = []
    for path in sorted(backups_dir.glob(BACKUP_FILENAME_GLOB)):
        try:
            if path.stat().st_mtime < threshold:
                path.unlink()
                pruned.append(path)
        except FileNotFoundError:
            # Race with a parallel run; skip silently.
            continue
    return pruned


def _resolve_backups_dir(conn, override: Path | None) -> Path:
    if override is not None:
        return Path(override)
    seeded = get_setting(conn, "backup_dir", default=str(DEFAULT_BACKUPS_DIR))
    return Path(seeded) if seeded else DEFAULT_BACKUPS_DIR


def _resolve_retention_days(conn, override: int | None) -> int:
    if override is not None:
        return int(override)
    seeded = get_setting(conn, "backup_retention_days", default=DEFAULT_RETENTION_DAYS)
    try:
        return int(seeded)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def backup_database(
    source_path: Path | str | None = None,
    backups_dir: Path | str | None = None,
    retention_days: int | None = None,
) -> BackupResult:
    """Run a single VACUUM INTO backup and prune old backups.

    Parameters
    ----------
    source_path
        Path to the live SQLite DB. Defaults to ``app.db.DEFAULT_DB_PATH``.
    backups_dir
        Directory the new backup file is written into. Defaults to the
        ``backup_dir`` settings row (or ``data/backups`` if unset).
    retention_days
        Files older than this many days are pruned at the end of the run.
        Defaults to the ``backup_retention_days`` settings row (or 30).
    """
    source = Path(source_path) if source_path is not None else DEFAULT_DB_PATH
    if not source.exists():
        raise FileNotFoundError(f"Source DB not found: {source}")

    conn = connect(source)
    try:
        backups_path = _resolve_backups_dir(conn, Path(backups_dir) if backups_dir else None)
        retention = _resolve_retention_days(conn, retention_days)

        # Resolve to an absolute path so VACUUM INTO doesn't pick up the
        # CWD at invocation time (cron runs typically start in $HOME).
        backups_path = backups_path.resolve()
        backups_path.mkdir(parents=True, exist_ok=True)

        target = backups_path / _backup_filename()
        # Defensive: if the filename collides (sub-second double-run) wait a
        # tick and regenerate rather than overwriting an existing backup.
        if target.exists():
            time.sleep(1)
            target = backups_path / _backup_filename()

        started = time.perf_counter()
        conn.execute(f"VACUUM INTO {_quote_sqlite_path(target)}")
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not target.exists():
            raise RuntimeError(
                f"VACUUM INTO completed but the target file is missing: {target}"
            )

        # Independent connection so the check is uncontaminated by the
        # source's session state.
        result = _open_backup_for_integrity_check(target)
        if result != "ok":
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise BackupIntegrityError(
                f"PRAGMA integrity_check on {target} returned {result!r} (expected 'ok'). "
                "Backup deleted; source DB is untouched."
            )

        set_setting(conn, "last_backup_at_utc", _now_utc_iso())
        pruned = _prune_old_backups(backups_path, retention)

        return BackupResult(
            path=target,
            size_bytes=target.stat().st_size,
            duration_ms=duration_ms,
            integrity_check_passed=True,
            pruned=pruned,
        )
    finally:
        conn.close()


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
