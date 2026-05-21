"""Seed the v1 content taxonomy.

§10.2 explicitly stores ``pillar`` / ``audience`` / ``cta`` as ``TEXT``
columns rather than rigid SQL enums "so v2 is a config change, not a
migration". There is therefore no reference table to populate in Phase 1.

The v1 taxonomy values — 3 pillars × 2 audiences × 2 CTAs = 12 cells per
§10.2 / §15.3 — are surfaced by the UI dropdowns (Phase 2+) sourced from
``config/content_pillars.yaml`` (also Phase 2+). This script is a no-op
stub kept in the seed pipeline so the Phase 1 orchestrator can call it
unconditionally and the surface stays stable when Phase 2 wires in the
real dropdown source.
"""

from __future__ import annotations

import sqlite3

V1_PILLARS: tuple[str, ...] = ("stir", "build", "self")
V1_AUDIENCES: tuple[str, ...] = ("icp", "other")
V1_CTAS: tuple[str, ...] = ("ask", "none")


def seed_taxonomy(_conn: sqlite3.Connection) -> int:
    """No-op for Phase 1. Returns 0 (rows seeded)."""
    return 0
