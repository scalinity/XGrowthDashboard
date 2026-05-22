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


def _assert_publish_tools_unreachable() -> None:
    """Startup invariant (§28.2 rule #10, §28.4 internal-only tool surface).

    The publish tools must not leak into the agent's tool registry. If a
    future refactor accidentally adds 'publish_post_to_x' / 'publish_reply_to_x'
    to AGENT_TOOLS, this assertion stops the app before the model can be
    given a tool catalog that contains them.
    """
    from app.agent._internal_tools import INTERNAL_TOOLS
    from app.agent.tools import AGENT_TOOLS

    agent_names = {t.name for t in AGENT_TOOLS}
    internal_names = {t.name for t in INTERNAL_TOOLS}
    leaked = agent_names & internal_names
    assert not leaked, (
        "INVARIANT VIOLATION (§28.2 rule #10): publish tools leaked into "
        f"the agent's tool registry: {sorted(leaked)}. The publish path is "
        "click-handler-only by construction; see app/agent/_internal_tools.py."
    )


def _assert_personality_lore_unreachable() -> None:
    """Startup invariant (§28.21 Phase 5.9 access-control rule).

    No tool in AGENT_TOOLS may grant the agent write access to the
    ``personality_lore`` table. Lore is Daniel-curated; auto-extracted
    lore would warp drafts in unbounded ways.

    Same pattern as ``_assert_publish_tools_unreachable``. We scan each
    tool's name + description + JSON schema text for the bare table
    name. A read-only listing tool would still trip this assertion —
    the spec is explicit that NO tool entry references the table, even
    a hypothetical read-only one.
    """
    import json as _json
    from app.agent.tools import AGENT_TOOLS

    needle = "personality_lore"
    offenders: list[str] = []
    for tool in AGENT_TOOLS:
        haystack = tool.name + " " + tool.description + " " + _json.dumps(tool.input_schema)
        if needle in haystack:
            offenders.append(tool.name)
    assert not offenders, (
        "INVARIANT VIOLATION (§28.21): personality_lore must NOT be "
        f"referenced by any AGENT_TOOLS entry. Offending tools: "
        f"{sorted(offenders)}. Daniel is the only writer; "
        "auto-extracted lore would warp drafts."
    )


def _bootstrap_session_state() -> None:
    if "db_initialized" not in st.session_state:
        conn = connect(DEFAULT_DB_PATH)
        apply_migrations(conn)
        conn.close()
        _assert_publish_tools_unreachable()
        _assert_personality_lore_unreachable()
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
