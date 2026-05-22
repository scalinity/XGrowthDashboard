"""Raw JSON archive exporter — spec.md §16, §18.

Dumps every table in the project's domain schema to a single JSON document
keyed by table name. Redacts column names that match secret-like patterns
(``*_token``, ``*_key``, ``*_secret``) and walks the JSON-blob columns
``raw_api_responses.response_json`` / ``request_params_json`` to redact any
HTTP ``Authorization`` / ``X-API-Key`` style headers that may have been
captured before §18 rules were tightened.

Output schema
-------------

::

    {
        "schema_version": 1,
        "exported_at_utc": "2026-05-21T22:00:00Z",
        "db_schema_migrations_applied": ["001_initial.sql", "002_views.sql", ...],
        "redactions": {
            "table.column": "redacted because <reason>",
            ...
        },
        "tables": {
            "<table_name>": [
                {"<col>": <value>, ...},
                ...
            ],
            ...
        }
    }

Tables included
---------------

Every table that this MVP creates (per ``migrations/001_initial.sql`` +
``003_backup_settings.sql`` + ``004_data_exports.sql``), with one important
carve-out: ``stir_testers`` and ``stir_conversion_events.qualitative_feedback``
are excluded by default because they hold tester PII per §18 rules 4-6. The
``--include-stir-pii`` flag opts them back in for archival snapshots that
will live only on Daniel's machine.

The CSV exporter's allowlist does NOT govern this dump — that allowlist
expresses what's safe to share offline; the JSON dump is the archival
form and includes every table the redaction layer judges safe.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.db import DEFAULT_DB_PATH, PROJECT_ROOT, apply_migrations, connect
from app.exports._audit import EXPORT_KIND_JSON, record_export
from app.exports._sql import quote_identifier

JSON_SCHEMA_VERSION: int = 1

# Tables in MVP scope. The JSON dump intentionally omits
# ``schema_migrations`` (operational bookkeeping covered by
# db_schema_migrations_applied at the top level) and ``account_snapshot_corrections``
# (Phase 3 surface; deferred to a future export-corrections phase).
_DEFAULT_TABLES: tuple[str, ...] = (
    "settings",
    "account_snapshots",
    "raw_api_responses",
    "posts",
    "post_metric_snapshots",
    "post_classifications",
    "daily_activity",
    "reply_sessions",
    "stir_conversion_events",
    "milestones",
    "weekly_reviews",
    "experiments",
    "data_exports",
)

# Tables and per-table columns gated behind the PII opt-in flag.
_PII_GATED_TABLES: frozenset[str] = frozenset({"stir_testers"})
_PII_GATED_COLUMNS: dict[str, frozenset[str]] = {
    "stir_conversion_events": frozenset({"qualitative_feedback"}),
}

# Column-name patterns that always get redacted regardless of table.
# Compiled at module import time so the per-row check is cheap.
_SECRET_COLUMN_PATTERN: re.Pattern[str] = re.compile(
    r"(_token|_key|_secret|_password|_credential)s?$",
    re.IGNORECASE,
)

# JSON header keys (case-insensitive) that should be redacted when walking
# nested response_json / request_params_json blobs from raw_api_responses.
_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset({
    "authorization",
    "x-api-key",
    "x-auth-token",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-amz-security-token",
})

# Top-level JSON keys (case-insensitive) inside raw_api_responses payloads
# that may contain a flat secret string rather than a header object. Only
# matched at depth 0 of the response_json / request_params_json blob, since
# at deeper nesting a key like "password" is plausibly user-content (e.g. a
# tester quoting a UI element) rather than an actual secret.
_SENSITIVE_TOP_KEYS: frozenset[str] = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "bearer_token",
    "client_secret",
    "consumer_secret",
    "consumer_key",
    "oauth_token",
    "oauth_token_secret",
    "api_key",
    "api_secret",
    "private_key",
    "password",
})

# Parent-key names that signal "the dict I'm about to walk is an HTTP
# headers map; redact Authorization-style keys within it". Case-insensitive
# match. Restricting header-name redaction to these contexts avoids
# false-positives where a user-content field happens to be called e.g.
# "cookie" or "authorization" outside an HTTP-request shape.
_HEADER_PARENT_KEYS: frozenset[str] = frozenset({
    "headers",
    "request_headers",
    "response_headers",
})

_REDACTED_SENTINEL: str = "[REDACTED]"


@dataclass
class JsonExportResult:
    """Outcome of a single :func:`export_database_to_json` invocation."""

    path: Path
    table_row_counts: dict[str, int]
    redactions: dict[str, str] = field(default_factory=dict)


def _anchor_on_project_root(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _is_secret_column(column_name: str) -> bool:
    return bool(_SECRET_COLUMN_PATTERN.search(column_name))


def _redact_json_blob(
    payload: object,
    *,
    depth: int = 0,
    inside_headers: bool = False,
) -> tuple[object, bool]:
    """Walk a JSON-decoded blob and redact secret-looking pieces.

    Returns ``(redacted_payload, was_redacted)``. The boolean lets the caller
    record which (table, column) chains had to be redacted at all, so the
    output document is self-describing.

    Two scopes — both narrower than the previous "match in either set at
    any depth" rule (/review-2 🔵 S6):

    1. :data:`_SENSITIVE_TOP_KEYS` fires ONLY at ``depth == 0`` of the
       captured response/request blob (the raw_api_responses payload's
       top level). A deeper "password" key is plausibly user-content
       and is left alone.
    2. :data:`_SENSITIVE_HEADER_NAMES` fires ONLY when we are inside a
       dict whose parent key matched :data:`_HEADER_PARENT_KEYS`
       (``headers`` / ``request_headers`` / ``response_headers``). An
       Authorization-named key sitting outside an HTTP headers context
       is also plausibly user-content.

    No regex match on the value text — header values can be high-entropy
    arbitrary strings, and matching by *key* is the safer rule.
    """
    if isinstance(payload, dict):
        out: dict[str, object] = {}
        was = False
        for k, v in payload.items():
            k_lower = k.lower() if isinstance(k, str) else ""
            if depth == 0 and k_lower in _SENSITIVE_TOP_KEYS:
                out[k] = _REDACTED_SENTINEL
                was = True
                continue
            if inside_headers and k_lower in _SENSITIVE_HEADER_NAMES:
                out[k] = _REDACTED_SENTINEL
                was = True
                continue
            child, child_was = _redact_json_blob(
                v,
                depth=depth + 1,
                inside_headers=k_lower in _HEADER_PARENT_KEYS,
            )
            out[k] = child
            was = was or child_was
        return out, was
    if isinstance(payload, list):
        new_list: list[object] = []
        was = False
        for item in payload:
            child, child_was = _redact_json_blob(
                item,
                depth=depth + 1,
                inside_headers=inside_headers,
            )
            new_list.append(child)
            was = was or child_was
        return new_list, was
    return payload, False


def _normalise_row(
    row: sqlite3.Row,
    *,
    table_name: str,
    columns: list[str],
    pii_columns: frozenset[str],
    include_pii: bool,
    redactions: dict[str, str],
) -> dict[str, object]:
    """Convert a sqlite3.Row to a plain dict, applying redactions.

    Logic per cell:
    1. If the column matches the secret-name pattern → replace value with
       ``_REDACTED_SENTINEL`` and record the (table.column, reason).
    2. If the table is the special-case ``raw_api_responses`` and the column
       is one of the JSON-blob columns, attempt to ``json.loads`` and walk
       for sensitive header keys; on success replace the stringified blob
       with the redacted version.
    3. If the column is PII-gated and ``include_pii`` is False → redact.
    4. Otherwise pass through.
    """
    out: dict[str, object] = {}
    for col in columns:
        value = row[col]
        cell_path = f"{table_name}.{col}"
        if _is_secret_column(col):
            out[col] = _REDACTED_SENTINEL
            redactions.setdefault(cell_path, f"column name matches /{_SECRET_COLUMN_PATTERN.pattern}/")
            continue
        if col in pii_columns and not include_pii:
            out[col] = _REDACTED_SENTINEL
            redactions.setdefault(cell_path, "tester PII; pass --include-stir-pii to opt in")
            continue
        if table_name == "settings" and col == "value_json":
            # The generic (key, value_json) shape of the settings table
            # escapes the column-name regex above. Apply the same regex
            # against the row's `key` so a future settings row like
            # `anthropic_api_key` or `x_oauth_bearer_token` still gets
            # redacted in the dump even though `value_json` itself is
            # not a sensitive name.
            key_value = row["key"] if "key" in columns else ""
            if isinstance(key_value, str) and _is_secret_column(key_value):
                out[col] = _REDACTED_SENTINEL
                redactions.setdefault(
                    f"settings[{key_value}].value_json",
                    f"settings.key {key_value!r} matches /{_SECRET_COLUMN_PATTERN.pattern}/",
                )
                continue
        if (
            table_name == "raw_api_responses"
            and col in {"response_json", "request_params_json"}
            and isinstance(value, str)
            and value.strip()
        ):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                # Fail closed. raw_api_responses payloads may contain
                # captured Authorization-style text; a non-JSON payload
                # (xurl transcript, partial response, error body stored
                # as text) would otherwise bypass _redact_json_blob and
                # land cleartext in the export. The column is TEXT NOT
                # NULL with no JSON-validity constraint, so this branch
                # is reachable from any non-JSON write path.
                out[col] = _REDACTED_SENTINEL
                redactions.setdefault(
                    cell_path,
                    "raw_api_responses payload was not valid JSON; "
                    "redacted to avoid leaking captured Authorization-style text",
                )
                continue
            redacted, was = _redact_json_blob(decoded)
            if was:
                redactions.setdefault(cell_path, "redacted Authorization-like header(s)")
            out[col] = redacted
            continue
        out[col] = value
    return out


def _list_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    """Return the ordered list of columns for an existing table.

    Uses ``PRAGMA table_info`` so the schema discovery survives any future
    column reordering. PRAGMA does not accept bound parameters, so the
    table name is inlined as a quoted identifier via
    :func:`app.exports._sql.quote_identifier`. Callers (currently only
    :func:`export_database_to_json`) are responsible for confirming the
    table exists in ``sqlite_master`` first via :func:`_table_exists`;
    this function does NOT re-check.
    """
    rows = conn.execute(
        f"PRAGMA table_info({quote_identifier(table_name)})"
    ).fetchall()
    return [r["name"] for r in rows]


def export_database_to_json(
    output_path: str | Path,
    *,
    include_stir_pii: bool = False,
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
    pretty: bool = True,
) -> JsonExportResult:
    """Dump every supported table to a single JSON document.

    Parameters
    ----------
    output_path
        Destination file. Parent directory created on demand. Relative paths
        anchor on ``PROJECT_ROOT``.
    include_stir_pii
        When True, includes ``stir_testers`` and the PII-gated columns of
        ``stir_conversion_events``. Default False per §18.
    db_path
        Source DB. Defaults to :data:`app.db.DEFAULT_DB_PATH`.
    conn
        Pre-opened connection (overrides ``db_path``).
    pretty
        2-space indent when True; compact when False. The Phase 5 prompt
        suggests minified output as a future option if size exceeds 100MB;
        for MVP-scale data, pretty makes manual inspection easier.

    Notes
    -----
    There is intentionally no ``redact_secrets`` opt-out. Previous revisions
    exposed one, but the failure mode was silent (a caller passing
    ``redact_secrets=False`` would dump PII + Authorization-bearing
    ``raw_api_responses`` blobs verbatim). Redaction is now always-on; tests
    that need to verify the raw row shape call :func:`_normalise_row` directly
    rather than going through this public entry point.
    """
    target = _anchor_on_project_root(Path(output_path))
    target.parent.mkdir(parents=True, exist_ok=True)

    own_conn = conn is None
    active = conn if conn is not None else connect(db_path)
    try:
        if own_conn:
            apply_migrations(active)

        tables_to_export: list[str] = list(_DEFAULT_TABLES)
        if include_stir_pii:
            tables_to_export.extend(t for t in _PII_GATED_TABLES if t not in tables_to_export)

        redactions: dict[str, str] = {}
        if not include_stir_pii:
            for t in _PII_GATED_TABLES:
                redactions[t] = "tester PII; pass --include-stir-pii to opt in"

        applied = [
            r["filename"]
            for r in active.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename ASC"
            ).fetchall()
        ]

        tables_payload: dict[str, list[dict[str, object]]] = {}
        row_counts: dict[str, int] = {}
        for table_name in tables_to_export:
            if not _table_exists(active, table_name):
                continue
            columns = _list_columns(active, table_name)
            select_cols = ", ".join(quote_identifier(c) for c in columns)
            rows = active.execute(f"SELECT {select_cols} FROM {quote_identifier(table_name)}").fetchall()
            pii_cols = _PII_GATED_COLUMNS.get(table_name, frozenset())
            normalised = [
                _normalise_row(
                    r,
                    table_name=table_name,
                    columns=columns,
                    pii_columns=pii_cols,
                    include_pii=include_stir_pii,
                    redactions=redactions,
                )
                for r in rows
            ]
            tables_payload[table_name] = normalised
            row_counts[table_name] = len(rows)

        document: dict[str, object] = {
            "schema_version": JSON_SCHEMA_VERSION,
            "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "db_schema_migrations_applied": applied,
            "redactions": redactions,
            "tables": tables_payload,
        }

        encoded = json.dumps(
            document,
            indent=2 if pretty else None,
            ensure_ascii=False,
            default=str,
        )
        target.write_text(encoded, encoding="utf-8")

        record_export(
            active,
            kind=EXPORT_KIND_JSON,
            output_path=target,
            row_count=sum(row_counts.values()),
        )
    finally:
        if own_conn:
            active.close()

    return JsonExportResult(
        path=target,
        table_row_counts=row_counts,
        redactions=redactions,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m app.exports.json_exporter --output <path> [--include-stir-pii]``.

    Always redacts secrets. The unredacted form is reachable only from
    Python tests; there is no CLI flag for it.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Export every supported table to a single JSON archive.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON path. Relative paths anchor on the project root.",
    )
    parser.add_argument(
        "--include-stir-pii",
        action="store_true",
        help="Include stir_testers and stir_conversion_events.qualitative_feedback.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=f"Source DB path. Defaults to {DEFAULT_DB_PATH}.",
    )
    parser.add_argument(
        "--minified",
        action="store_true",
        help="Write compact JSON (no indent). Use for very large dumps.",
    )

    args = parser.parse_args(argv)
    result = export_database_to_json(
        args.output,
        include_stir_pii=args.include_stir_pii,
        db_path=args.db_path,
        pretty=not args.minified,
    )
    total = sum(result.table_row_counts.values())
    print(
        f"JSON export · {result.path} "
        f"({total} rows across {len(result.table_row_counts)} tables, "
        f"{len(result.redactions)} redactions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
