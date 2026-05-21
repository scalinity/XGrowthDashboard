"""Streamlit entry point — see CLAUDE.md "Directory conventions".

Responsibilities, strictly:

1. ``st.set_page_config`` once.
2. Initialize the project's required ``st.session_state`` keys once.
3. Provide a landing screen pointing into ``app/pages/``.

This file does **not** declare views. Streamlit auto-discovers files in
``app/pages/`` and renders them as pages in the sidebar nav. The pages
themselves own all routing, render, and ``st.session_state`` semantics.

Per the Streamlit side-effects rule in CLAUDE.md, the DB bootstrap below is
gated with ``if "db_initialized" not in st.session_state`` so it runs exactly
once per session — never on every rerun.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/main.py` adds the script's directory (`app/`) to sys.path
# but NOT the project root, so `from app.db ...` fails without this shim.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.db import DEFAULT_DB_PATH, apply_migrations, connect

st.set_page_config(page_title="X Growth Dashboard", layout="wide")


def _bootstrap_session_state() -> None:
    if "db_initialized" not in st.session_state:
        conn = connect(DEFAULT_DB_PATH)
        apply_migrations(conn)
        conn.close()
        st.session_state.db_initialized = True
    st.session_state.setdefault("preselected_classify_post_id", None)
    st.session_state.setdefault("manual_entry_active_tab", None)


_bootstrap_session_state()

st.title("X Growth Dashboard")
st.write(
    "Use the sidebar to navigate. Phase 2 adds manual entry forms; analytical "
    "views land in Phase 3."
)
st.caption(
    "Single-user, local-only tool (see `CLAUDE.md`). All data lives in "
    f"`{DEFAULT_DB_PATH}`."
)
