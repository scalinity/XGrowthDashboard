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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


@contextmanager
def _chdir(path: Path):
    prev = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(prev)

from app.backup import BACKUP_FILENAME_GLOB, _pick_target_path, backup_database
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
    # W6 regression — dry-run must NOT advertise a sidecar path; the real
    # --confirm run picks a fresh timestamp, so any predicted path would
    # mismatch and break the printed rollback instruction.
    assert result.sidecar_path is None, (
        f"Dry-run must not predict a sidecar path; got {result.sidecar_path}"
    )


# ---------------------------------------------------------------------------
# 5. Confirmed restore moves the previous target to a timestamped sidecar.
# ---------------------------------------------------------------------------

def test_restore_moves_wal_and_shm_siblings_to_sidecar(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    """W1 regression — restore must move -wal/-shm next to the sidecar, not
    leave them at the original target path where the restored backup would
    inherit them and trigger spurious WAL recovery on next open.
    """
    db_conn.close()
    backup = backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )

    # Fake leftover WAL/SHM next to the live DB. (The real Streamlit session
    # would have its own pair; we synthesize them deterministically.)
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    wal.write_bytes(b"\x00" * 32)
    shm.write_bytes(b"\x00" * 32)

    result = restore_database(
        backup_path=backup.path,
        target_path=db_path,
        dry_run=False,
    )

    assert result.sidecar_path is not None
    assert not wal.exists(), "WAL must not remain at the live-DB path"
    assert not shm.exists(), "SHM must not remain at the live-DB path"
    assert result.sidecar_path.with_name(result.sidecar_path.name + "-wal").exists(), (
        "WAL should have been renamed alongside the sidecar"
    )
    assert result.sidecar_path.with_name(result.sidecar_path.name + "-shm").exists(), (
        "SHM should have been renamed alongside the sidecar"
    )


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

def test_relative_backups_dir_anchored_on_project_root(
    db_conn: sqlite3.Connection,
    db_path: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """W3 regression — a relative backups_dir must NOT resolve against the
    process CWD. We monkeypatch PROJECT_ROOT to a tmp directory, set CWD
    to a *different* tmp directory, and pass a relative backups_dir. The
    backup must land under the patched PROJECT_ROOT, not under CWD.
    """
    db_conn.close()
    fake_root = tmp_path / "fake_project_root"
    fake_root.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.backup.PROJECT_ROOT", fake_root)

    with _chdir(elsewhere):
        result = backup_database(
            source_path=db_path,
            backups_dir=Path("rel_backups"),
            retention_days=30,
        )

    expected_dir = (fake_root / "rel_backups").resolve()
    assert result.path.parent == expected_dir, (
        f"Backup landed at {result.path.parent}, expected {expected_dir}. "
        "Relative backups_dir leaked into CWD-anchored resolution."
    )
    assert not (elsewhere / "rel_backups").exists(), (
        "Backup must not appear under CWD-relative path."
    )


def test_pick_target_path_falls_back_to_monotonic_suffix(
    tmp_path: Path, monkeypatch,
) -> None:
    """W5 regression — when every fresh second-precision filename is taken
    (e.g. clock pinned via monkeypatch), the picker must fall back to
    `-1`, `-2`, … suffixes within bounded retries instead of returning
    a colliding path or raising into VACUUM INTO.
    """
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    # Pin the filename generator so every time-retry produces the same
    # name and we exercise the suffix fallback deterministically.
    monkeypatch.setattr(
        "app.backup._backup_filename",
        lambda now=None: "x_growth_2026-05-21_210000.db",
    )
    # Make sleep() a no-op so the test doesn't spend FILENAME_TIME_RETRY_LIMIT
    # seconds in real wall time.
    monkeypatch.setattr("app.backup.time.sleep", lambda _s: None)

    # Pre-create the base filename and two suffix attempts.
    (backups_dir / "x_growth_2026-05-21_210000.db").write_bytes(b"")
    (backups_dir / "x_growth_2026-05-21_210000-1.db").write_bytes(b"")
    (backups_dir / "x_growth_2026-05-21_210000-2.db").write_bytes(b"")

    target = _pick_target_path(backups_dir)

    assert target.name == "x_growth_2026-05-21_210000-3.db", (
        f"Expected suffix fallback to -3; got {target.name}"
    )
    assert not target.exists(), "Picker must return a not-yet-existing path."


def test_restore_main_translates_oserror_to_structured_failure(
    db_conn: sqlite3.Connection,
    db_path: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """W7 regression — restore_db's main() must catch OSError (disk full,
    permission denied, cross-FS rename) and surface the structured
    "Restore failed: …" message + exit 1, rather than letting a bare
    Python stack trace escape to the CLI user.
    """
    db_conn.close()
    backup = backup_database(
        source_path=db_path,
        backups_dir=tmp_path / "backups",
        retention_days=30,
    )

    def _raise(*_args, **_kwargs):
        raise OSError(28, "Synthetic ENOSPC for the test")

    monkeypatch.setattr("scripts.restore_db.shutil.copy2", _raise)

    from scripts.restore_db import main

    rc = main([
        "--backup", str(backup.path),
        "--target", str(db_path),
        "--confirm",
    ])

    captured = capsys.readouterr()
    assert rc == 1, f"main() should return 1 on OSError; got {rc}"
    assert "Restore failed" in captured.err, (
        f"Expected structured 'Restore failed' on stderr; got: {captured.err!r}"
    )
    # The previous behaviour was a raw traceback — those don't start
    # with our prefix. This guards the failure shape.
    assert "Traceback" not in captured.err


def test_retention_zero_keeps_the_freshly_created_backup(
    db_conn: sqlite3.Connection, db_path: Path, tmp_path: Path
) -> None:
    """W2 regression — retention_days=0 must NOT delete the just-created
    backup, regardless of whether the prune threshold makes it appear old.
    The previous implementation treated 0 as "prune everything older than
    now()", which would unlink the file VACUUM INTO had just produced.
    """
    db_conn.close()
    backups_dir = tmp_path / "backups"

    result = backup_database(
        source_path=db_path,
        backups_dir=backups_dir,
        retention_days=0,
    )

    assert result.path.exists(), (
        "retention_days=0 must not delete the freshly-created backup"
    )
    assert result.pruned == [], "retention_days=0 should be a no-op prune"


def test_retention_prunes_old_backups(
    db_conn: sqlite3.Connection,
    db_path: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """W10 hardened: clock is monkeypatched so the retention threshold is
    deterministic. The test originally seeded "recent" / "old" files via
    os.utime against the system clock while _prune_old_backups re-read
    time.time() separately — harmless in practice but fragile-looking.
    Pinning time.time() to NOW removes that ambiguity.
    """
    NOW = 1_716_336_000.0  # fixed Unix timestamp, ~2024-05-22 (arbitrary)
    monkeypatch.setattr("app.backup.time.time", lambda: NOW)

    db_conn.close()
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Fake an old backup file and a recent backup file by writing then
    # rewinding mtime relative to the frozen NOW (so both samples are
    # captured against the same clock the prune sweep will use).
    old_file = backups_dir / "x_growth_2025-01-01_010101.db"
    old_file.write_bytes(b"dummy old backup contents")
    fortnight_ago = NOW - (14 * 86400)
    os.utime(old_file, (fortnight_ago, fortnight_ago))

    recent_file = backups_dir / "x_growth_2026-05-01_010101.db"
    recent_file.write_bytes(b"dummy recent backup contents")
    yesterday = NOW - 86400
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
