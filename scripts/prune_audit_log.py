"""Prune ``audit_logs`` rows past the retention window (§28.30).

Usage:
    uv run python -m scripts.prune_audit_log [--db-path PATH] [--retention-days N]

The retention window defaults to the ``audit_log_retention_days`` setting
(default 365). The prune itself audit-logs an ``admin/audit_logs_pruned``
row with the pruned count, so the deletion is visible in the same table
it just trimmed. Set retention to 0 to disable pruning entirely.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project root importable when invoked as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import audit_log  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune audit_logs past retention.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Path to the SQLite file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Override the audit_log_retention_days setting for this run only.",
    )
    args = parser.parse_args()

    target = Path(args.db_path) if args.db_path is not None else DEFAULT_DB_PATH
    conn = connect(target)
    try:
        pruned = audit_log.prune(conn, retention_days=args.retention_days)
    finally:
        conn.close()
    print(f"Pruned {pruned} audit_logs row(s) from {target}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
