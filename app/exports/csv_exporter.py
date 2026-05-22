"""Per-table CSV exporter — spec.md §16, project CLAUDE.md.

This module is a thin walker over :mod:`app.exports.allowlists`. The allowlist
decides what gets exported; the exporter just writes the bytes and logs the
run.

The CLI shim at the bottom (``python -m app.exports.csv_exporter``) is
documented in the Phase 5 acceptance gates. Streamlit pages import
``export_table_to_csv`` directly.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db import DEFAULT_DB_PATH, PROJECT_ROOT, apply_migrations, connect
from app.exports._audit import EXPORT_KIND_CSV, record_export
from app.exports._sql import quote_identifier
from app.exports.allowlists import (
    ALLOWLISTS,
    UnknownTableError,
    columns_for_export,
)


@dataclass(frozen=True)
class CsvExportResult:
    """Outcome of a single :func:`export_table_to_csv` invocation."""

    path: Path
    table_name: str
    row_count: int
    columns: list[str]
    include_opt_in: bool


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _anchor_on_project_root(path: Path) -> Path:
    """Thin alias for :func:`app.exports._audit.anchor_on_project_root`.

    Hoisted in P58R-31; the prior per-exporter copies (csv / markdown /
    json) all returned `PROJECT_ROOT / path` for relative inputs.
    """
    from app.exports._audit import anchor_on_project_root
    return anchor_on_project_root(path)


def _quote_identifier(name: str) -> str:
    """Backward-compatible alias for :func:`app.exports._sql.quote_identifier`."""
    return quote_identifier(name)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def export_table_to_csv(
    table_name: str,
    output_path: str | Path,
    *,
    include_opt_in: bool = False,
    conn: sqlite3.Connection | None = None,
    db_path: str | Path | None = None,
) -> CsvExportResult:
    """Export one table to a UTF-8 CSV file via the column allowlist.

    Parameters
    ----------
    table_name
        Key into :data:`app.exports.allowlists.ALLOWLISTS`.
    output_path
        Destination file. Created if absent; overwritten if present. Parent
        directory is created on demand.

        Relative paths anchor on ``PROJECT_ROOT`` (not the current working
        directory), matching ``app.backup``. This differs from Python's
        usual "relative means relative to CWD" convention — a CLI caller
        running ``python -m app.exports.csv_exporter --output foo.csv``
        from ``/tmp`` will see the file land at ``<project>/foo.csv``,
        not ``/tmp/foo.csv``. Pass an absolute path if you want CWD
        semantics.
    include_opt_in
        When True, appends ``opt_in_columns`` after ``default_columns`` in
        both the header and each row. Phase 5 ships with every table's
        ``opt_in_columns`` empty, so this flag is documentary at MVP and
        becomes load-bearing in Phase 5.5.
    conn
        Optional pre-opened connection. When None, opens a fresh one via
        :func:`app.db.connect` and closes it on return.
    db_path
        Optional override for the source DB path (only used when ``conn``
        is None). Defaults to :data:`app.db.DEFAULT_DB_PATH`.

    Returns
    -------
    CsvExportResult
        path, table, row count, column list, opt-in flag.

    Raises
    ------
    UnknownTableError
        ``table_name`` is not in the allowlist registry.
    ValueError
        Allowlist is internally inconsistent (column in both inclusion and
        excluded lists).
    """
    if table_name not in ALLOWLISTS:
        raise UnknownTableError(table_name)

    target = _anchor_on_project_root(Path(output_path))
    target.parent.mkdir(parents=True, exist_ok=True)

    columns = columns_for_export(table_name, include_opt_in=include_opt_in)
    select_cols = ", ".join(_quote_identifier(c) for c in columns)
    quoted_table = _quote_identifier(table_name)
    sql = f"SELECT {select_cols} FROM {quoted_table}"

    own_conn = conn is None
    active = conn if conn is not None else connect(db_path)
    try:
        if own_conn:
            # Defensive: if we opened our own connection, make sure the DB
            # has the data_exports table by applying migrations. Idempotent.
            apply_migrations(active)
        if not _table_exists(active, table_name):
            raise UnknownTableError(table_name)

        # /review-2 W7: iterate the cursor row-by-row instead of
        # materialising every row up front. post_metric_snapshots and
        # raw_api_responses are designed to grow unbounded; fetchall()
        # would force the whole table into memory before writing a
        # single byte to disk. Streaming keeps the writer O(1).
        cursor = active.execute(sql)
        row_count = 0

        # newline="" per the csv module docs to avoid blank lines on Windows
        # — even though this is a macOS-only project, the rule is harmless.
        with target.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for row in cursor:
                # sqlite3.Row supports indexing by name OR position. Use
                # position-by-column-name so the row order in CSV exactly
                # matches the header order.
                writer.writerow([row[col] for col in columns])
                row_count += 1

        record_export(
            active,
            kind=EXPORT_KIND_CSV,
            table_name=table_name,
            output_path=target,
            row_count=row_count,
            include_opt_in=include_opt_in,
        )
    finally:
        if own_conn:
            active.close()

    return CsvExportResult(
        path=target,
        table_name=table_name,
        row_count=row_count,
        columns=columns,
        include_opt_in=include_opt_in,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m app.exports.csv_exporter --table posts --output ... [--opt-in]``.

    Used by the Phase 5 acceptance gate. Streamlit pages do NOT shell out to
    this; they import :func:`export_table_to_csv` directly.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Export one table to CSV via the column allowlist.",
    )
    parser.add_argument(
        "--table",
        required=True,
        choices=sorted(ALLOWLISTS.keys()),
        help="Table name from app/exports/allowlists.ALLOWLISTS.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output CSV path. Relative paths anchor on the project root.",
    )
    parser.add_argument(
        "--opt-in",
        action="store_true",
        help="Include opt_in_columns alongside default_columns.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=f"Source DB path. Defaults to {DEFAULT_DB_PATH}.",
    )

    args = parser.parse_args(argv)
    result = export_table_to_csv(
        args.table,
        args.output,
        include_opt_in=args.opt_in,
        db_path=args.db_path,
    )
    print(
        f"CSV export · {result.table_name} → {result.path} "
        f"({result.row_count} rows, {len(result.columns)} columns, "
        f"opt_in={'on' if result.include_opt_in else 'off'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
