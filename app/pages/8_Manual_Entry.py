"""Manual Entry — Phase 2 hub for every §15 form.

Tabbed surface: every form lives here in Phase 2. Phase 3 will surface
context-aware launchers from Today / Next Rep / Weekly Review pages, but
the hub stays as the canonical "I just want to type some data" landing.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.forms import classify, correction, daily_reps, post_log, queues, snapshot, stir_event, stir_tester
from app.pages import open_connection

st.title("Manual entry")
st.caption(
    "Spec §15.1–§15.4. Every form here writes directly to the SQLite store. "
    "Phase 3 adds context-aware launchers from Today / Next Rep / Weekly."
)

conn = open_connection()

TAB_LABELS = [
    "Snapshot",
    "Correction",
    "Post / Reply",
    "Classify",
    "Daily reps",
    "Stir event",
    "Tester",
    "Needs tagging",
    "Needs post ID",
]

# Streamlit `st.tabs` doesn't expose an "active tab" API; the active-tab
# session-state key set by the queue handoff is honored by visually flagging
# the right tab in the caption above the tabs. The user still clicks; this
# keeps the UI honest about not auto-jumping.
active_hint = st.session_state.get("manual_entry_active_tab")
if active_hint:
    st.info(f"👉 Continue in the **{active_hint}** tab below.")

tabs = st.tabs(TAB_LABELS)

with tabs[0]:
    snapshot.render(conn)
with tabs[1]:
    correction.render(conn)
with tabs[2]:
    post_log.render(conn)
with tabs[3]:
    classify.render(
        conn,
        preselected_post_id=st.session_state.get("preselected_classify_post_id"),
    )
with tabs[4]:
    daily_reps.render(conn)
with tabs[5]:
    stir_event.render(conn)
with tabs[6]:
    stir_tester.render(conn)
with tabs[7]:
    queues.render_needs_tagging(conn)
with tabs[8]:
    queues.render_needs_post_id(conn)
