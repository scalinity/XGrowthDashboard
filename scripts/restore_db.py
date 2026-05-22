"""Restore the live SQLite DB from a VACUUM INTO backup.

Phase 4 companion to ``scripts/backup_db.py``. The restore path is
deliberately defensive:

* Dry-run is the default. The destructive form requires ``--confirm``.
* The backup file is integrity-checked before anything is moved.
* The current target DB is renamed to a timestamped sidecar
  (``<target>.pre-restore.<ts>``) rather than deleted, so a botched restore
  can be rolled back manually.

Usage
-----
::

    # Dry-run — prints the plan, exits 0, touches nothing:
    uv run python -m scripts.restore_db --backup data/backups/x_growth_2026-05-21_210000.db

    # Real restore — requires --confirm:
    uv run python -m scripts.restore_db --backup data/backups/x_growth_2026-05-21_210000.db --confirm

Optional flags:
    --target PATH    Override the destination DB (default: app.db.DEFAULT_DB_PATH).

There is no auto-restore on detected corruption. Manual restore is the only
restore — Daniel decides when (and from which file) the DB rolls back.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import DEFAULT_DB_PATH  # noqa: E402


class RestoreIntegrityError(RuntimeError):
    """Raised when the chosen backup file fails ``PRAGMA integrity_check``."""


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of a single ``restore_database`` invocation."""

    backup_path: Path
    target_path: Path
    sidecar_path: Path | None
    dry_run: bool
    integrity_check_passed: bool


class RestoreBlockedByOpenDB(RuntimeError):
    """Raised when ``target-wal``/``-shm`` files exist at restore time —
    strong evidence Streamlit (or another process) has the DB open and the
    restore would silently lose writes to a renamed-out inode."""


