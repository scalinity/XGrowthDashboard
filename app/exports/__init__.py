"""Phase 5 export module — see spec.md §16 and project CLAUDE.md.

Three exporters, one allowlist:

- :mod:`app.exports.allowlists` — single source of truth for which columns
  are exportable per table, plus opt-in and explicitly-excluded carve-outs.
  Phase 5.5 (Growth Agent) and Phase 5.6 (Reply Target Discovery) extend this
  module by appending to ``opt_in_columns`` / ``excluded_columns`` of the
  ``posts`` allowlist — a single-line edit in one place, by design.
- :mod:`app.exports.csv_exporter` — per-table CSV writer driven by the
  allowlist. Records every run in the ``data_exports`` audit table.
- :mod:`app.exports.markdown_weekly` — §16 / §24 Markdown weekly report. Gated
  by the counterfactual note (§14.6) at the export layer, independent of the
  ``counterfactual_required`` settings toggle.
- :mod:`app.exports.json_exporter` — raw JSON archive with redaction of
  secret-like columns and ``raw_api_responses`` auth headers (§18).

The package re-exports lightweight allowlist types/helpers eagerly because
they're used by every caller (including the audit-table schema tests). The
exporter functions and result dataclasses are re-exported via __getattr__
lazy access: this avoids the Python 3.14 ``runpy`` warning that fires when
``python -m app.exports.csv_exporter`` loads ``app/exports/__init__.py``
first and finds the same submodule already in ``sys.modules``.

Two import idioms are equally valid:

    from app.exports import export_table_to_csv               # lazy via __getattr__
    from app.exports.csv_exporter import export_table_to_csv  # direct

The direct form is preferred inside this repo (it makes `grep` for callers
trivial). The lazy form exists so external consumers and the test module
don't have to know the file layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.exports.allowlists import (
    ALLOWLISTS,
    TableAllowlist,
    UnknownTableError,
    columns_for_export,
    excluded_columns,
    opt_in_columns,
)

if TYPE_CHECKING:
    # Type-checking-only imports so editors still resolve the names without
    # forcing eager import at runtime.
    from app.exports.csv_exporter import (
        CsvExportResult,
        export_table_to_csv,
    )
    from app.exports.json_exporter import (
        JsonExportResult,
        export_database_to_json,
    )
    from app.exports.markdown_weekly import (
        CounterfactualMissingError,
        MarkdownWeeklyExportResult,
        export_weekly_report,
    )

__all__ = [
    # Eager (allowlist surface).
    "ALLOWLISTS",
    "TableAllowlist",
    "UnknownTableError",
    "columns_for_export",
    "excluded_columns",
    "opt_in_columns",
    # Lazy (exporter functions + result dataclasses + errors).
    "CsvExportResult",
    "JsonExportResult",
    "MarkdownWeeklyExportResult",
    "CounterfactualMissingError",
    "export_table_to_csv",
    "export_database_to_json",
    "export_weekly_report",
]


_LAZY_SUBMODULES: dict[str, str] = {
    "CsvExportResult": "app.exports.csv_exporter",
    "export_table_to_csv": "app.exports.csv_exporter",
    "JsonExportResult": "app.exports.json_exporter",
    "export_database_to_json": "app.exports.json_exporter",
    "MarkdownWeeklyExportResult": "app.exports.markdown_weekly",
    "CounterfactualMissingError": "app.exports.markdown_weekly",
    "export_weekly_report": "app.exports.markdown_weekly",
}


def __getattr__(name: str) -> Any:  # noqa: D401
    """Lazy-import exporter functions to avoid the 3.14 runpy warning."""
    module_name = _LAZY_SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module 'app.exports' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent lookups
    return value
