"""FastAPI application factory for the sidecar (§31.3).

``create_app`` builds an app with:

- a per-request SQLite connection (via the project's ``app.db.connect``),
- per-launch bearer-token auth on every non-health route,
- the §28 startup invariants run once at boot (same guarantees as ``streamlit run``).

Endpoints are added incrementally through Phase 11.0. This module owns the
HTTP shape only; all reads/writes delegate to existing backend code.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, FastAPI

from app.agent import invariants
from app.db import DEFAULT_DB_PATH, apply_migrations, connect
from app.service.security import BearerTokenAuth

ConnFactory = Callable[[], sqlite3.Connection]

SERVICE_NAME = "x-growth-dashboard-service"
SERVICE_VERSION = "0.1.0"


def _default_conn_factory() -> sqlite3.Connection:
    """Open the real DB and ensure migrations are applied (sidecar default)."""
    conn = connect(DEFAULT_DB_PATH)
    apply_migrations(conn)
    return conn


def create_app(
    *,
    token: str,
    conn_factory: ConnFactory | None = None,
    run_invariants: bool = True,
) -> FastAPI:
    """Build the sidecar FastAPI app.

    Parameters
    ----------
    token
        The per-launch bearer token required on every protected route.
    conn_factory
        Returns a fresh ``sqlite3.Connection`` per request. Defaults to the
        real DB; tests inject a tmp-DB factory.
    run_invariants
        Run the §28 startup invariants at app creation. Default True.
    """
    factory = conn_factory or _default_conn_factory
    if run_invariants:
        invariants.run_all()

    app = FastAPI(title="X Growth Dashboard — local service", version=SERVICE_VERSION)
    auth = BearerTokenAuth(token)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Liveness probe for the Tauri shell's sidecar handshake. Unauthenticated."""
        return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    @app.get("/views/today", dependencies=[Depends(auth)])
    def view_today(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """§14.1 Today slice — mirrors the Streamlit page's primary reads.

        Same canonical views the page and the agent's ``query_dashboard_state``
        tool use (``v_daily_reps`` latest + last-7 ``v_account_daily``).
        """
        daily_reps = conn.execute(
            "SELECT * FROM v_daily_reps ORDER BY activity_date DESC LIMIT 1"
        ).fetchall()
        account_last_7 = conn.execute(
            "SELECT * FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 7"
        ).fetchall()
        return {
            "slice": "today",
            "daily_reps": [dict(r) for r in daily_reps],
            "account_last_7": [dict(r) for r in account_last_7],
        }

    return app
