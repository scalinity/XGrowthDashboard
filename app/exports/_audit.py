"""Shared data_exports audit-row writer.

Hoisted out of the three exporters after /review-2 flagged the duplication
(DRY) AND the fact that the three local copies all swallowed every
``sqlite3.Error`` via ``warnings.warn`` — including CHECK-constraint
violations which signal a developer bug rather than a runtime data issue.

Two policy changes ride along with the consolidation:

1. **Kind values are constants, not magic strings.** ``EXPORT_KIND_CSV``
   etc. live here so the four call sites (three exporters + the
   migration's CHECK list) can all reference one name. A small schema
   test asserts the migration's CHECK list matches the constants.

2. **CHECK violations re-raise.** :class:`sqlite3.IntegrityError`
   surfaces loudly (a typo in ``kind`` or a future column-level CHECK
   trip is a programmer bug). Transient :class:`sqlite3.OperationalError`
   still warns and continues so an export with a broken DB doesn't lose
   its output file over an audit-row write failure.
"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# Single source of truth for ``data_exports.kind`` values. Mirror in
# ``migrations/004_data_exports.sql`` CHECK list.
EXPORT_KIND_CSV: str = "csv"
EXPORT_KIND_MARKDOWN_WEEKLY: str = "markdown_weekly"
EXPORT_KIND_JSON: str = "json"

EXPORT_KINDS: frozenset[str] = frozenset({
    EXPORT_KIND_CSV,
    EXPORT_KIND_MARKDOWN_WEEKLY,
    EXPORT_KIND_JSON,
})

ExportKind = Literal["csv", "markdown_weekly", "json"]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_export(
    conn: sqlite3.Connection,
    *,
    kind: ExportKind,
    output_path: Path,
    table_name: str | None = None,
    row_count: int | None = None,
    include_opt_in: bool | None = None,
    notes: str | None = None,
) -> None:
    """Insert one row into ``data_exports``.

    Constraint-violation failures (e.g. ``kind`` typos) re-raise so the
    caller surfaces a programmer bug loudly. Transient operational
    failures (locked DB, missing migration 004) emit a ``RuntimeWarning``
    and return — the export's primary obligation (file on disk) has
    already been met, and a broken DB shouldn't make us drop the file.
    """
    if kind not in EXPORT_KINDS:
        raise ValueError(
            f"Unknown export kind {kind!r}. Allowed: {sorted(EXPORT_KINDS)}."
        )
    try:
        conn.execute(
            """
            INSERT INTO data_exports
                (exported_at_utc, kind, table_name, output_path, row_count, include_opt_in, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_utc_iso(),
                kind,
                table_name,
                str(output_path),
                row_count,
                None if include_opt_in is None else int(include_opt_in),
                notes,
            ),
        )
    except sqlite3.IntegrityError:
        # CHECK violation = a developer bug (e.g. a typo in kind or a
        # future column-level constraint trip). Surface loudly.
        raise
    except sqlite3.OperationalError as exc:
        # Transient runtime failure — DB locked, table missing because
        # migration 004 hasn't been applied, etc. The export file is
        # already on disk; don't roll that back over an audit hiccup.
        warnings.warn(
            f"Failed to record data_exports row for {kind}/{table_name!r}: {exc}. "
            "Run `uv run python -m scripts.init_db` to ensure migration 004 has applied.",
            RuntimeWarning,
            stacklevel=2,
        )
