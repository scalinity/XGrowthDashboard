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
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

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


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Atomic transaction context manager — BEGIN IMMEDIATE / COMMIT / ROLLBACK.

    The project's ``connect()`` opens connections with ``isolation_level=None``
    (autocommit mode), so every individual statement commits on its own. Any
    multi-statement write that must be atomic (publish flow per §28.10, the
    save_draft_post / save_draft_reply / revise_draft chain on the agent
    side, any future schema-touching helper) MUST wrap its statements in
    ``with transaction(conn): ...``.

    On exception, the context manager issues ``ROLLBACK`` and re-raises so
    the caller can compose its own recovery (e.g. validation-failure path
    in publish.py opens a second, narrower transaction for the audit row
    + attempt-counter bump).

    ``BEGIN IMMEDIATE`` acquires the SQLite write lock at transaction start
    rather than at the first write, which matches the semantics callers
    expect — concurrent readers won't see partial state through the WAL.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


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


def _recover_half_rebuilt_tables(conn: sqlite3.Connection) -> list[str]:
    """P9R-1 retry-safety: complete any half-finished ALTER-TABLE rebuilds.

    Migrations that extend a CHECK constraint use the SQLite 12-step
    drop-and-recreate recipe: CREATE <table>_new → INSERT SELECT →
    DROP <table> → ALTER RENAME. If the process is interrupted between
    DROP and ALTER, the original table is missing and the data sits in
    <table>_new. On the next ``apply_migrations`` call the migration
    file would re-run from the top; its defensive
    ``DROP TABLE IF EXISTS <table>_new`` would then destroy the only
    surviving copy.

    This helper runs BEFORE any migration is executescript()'d. For each
    known half-rebuild state it detects, it completes the prior run by
    ALTER-RENAMing ``<table>_new`` back into place, then logs the
    recovery to ``audit_logs``. The subsequent migration replays its
    full rebuild against the (now-restored) original table; idempotent
    CHECK constraints + IF NOT EXISTS guards make the redo a no-op or
    a clean re-rebuild.

    Returns the list of recovered table names (empty when nothing to do).
    """
    known_rebuilds = ("reply_targets",)
    recovered: list[str] = []
    for table in known_rebuilds:
        new_name = f"{table}_new"
        row_new = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (new_name,),
        ).fetchone()
        row_orig = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row_new is not None and row_orig is None:
            # Crash state. Recover by completing the rename. FKs must be
            # OFF for the ALTER to succeed cleanly without re-validating
            # incoming references; we restore the prior PRAGMA value.
            prior_fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute(f"ALTER TABLE {new_name} RENAME TO {table}")
            finally:
                conn.execute(f"PRAGMA foreign_keys = {prior_fk}")
            recovered.append(table)
            # Audit row — best-effort; audit_logs may not yet exist on a
            # pre-migration-015 DB. Don't let a missing table block recovery.
            try:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (event_category, event_type, target_type, target_id,
                         details_json, success, error_message)
                    VALUES ('migration',
                            'migration_crash_recovery_completed',
                            'migration',
                            ?,
                            ?,
                            1,
                            NULL)
                    """,
                    (
                        table,
                        '{"table":"' + table + '","action":"renamed_'
                        + new_name + '_to_' + table + '"}',
                    ),
                )
            except sqlite3.OperationalError:
                pass
    return recovered


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply every ``.sql`` file in ``migrations_dir`` once, in lex order.

    Records applied filenames in ``schema_migrations``. Returns the list of
    filenames newly applied during this call (empty if everything was already
    applied).

    P9R-1 retry-safety: before applying any migration, ``_recover_half_
    rebuilt_tables`` detects and completes any prior crash mid-rebuild
    (CHECK-constraint extension via DROP/CREATE/RENAME), preventing the
    re-run's defensive ``DROP TABLE IF EXISTS …_new`` from destroying the
    only surviving copy of the data.
    """
    directory = Path(migrations_dir) if migrations_dir is not None else MIGRATIONS_DIR
    _ensure_schema_migrations_table(conn)
    _recover_half_rebuilt_tables(conn)
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
        # and the next run re-applies the file idempotently. P9R-1 adds a
        # pre-flight recovery for known DROP/RENAME crash states.
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
