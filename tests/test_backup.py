"""Phase 4 — backup and restore tests.

Covers the six acceptance scenarios called out in
``phase-4-backup-data-hygiene.md``:

1. ``backup_database`` actually creates a file at the expected place.
2. The file passes ``PRAGMA integrity_check`` when opened independently.
3. ``settings.last_backup_at_utc`` is updated after a successful backup.
4. ``restore_database(dry_run=True)`` does not touch the target.
5. ``restore_database(dry_run=False)`` moves the previous DB to a sidecar.
6. Retention pruning deletes files whose mtime is older than the window.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.backup import BACKUP_FILENAME_GLOB, backup_database
from app.forms import get_setting
from scripts.restore_db import restore_database


# ---------------------------------------------------------------------------
# 1. Backup creates a file at the expected place.
# ---------------------------------------------------------------------------

def test_backup_creates_file(db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path) -> None:
    db_conn.close()  # release WAL handles so VACUUM INTO can run cleanly.

    backups_dir = tmp_path / "backups"

    result = backup_database(
        source_path=db_path,
        backups_dir=backups_dir,
        retention_days=30,
    )

    assert result.path.exists(), "Backup file must exist on disk"
    assert result.path.parent == backups_dir.resolve(), (
        f"Backup landed in {result.path.parent}, expected {backups_dir.resolve()}"
    )
    assert result.size_bytes > 0, "Backup file must not be empty"
    assert result.integrity_check_passed is True
    assert result.path.name.startswith("x_growth_")
    assert result.path.name.endswith(".db")


# ---------------------------------------------------------------------------
# 2. Backup file passes integrity_check from an independent connection.
# ---------------------------------------------------------------------------

def test_backup_passes_integrity_check(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    db_conn.close()

    result = backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )

    # Open the backup with a fresh, vanilla connection (no aggregates / pragmas)
    # to prove the file is self-contained and uncorrupt.
    conn = sqlite3.connect(str(result.path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "ok", f"PRAGMA integrity_check returned {row[0]!r} (expected 'ok')"


# ---------------------------------------------------------------------------
# 3. Backup updates settings.last_backup_at_utc.
# ---------------------------------------------------------------------------

def test_backup_updates_last_backup_setting(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    # Pre-check: the seeded migration leaves last_backup_at_utc as JSON null.
    assert get_setting(db_conn, "last_backup_at_utc") is None
    db_conn.close()

    backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )

    # Re-open and read the row directly so we don't share connection state
    # with the script under test.
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = 'last_backup_at_utc'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    value = json.loads(row[0])
    assert isinstance(value, str), "last_backup_at_utc must be ISO-8601 string"
    assert value.endswith("Z"), f"expected UTC suffix, got {value!r}"
    # The timestamp should parse and be within a sensible window (<60s old).
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    assert -timedelta(seconds=5) <= delta <= timedelta(seconds=60), (
        f"last_backup_at_utc out of range: {value!r}"
    )


# ---------------------------------------------------------------------------
# 4. Dry-run restore does not mutate the target.
# ---------------------------------------------------------------------------

def test_restore_dry_run_does_not_touch_target(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    db_conn.close()

    backup = backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )

    target_mtime_before = db_path.stat().st_mtime
    target_size_before = db_path.stat().st_size

    result = restore_database(
        backup_path=backup.path,
        target_path=db_path,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.integrity_check_passed is True
    assert db_path.stat().st_mtime == target_mtime_before
    assert db_path.stat().st_size == target_size_before
    # No sidecar should have been created during dry-run.
    sidecars = list(db_path.parent.glob(f"{db_path.name}.pre-restore.*"))
    assert sidecars == [], f"Dry-run must not create sidecars; found {sidecars}"


# ---------------------------------------------------------------------------
# 5. Confirmed restore moves the previous target to a timestamped sidecar.
# ---------------------------------------------------------------------------

def test_restore_with_confirm_moves_old_to_sidecar(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    db_conn.close()

    backup = backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )
    pre_restore_size = db_path.stat().st_size

    result = restore_database(
        backup_path=backup.path,
        target_path=db_path,
        dry_run=False,
    )

    assert result.dry_run is False
    assert result.integrity_check_passed is True
    assert result.sidecar_path is not None
    assert result.sidecar_path.exists(), "Sidecar must still exist (manual rollback)."
    # Sidecar should hold the OLD content; target now matches the backup.
    assert result.sidecar_path.stat().st_size == pre_restore_size
    assert db_path.exists()
    # And the restored DB must still pass an integrity check.
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    assert row[0] == "ok"


# ---------------------------------------------------------------------------
# 6. Retention prunes files older than the window (mtime-based).
# ---------------------------------------------------------------------------

def test_retention_prunes_old_backups(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    db_conn.close()
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Fake an old backup file and a recent backup file by writing then
    # rewinding mtime via os.utime.
    old_file = backups_dir / "x_growth_2025-01-01_010101.db"
    old_file.write_bytes(b"dummy old backup contents")
    fortnight_ago = time.time() - (14 * 86400)
    os.utime(old_file, (fortnight_ago, fortnight_ago))

    recent_file = backups_dir / "x_growth_2026-05-01_010101.db"
    recent_file.write_bytes(b"dummy recent backup contents")
    yesterday = time.time() - 86400
    os.utime(recent_file, (yesterday, yesterday))

    # Run a real backup with a 7-day retention; pre-existing files older
    # than 7 days should be pruned, while the recent one survives along
    # with the freshly-created file.
    result = backup_database(
        source_path=db_path,
        backups_dir=backups_dir,
        retention_days=7,
    )

    assert old_file in result.pruned, f"Old file should be pruned: {result.pruned}"
    assert not old_file.exists(), "Pruned file should be removed from disk"
    assert recent_file.exists(), "Recent file (within retention) must remain"
    assert result.path.exists(), "Freshly-created backup must survive its own prune pass"

    remaining = sorted(backups_dir.glob(BACKUP_FILENAME_GLOB))
    assert recent_file in remaining
    assert result.path in remaining
