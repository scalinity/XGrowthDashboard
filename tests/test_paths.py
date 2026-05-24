"""Phase 11.1 tests for the data-path resolver + legacy-DB migration (§31.5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XGROWTH_DATA_DIR", raising=False)


def _appsupport(home: Path) -> Path:
    return home / "Library" / "Application Support" / paths.APP_NAME


def test_env_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XGROWTH_DATA_DIR", str(tmp_path / "custom"))
    assert paths.resolve_data_dir() == tmp_path / "custom"
    assert paths.resolve_db_path() == tmp_path / "custom" / paths.DB_FILENAME


def test_legacy_default_when_no_env_no_appsupport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    legacy = tmp_path / "legacy"
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)
    assert paths.resolve_data_dir() == legacy


def test_appsupport_used_once_its_db_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", tmp_path / "legacy")
    appsup = _appsupport(home)
    appsup.mkdir(parents=True)
    (appsup / paths.DB_FILENAME).write_text("")  # presence flips precedence
    assert paths.resolve_data_dir() == appsup


def test_migrate_copies_legacy_db_and_preserves_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)

    # Seed a real SQLite DB in the legacy location.
    src = sqlite3.connect(str(legacy / paths.DB_FILENAME))
    src.execute("CREATE TABLE t (x INTEGER)")
    src.execute("INSERT INTO t VALUES (42)")
    src.commit()
    src.close()

    target = paths.migrate_legacy_db_if_needed()
    assert target == _appsupport(home) / paths.DB_FILENAME
    assert target.exists()
    assert (legacy / paths.DB_FILENAME).exists()  # original preserved (copy, not move)

    dst = sqlite3.connect(str(target))
    assert dst.execute("SELECT x FROM t").fetchone()[0] == 42  # data intact
    dst.close()

    # After migration, the resolver points at Application Support.
    assert paths.resolve_data_dir() == _appsupport(home)


def test_migrate_is_noop_with_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XGROWTH_DATA_DIR", str(tmp_path / "custom"))
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / paths.DB_FILENAME).write_text("x")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)
    assert paths.migrate_legacy_db_if_needed() == tmp_path / "custom" / paths.DB_FILENAME


def test_migrate_is_noop_without_legacy_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", tmp_path / "nolegacy")
    paths.migrate_legacy_db_if_needed()
    assert not (_appsupport(home) / paths.DB_FILENAME).exists()
