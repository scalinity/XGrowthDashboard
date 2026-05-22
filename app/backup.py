"""VACUUM INTO backup primitives for the X Growth Dashboard.

This is the *library* form of the Phase 4 backup runner. The CLI shim lives
at ``scripts/backup_db.py`` and re-exports everything below; the Streamlit
Settings page imports from this module directly. Per the project CLAUDE.md
"Issue tracking and review-fix workflow" section, presentation code should
not reach across into ``scripts/`` — keeping the importable surface here
in ``app/`` enforces that boundary.

See ``spec.md`` §7.1, §17, §18 rule 10, and §25 Phase 4 for the rules this
module implements.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db import DEFAULT_DB_PATH, PROJECT_ROOT, connect
from app.forms import get_setting, set_setting

DEFAULT_BACKUPS_DIR: Path = DEFAULT_DB_PATH.parent / "backups"
DEFAULT_RETENTION_DAYS: int = 30
BACKUP_FILENAME_PREFIX: str = "x_growth_"
BACKUP_FILENAME_SUFFIX: str = ".db"
BACKUP_FILENAME_GLOB: str = f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"

# Bounded retry budget when generating a fresh second-precision filename
# encounters an existing file. After this many time-based retries we fall
# back to a monotonic ``-N`` suffix so a sub-second double-click can never
# cause VACUUM INTO to refuse to overwrite.
FILENAME_TIME_RETRY_LIMIT: int = 3
FILENAME_SUFFIX_RETRY_LIMIT: int = 1_000


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
    return moment.strftime(f"{BACKUP_FILENAME_PREFIX}%Y-%m-%d_%H%M%S{BACKUP_FILENAME_SUFFIX}")


def _quote_sqlite_path(path: Path) -> str:
    """Return a single-quoted SQL string literal for ``VACUUM INTO``.

    ``VACUUM INTO`` doesn't accept bound parameters, so the target path must
    be inlined as a literal. SQLite escapes embedded single quotes by
    doubling them — see https://sqlite.org/lang_keywords.html.
    """
    return "'" + str(path).replace("'", "''") + "'"


def _open_backup_for_integrity_check(backup_path: Path) -> str:
    """Open the freshly-written backup file read-only via SQLite URI form
    and run ``PRAGMA integrity_check``. Returns the first result row
    ('ok' on success).

    ``mode=ro&immutable=1`` keeps SQLite from creating ``-wal``/``-shm``
    siblings next to the backup file. A normal close would clean those
    up under happy-path; an abrupt crash mid-check could strand them.
    Read-only URI mode dodges the question entirely.

    The vanilla sqlite3 connection (no app.db wrapper) keeps the check
    independent of any custom aggregates the project registers.
    """
    check_conn = sqlite3.connect(
        f"file:{backup_path}?mode=ro&immutable=1", uri=True
    )
    try:
        # fetchall (not fetchone) so a multi-corruption DB reports every
        # symptom in the resulting error message, not just the first.
        rows = check_conn.execute("PRAGMA integrity_check").fetchall()
        if not rows:
            return ""
        if len(rows) == 1 and rows[0][0] == "ok":
            return "ok"
        return "; ".join(r[0] for r in rows)
    finally:
        check_conn.close()


def _prune_old_backups(
    backups_dir: Path,
    retention_days: int,
    keep: Path | None = None,
) -> list[Path]:
    """Delete ``x_growth_*.db`` files older than ``retention_days`` by mtime.

    ``retention_days <= 0`` is treated as "keep forever" and is a no-op.
    This prevents a hand-edited settings value or a stray ``--retention-days
    0`` CLI flag from deleting the just-created backup: the prune sweep
    runs *after* VACUUM INTO, with a threshold captured at prune time
    (strictly later than the new file's mtime), so the fresh file would
    otherwise satisfy ``mtime < threshold`` and get unlinked.

    ``keep`` is also exempted from the sweep regardless of mtime —
    defensive against clock drift that could make the new file look "old".
    """
    if retention_days <= 0:
        return []
    threshold = time.time() - (retention_days * 86400)
    pruned: list[Path] = []
    for path in sorted(backups_dir.glob(BACKUP_FILENAME_GLOB)):
        if keep is not None and path == keep:
            continue
        try:
            if path.stat().st_mtime < threshold:
                path.unlink()
                pruned.append(path)
        except FileNotFoundError:
            continue
    return pruned


def _anchor_on_project_root(path: Path) -> Path:
    """If ``path`` is relative, anchor it on ``PROJECT_ROOT``.

    The seeded ``backup_dir`` value is ``"data/backups"`` (relative). The
    launchd plist and crontab in docs/AUTOMATION.md ``cd`` into the project
    first, so a naive ``Path.resolve()`` against CWD works there. But a
    manual ``uv run python -m scripts.backup_db`` from ``~`` would drop
    backups at ``~/data/backups/…`` while the dashboard looks under the
    project's own ``data/backups/``. Anchoring on PROJECT_ROOT keeps the
    two views consistent regardless of CWD.
    """
    return path if path.is_absolute() else PROJECT_ROOT / path


def _resolve_backups_dir(conn, override: Path | None) -> Path:
    if override is not None:
        return _anchor_on_project_root(Path(override))
    seeded = get_setting(conn, "backup_dir", default=str(DEFAULT_BACKUPS_DIR))
    return _anchor_on_project_root(Path(seeded) if seeded else DEFAULT_BACKUPS_DIR)


def _pick_target_path(backups_path: Path) -> Path:
    """Pick a free filename under ``backups_path`` with bounded retry.

    Second-precision filenames can collide if the caller mashes "Back up
    now" twice or a cron run overlaps a manual run. The previous
    implementation was a single ``sleep(1)`` then one regeneration —
    a sub-second second double-click could still produce identical paths
    after the sleep, and VACUUM INTO would refuse to overwrite. This
    function tries up to ``FILENAME_TIME_RETRY_LIMIT`` fresh timestamps,
    then falls back to monotonic ``-1``, ``-2``, … suffixes so we always
    find a free name in bounded time.
    """
    for _ in range(FILENAME_TIME_RETRY_LIMIT):
        target = backups_path / _backup_filename()
        if not target.exists():
            return target
        time.sleep(1)
    base = backups_path / _backup_filename()
    for suffix in range(1, FILENAME_SUFFIX_RETRY_LIMIT + 1):
        candidate = base.with_name(base.stem + f"-{suffix}" + base.suffix)
        if not candidate.exists():
            return candidate
    raise RuntimeError(
        f"Could not generate a unique backup filename in {backups_path} "
        f"after {FILENAME_TIME_RETRY_LIMIT} time-retries and "
        f"{FILENAME_SUFFIX_RETRY_LIMIT} suffix attempts."
    )


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
    """Run a single VACUUM INTO backup and prune old backups."""
    source = Path(source_path) if source_path is not None else DEFAULT_DB_PATH
    if not source.exists():
        raise FileNotFoundError(f"Source DB not found: {source}")

    conn = connect(source)
    try:
        backups_path = _resolve_backups_dir(conn, Path(backups_dir) if backups_dir else None)
        retention = _resolve_retention_days(conn, retention_days)

        backups_path = backups_path.resolve()
        backups_path.mkdir(parents=True, exist_ok=True)

        target = _pick_target_path(backups_path)

        started = time.perf_counter()
        conn.execute(f"VACUUM INTO {_quote_sqlite_path(target)}")
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not target.exists():
            raise RuntimeError(
                f"VACUUM INTO completed but the target file is missing: {target}"
            )

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
        pruned = _prune_old_backups(backups_path, retention, keep=target)

        return BackupResult(
            path=target,
            size_bytes=target.stat().st_size,
            duration_ms=duration_ms,
            integrity_check_passed=True,
            pruned=pruned,
        )
    finally:
        conn.close()
