"""SQLite connection wrapper for the X Growth Dashboard.

See spec.md §10 (data model) and §11 (computed views). The single
responsibility of this module is to provide connections that:

1. Have ``PRAGMA foreign_keys = ON`` (SQLite defaults this OFF; missing it
   silently disables every FK declaration in 001_initial.sql).
2. Use ``PRAGMA journal_mode = WAL`` for safer concurrent reads.
3. Have the ``percentile(value, p)`` user-defined aggregate registered so
   ``v_lane_performance`` can compute medians and IQR bounds.

The Streamlit-side helper ``get_st_connection`` is included for forward
compatibility with later phases; Phase 1 itself does not exercise it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH: Path = PROJECT_ROOT / "data" / "dashboard.db"
MIGRATIONS_DIR: Path = PROJECT_ROOT / "migrations"


class _PercentileAggregate:
    """User-defined aggregate: linear-interpolation percentile.

    Mirrors the PERCENTILE_CONT(p) semantics used by Postgres and SQL:2003.
    NULL ``value`` rows are ignored (matching SQL aggregate semantics).
    """

    def __init__(self) -> None:
        self._values: list[float] = []
        self._p: float | None = None

    def step(self, value, p) -> None:  # pragma: no cover - trivial
        # Capture p first so finalize() still knows which percentile was
        # requested even if every value row is NULL (returns None, correctly).
        if self._p is None and p is not None:
            self._p = float(p)
        if value is None:
            return
        try:
            self._values.append(float(value))
        except (TypeError, ValueError):
            return

    def finalize(self):
        if not self._values or self._p is None:
            return None
        s = sorted(self._values)
        n = len(s)
        if n == 1:
            return s[0]
        rank = self._p * (n - 1)
        lo = int(rank)
        hi = lo + 1
        if hi >= n:
            return s[lo]
        frac = rank - lo
        return s[lo] + frac * (s[hi] - s[lo])


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a sqlite3 connection with the project's standing pragmas + aggregates.

    The DB file's parent directory is created on demand so callers can pass a
    path in a fresh data/ subtree without first running ``mkdir``.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.create_aggregate("percentile", 2, _PercentileAggregate)
    return conn


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename       TEXT PRIMARY KEY,
            applied_at_utc TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _iter_migration_files(migrations_dir: Path) -> Iterable[Path]:
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply every ``.sql`` file in ``migrations_dir`` once, in lex order.

    Records applied filenames in ``schema_migrations``. Returns the list of
    filenames newly applied during this call (empty if everything was already
    applied).
    """
    directory = Path(migrations_dir) if migrations_dir is not None else MIGRATIONS_DIR
    _ensure_schema_migrations_table(conn)
    already_applied = {
        row[0]
        for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    newly_applied: list[str] = []
    for path in _iter_migration_files(directory):
        if path.name in already_applied:
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript() commits any open transaction before running, so
        # wrapping in BEGIN/COMMIT here is incompatible. Migrations rely on
        # CREATE TABLE/VIEW IF NOT EXISTS for partial-failure recovery: if a
        # script aborts mid-stream, the schema_migrations row is NOT inserted
        # and the next run re-applies the file idempotently.
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,)
        )
        newly_applied.append(path.name)
    return newly_applied


def get_st_connection():  # pragma: no cover - exercised in later phases
    """Return a Streamlit ``st.connection("dashboard", type="sql")`` handle.

    Phase 1 does not consume this; pages added in Phase 3 will. The pragmas
    and aggregate registration happen on the engine ``connect`` event so
    SQLAlchemy-managed connections behave identically to the raw factory.
    """
    import streamlit as st
    from sqlalchemy import event

    conn = st.connection("dashboard", type="sql", url=f"sqlite:///{DEFAULT_DB_PATH}")
    # Use the public SQLConnection.engine accessor; the private _instance.engine
    # path may break across Streamlit upgrades.
    engine = conn.engine

    @event.listens_for(engine, "connect")
    def _setup(dbapi_conn, _connection_record):
        dbapi_conn.execute("PRAGMA foreign_keys = ON;")
        dbapi_conn.execute("PRAGMA journal_mode = WAL;")
        dbapi_conn.create_aggregate("percentile", 2, _PercentileAggregate)

    return conn
