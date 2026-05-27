"""FastAPI dependencies for the sidecar."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

ConnFactory = Callable[[], sqlite3.Connection]


def make_get_conn(factory: ConnFactory):
    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    return get_conn
