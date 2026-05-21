"""Shared pytest fixtures for the X Growth Dashboard tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Make the project root importable so `import app` and `import scripts.*` work.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import apply_migrations, connect  # noqa: E402
from scripts.seed_milestones import seed_milestones  # noqa: E402
from scripts.seed_settings import seed_settings  # noqa: E402


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_dashboard.db"


@pytest.fixture
def db_conn(db_path: Path) -> sqlite3.Connection:
    """Fresh DB with migrations applied and seeds in place."""
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    seed_milestones(conn)
    yield conn
    conn.close()


@pytest.fixture
def empty_db_conn(db_path: Path) -> sqlite3.Connection:
    """Fresh DB with migrations applied but no seeds."""
    conn = connect(db_path)
    apply_migrations(conn)
    yield conn
    conn.close()
