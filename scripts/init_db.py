"""Initialize the dashboard SQLite database.

Usage:
    uv run python -m scripts.init_db [--db-path PATH]

Idempotent: applies any unrun migrations from ``migrations/`` and seeds
settings + taxonomy + milestones with ``INSERT OR IGNORE`` semantics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path when invoked as `python -m scripts.init_db`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import DEFAULT_DB_PATH, apply_migrations, connect  # noqa: E402
from scripts.seed_milestones import seed_milestones  # noqa: E402
from scripts.seed_settings import seed_settings  # noqa: E402
from scripts.seed_taxonomy import seed_taxonomy  # noqa: E402


def init_db(db_path: Path | str | None = None) -> dict[str, int | list[str]]:
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = connect(target)
    try:
        applied = apply_migrations(conn)
        settings_inserted = seed_settings(conn)
        taxonomy_inserted = seed_taxonomy(conn)
        milestones_inserted = seed_milestones(conn)
    finally:
        conn.close()
    return {
        "db_path": str(target),
        "migrations_applied": applied,
        "settings_inserted": settings_inserted,
        "taxonomy_inserted": taxonomy_inserted,
        "milestones_inserted": milestones_inserted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the X Growth Dashboard DB.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Path to the SQLite file (default: {DEFAULT_DB_PATH}).",
    )
    args = parser.parse_args()
    result = init_db(args.db_path)
    print(
        "Initialized {db_path}\n"
        "  migrations applied: {migrations}\n"
        "  settings inserted: {settings}\n"
        "  taxonomy inserted: {taxonomy}\n"
        "  milestones inserted: {milestones}".format(
            db_path=result["db_path"],
            migrations=", ".join(result["migrations_applied"]) or "(none — already current)",
            settings=result["settings_inserted"],
            taxonomy=result["taxonomy_inserted"],
            milestones=result["milestones_inserted"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
