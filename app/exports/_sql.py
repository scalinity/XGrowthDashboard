"""Shared SQL helpers for the export module.

Hoisted out of ``csv_exporter`` after /review-2 corroborated by both
agents identified that ``json_exporter`` had its own (broken) version
that used ``json.dumps`` to quote SQL identifiers — JSON-escape rules
are not the same as SQLite-identifier-escape rules. One helper, one
place to forget the rule.
"""

from __future__ import annotations


def quote_identifier(name: str) -> str:
    """Return ``name`` wrapped in SQLite double-quotes with embedded
    ``"`` doubled per the standard SQL escape rule.

    See https://sqlite.org/lang_keywords.html — a quoted identifier may
    contain any character; embedded double-quotes are escaped by
    doubling them (``"foo""bar"`` is the identifier ``foo"bar``).

    Used by every exporter that builds a ``SELECT`` or ``PRAGMA`` literal
    against allowlist-derived column or table names. The allowlist
    values are repo-internal at MVP, but quoting future-proofs against
    a column whose name collides with a SQLite keyword (``select``,
    ``order``, etc.).
    """
    return '"' + name.replace('"', '""') + '"'