def _integrity_check(path: Path) -> str:
    """Open ``path`` read-only via URI form and run ``PRAGMA integrity_check``.

    Read-only URI mode (``mode=ro&immutable=1``) keeps SQLite from
    creating ``-wal``/``-shm`` siblings next to the backup file when
    this verification runs.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        if not rows:
            return ""
        if len(rows) == 1 and rows[0][0] == "ok":
            return "ok"
        return "; ".join(r[0] for r in rows)
    finally:
        conn.close()


def _sidecar_for(target: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return target.with_name(f"{target.name}.pre-restore.{ts}")


def _wal_sibling(path: Path) -> Path:
    """Return ``<path>-wal`` (e.g. ``dashboard.db`` → ``dashboard.db-wal``)."""
    return path.with_name(path.name + "-wal")


def _shm_sibling(path: Path) -> Path:
    """Return ``<path>-shm`` (e.g. ``dashboard.db`` → ``dashboard.db-shm``)."""
    return path.with_name(path.name + "-shm")


def _move_wal_siblings(target: Path, sidecar: Path) -> None:
    """Move any ``-wal`` / ``-shm`` siblings of ``target`` next to ``sidecar``.

    SQLite associates WAL/SHM files with a DB by filesystem path, not by
    inode. If we rename ``target`` → ``sidecar`` but leave its ``-wal`` and
    ``-shm`` siblings sitting at the original location, the freshly-copied
    backup that lands at ``target`` inherits those orphan sidecars. On the
    next open SQLite finds the ``-wal`` adjacent to the restored DB and
    enters WAL recovery; frame checksums usually protect us, but the SQLite
    Backup API docs explicitly call out removing or invalidating
    destination ``-wal``/``-shm`` precisely because mismatched-but-plausible
    WAL frames can replay.

    We move them next to the sidecar so manual rollback still has the
    matched triplet available.
    """
    for old, new in (
        (_wal_sibling(target), _wal_sibling(sidecar)),
        (_shm_sibling(target), _shm_sibling(sidecar)),
    ):
        if old.exists():
            old.rename(new)


def _wal_checkpoint_truncate(path: Path) -> None:
    """Open ``path`` and run ``PRAGMA wal_checkpoint(TRUNCATE)``.

    Defensive belt-and-braces after the restore copy: if any stale
    ``-wal``/``-shm`` was left behind by something other than the rename
    above, the checkpoint materialises a clean state. Failure is silent —
    a fresh backup file will checkpoint to a no-op.
    """
    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.OperationalError:
        pass


def restore_database(
    backup_path: Path | str,
    target_path: Path | str | None = None,
    *,
    dry_run: bool = True,
    allow_open_db: bool = False,
) -> RestoreResult:
    """Restore ``target_path`` from ``backup_path``.

    Behaviour:
        * Verifies the backup file exists and passes ``PRAGMA integrity_check``.
        * ``dry_run=True`` (default): no filesystem mutation, returns the plan.
        * ``dry_run=False``: renames the current target to a sidecar path,
          copies the backup over the target, runs an integrity check on the
          freshly-copied target, and returns the actual paths used.

    The sidecar (``<target>.pre-restore.<ts>``) is the manual rollback
    surface. It is never auto-deleted.
    """
    backup = Path(backup_path)
    target = Path(target_path) if target_path is not None else DEFAULT_DB_PATH

    if not backup.exists():
        raise FileNotFoundError(f"Backup not found: {backup}")
    if not backup.is_file():
        raise ValueError(f"Backup path is not a regular file: {backup}")

    # Refuse to restore over a DB that's currently open in another process.
    # WAL/SHM existence is the cheap heuristic SQLite itself uses: their
    # presence proves a recent (or live) writer. Restoring under those
    # conditions would rename the live inode to a sidecar while the open
    # connection still writes to it — those writes are silently lost when
    # the user later "rolls back" by mv'ing the sidecar back.
    if not dry_run and not allow_open_db:
        wal = _wal_sibling(target)
        shm = _shm_sibling(target)
        if wal.exists() or shm.exists():
            raise RestoreBlockedByOpenDB(
                f"Refusing to restore: {wal.name} or {shm.name} exists, "
                "which suggests the dashboard (or another process) has the DB "
                "open. Stop the Streamlit app and any other DB clients, then "
                "re-run. To override this guard (advanced — you understand "
                "the risk), pass allow_open_db=True from the Python API."
            )

    check_result = _integrity_check(backup)
    if check_result != "ok":
        raise RestoreIntegrityError(
            f"PRAGMA integrity_check on {backup} returned {check_result!r} (expected 'ok'). "
            "Refusing to restore from a corrupt backup."
        )

    if dry_run:
        # Don't synthesise a sidecar_path for the dry-run result. The
        # actual `--confirm` run picks its sidecar at the moment of
        # rename (fresh datetime.now()), so any path we returned here
        # would not match the one created later — and a user following
        # `mv <displayed-sidecar> <target>` would hit "no such file".
        return RestoreResult(
            backup_path=backup,
            target_path=target,
            sidecar_path=None,
            dry_run=True,
            integrity_check_passed=True,
        )

    sidecar: Path | None = None
    if target.exists():
        sidecar = _sidecar_for(target)
        target.rename(sidecar)
        # SQLite binds WAL/SHM to a DB by filesystem path. Move them with
        # the renamed DB so the freshly-copied backup doesn't inherit
        # orphan sidecars that could replay during WAL recovery on the
        # next open.
        _move_wal_siblings(target, sidecar)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    # Defensive: force a clean checkpoint of the restored file so any
    # stray ``-wal`` left behind by something other than the rename above
    # is materialised into the main file and the WAL is truncated.
    _wal_checkpoint_truncate(target)

    # Verify the restored file is still readable. If this fails we leave the
    # sidecar in place so a human can revert by renaming it back.
    check_after = _integrity_check(target)
    if check_after != "ok":
        raise RestoreIntegrityError(
            f"Restore copy failed integrity check: {check_after!r}. "
            f"Original target was moved to {sidecar}; you can revert by renaming it back."
        )

    return RestoreResult(
        backup_path=backup,
        target_path=target,
        sidecar_path=sidecar,
        dry_run=False,
        integrity_check_passed=True,
    )


def _print_plan(result: RestoreResult) -> None:
    if result.dry_run:
        print("Dry-run — no changes made.")
    else:
        print("Restore complete.")
    print(f"  backup:      {result.backup_path}")
    print(f"  target:      {result.target_path}")
    if result.dry_run:
        # Don't print a predicted sidecar path — the real --confirm run
        # generates a fresh timestamp at rename time, so any path printed
        # here would not match the one actually created. Describe the
        # naming pattern instead.
        if result.target_path.exists():
            print(
                "  sidecar:     <target>.pre-restore.<timestamp>  (created on --confirm)"
            )
        else:
            print("  sidecar:     (target does not exist; no sidecar will be created)")
    else:
        print(
            f"  sidecar:     {result.sidecar_path if result.sidecar_path else '(target did not exist; no sidecar created)'}"
        )
    print(f"  integrity:   {'ok' if result.integrity_check_passed else 'FAILED'}")
    if result.dry_run:
        print()
        print("Re-run with --confirm to perform the restore.")
    else:
        print()
        print("Recovery instruction:")
        if result.sidecar_path is not None:
            print("  If the restored DB misbehaves, restore the previous file by running:")
            print(f"      mv {result.sidecar_path} {result.target_path}")
        else:
            print("  The previous DB did not exist; no rollback path was needed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore the X Growth Dashboard SQLite DB from a VACUUM INTO backup.",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        required=True,
        help="Path to the backup .db file produced by scripts/backup_db.py.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=f"Target DB path (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Perform the restore. Without this flag the command is a dry-run.",
    )
    parser.add_argument(
        "--allow-open-db",
        action="store_true",
        help=(
            "Skip the live-DB guard. By default, --confirm refuses to "
            "restore when <target>-wal/<target>-shm exist (strong evidence "
            "Streamlit or another process has the DB open). Pass this to "
            "override — you must be sure no process is writing."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = restore_database(
            backup_path=args.backup,
            target_path=args.target,
            dry_run=not args.confirm,
            allow_open_db=args.allow_open_db,
        )
    except (
        FileNotFoundError,
        ValueError,
        RestoreIntegrityError,
        RestoreBlockedByOpenDB,
        OSError,
        shutil.Error,
    ) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    _print_plan(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
